"""Silver build: units with SCD2 history, chunks with context headers, cross references."""
from pathlib import Path

import pytest

from careplatform import lakehouse
from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.ingestion import fetch_to_landing, register_landing
from careplatform.silver import build_silver
from fakes import FakeBCLaws

SAMPLE = (Path(__file__).parent / "fixtures" / "bc_regulation_sample.xml").read_bytes()
TINY = b"""<?xml version="1.0"?><act xmlns="http://www.gov.bc.ca/2013/bclegislation"><content>
<section><marginalnote>Purpose</marginalnote><num>1</num><text>This Act sets out rules for care.</text></section>
</content></act>"""


@pytest.fixture
def bc(lakehouse_tmp):
    """Bronze with the sample as 96_2009 and tiny docs for the other two."""
    fake = FakeBCLaws({"02075_01": TINY, "96_2009": SAMPLE, "189_2019": TINY.replace(b"Act", b"Regulation")})

    def load():
        conn = BCLawsConnector(run_id="x", session=fake, sleep=lambda s: None)
        fetch_to_landing.fetch(lakehouse.landing_root() / "incoming", connector=conn)
        register_landing.run()
    load()
    return fake, load


def test_first_build(bc):
    s = build_silver.run()
    assert s["result"] == "succeeded" and s["stats"]["documents_parsed"] == 3

    units = lakehouse.read("silver.document_units")
    chunks = lakehouse.read("silver.chunks")
    assert units["is_current"].all()
    rep = units[units["unit_ref"] == "s. 30-31"].iloc[0]
    assert rep["is_repealed"] and rep["unit_id"] not in set(chunks["unit_id"])     # never chunked

    c13 = chunks[chunks["unit_ref"] == "s. 13"].iloc[0]
    assert c13["text"].startswith("[BC | Residential Care Regulation")
    assert "s. 13 Fire drills]" in c13["context_header"]

    sch = chunks[chunks["unit_ref"] == "Sch. A, s. 1"].iloc[0]
    assert sch["context_header"].count("Sch. A") == 1                     # not repeated

    refs = lakehouse.read("silver.cross_references")
    assert refs.iloc[0]["target_doc_id"] == "02075_01"


def test_rerun_with_same_documents_changes_nothing(bc):
    build_silver.run()
    before = lakehouse.read("silver.document_units")
    s = build_silver.run()
    assert s["stats"] == {"documents_up_to_date": 3}
    assert len(lakehouse.read("silver.document_units")) == len(before)


def test_amended_section_is_versioned_and_only_new_text_is_chunked(bc):
    fake, load = bc
    build_silver.run()
    fake.docs["96_2009"] = SAMPLE.replace(b"at least once a month", b"at least every two weeks")
    load()
    s = build_silver.run()
    assert s["stats"]["changed"] == 1 and s["stats"]["unchanged"] >= 5

    units = lakehouse.read("silver.document_units")
    s13 = units[units["unit_ref"] == "s. 13"]
    assert len(s13) == 2
    old = s13[~s13["is_current"]].iloc[0]
    assert "once a month" in old["text"] and old["valid_to"] is not None

    assert s["chunks"] == {"inserted": 1, "retired": 1, "unchanged": s["chunks"]["unchanged"]}
    chunks = lakehouse.read("silver.chunks")
    s13 = chunks[chunks["unit_ref"] == "s. 13"]
    live, retired = s13[s13["is_active"]], s13[~s13["is_active"]]
    assert len(live) == 1 and "every two weeks" in live.iloc[0]["text"]          # only new law is searchable
    assert len(retired) == 1 and "once a month" in retired.iloc[0]["text"]        # old kept as history
    assert retired.iloc[0]["retired_at"] is not None


def test_long_section_is_split_on_line_boundaries(bc, monkeypatch):
    real = build_silver._chunking
    monkeypatch.setattr(build_silver, "_chunking",
                        lambda: {**real(), "max_tokens": 20, "target_tokens": 20, "chars_per_token": 4})
    chunks = build_silver.build_chunks(_units_after_first_build(), "x")
    s12 = chunks[chunks["unit_ref"] == "s. 12"].sort_values("chunk_index")
    assert len(s12) > 1
    assert "(part 1 of" in s12.iloc[0]["context_header"]
    body_lines = [ln for t in s12["text"] for ln in t.split("\n")[1:]]
    assert "(a) giving access to the premises, and" in body_lines      # whole lines, never cut


def test_quiet_rerun_writes_no_chunks_and_keeps_lineage(bc):
    build_silver.run()
    before = lakehouse.read("silver.chunks").set_index("chunk_id")["load_id"]
    s = build_silver.run()                     # same documents
    assert s["chunks"]["inserted"] == 0 and s["chunks"]["retired"] == 0
    after = lakehouse.read("silver.chunks").set_index("chunk_id")["load_id"]
    assert after.equals(before.loc[after.index])      # every chunk still points at the run that created it


def test_bumping_chunker_version_rebuilds_every_chunk(bc, monkeypatch):
    build_silver.run()
    n = int(lakehouse.read("silver.chunks")["is_active"].sum())
    real = build_silver._chunking
    monkeypatch.setattr(build_silver, "_chunking", lambda: {**real(), "chunker_version": "2"})
    s = build_silver.run()
    assert s["chunks"] == {"inserted": n, "retired": n, "unchanged": 0}
    chunks = lakehouse.read("silver.chunks")
    assert set(chunks.loc[chunks["is_active"], "chunker_version"]) == {"2"}


def _units_after_first_build():
    build_silver.run()
    return lakehouse.read("silver.document_units")
