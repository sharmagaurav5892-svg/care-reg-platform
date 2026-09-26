"""The Databricks-side register: landed batches -> bronze, checked and idempotent."""
import pytest

from careplatform import lakehouse
from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.ingestion import fetch_to_landing, register_landing, watermarks
from careplatform.runlog import PipelineFailed
from fakes import FakeBCLaws

DOCS = {
    "02075_01": b"<act>Community Care and Assisted Living Act</act>",
    "96_2009": b"<reg>Residential Care Regulation</reg>",
    "189_2019": b"<reg>Assisted Living Regulation</reg>",
}


@pytest.fixture
def land(lakehouse_tmp):
    """Plays the GitHub runner: fetch from the fake API straight into the landing drop zone."""
    fake = FakeBCLaws(DOCS)

    def _land():
        conn = BCLawsConnector(run_id="x", session=fake, sleep=lambda s: None)
        return fetch_to_landing.fetch(lakehouse.landing_root() / "incoming", connector=conn)
    return fake, _land


def test_first_batch_is_registered_with_lineage(land):
    _, land_batch = land
    land_batch()
    s = register_landing.run()
    assert s["result"] == "succeeded" and s["loaded"] == 3

    bronze = lakehouse.read("bronze.raw_documents")
    run = lakehouse.read("ops.run_log").iloc[0]
    assert run["pipeline"] == "bronze_register:bclaws" and run["status"] == "SUCCEEDED"
    assert set(bronze["load_id"]) == {run["run_id"]}
    assert all(p.startswith("incoming/bclaws/") for p in bronze["landing_path"])

    calls = lakehouse.read("ops.api_call_log")
    assert len(calls) == 3 and set(calls["run_id"]) == {run["run_id"]}   # runner's calls, linked to this run
    assert watermarks.get("bclaws", "landing")


def test_rerun_finds_nothing_new(land):
    _, land_batch = land
    land_batch()
    register_landing.run()
    s = register_landing.run()
    assert s["result"] == "no new batches"
    assert len(lakehouse.read("bronze.raw_documents")) == 3
    assert len(lakehouse.read("ops.api_call_log")) == 3


def test_unchanged_documents_next_day_are_skipped_but_calls_logged(land):
    _, land_batch = land
    land_batch()
    register_landing.run()
    land_batch()
    s = register_landing.run()
    assert s["loaded"] == 0 and s["skipped"] == {"duplicate_content": 3}
    assert len(lakehouse.read("ops.api_call_log")) == 6


def test_amended_law_is_loaded_and_old_version_kept(land):
    fake, land_batch = land
    land_batch()
    register_landing.run()
    fake.docs["96_2009"] = b"<reg>Residential Care Regulation, amended 2026</reg>"
    land_batch()
    s = register_landing.run()
    assert s["loaded"] == 1
    assert (lakehouse.read("bronze.raw_documents")["source_ref"] == "96_2009").sum() == 2


def test_tampered_file_stops_the_load(land):
    _, land_batch = land
    batch = land_batch()
    (batch / "96_2009.xml").write_bytes(b"<reg>edited after upload</reg>")
    with pytest.raises(PipelineFailed, match="hash does not match"):
        register_landing.run()
    assert lakehouse.read("bronze.raw_documents").empty
    assert watermarks.get("bclaws", "landing") is None
    assert lakehouse.read("ops.run_log").iloc[0]["status"] == "FAILED"


def test_batch_without_manifest_is_ignored(land):
    _, land_batch = land
    batch = land_batch()
    (batch / "_manifest.json").unlink()      # upload died before the manifest went up
    s = register_landing.run()
    assert s["result"] == "no new batches"