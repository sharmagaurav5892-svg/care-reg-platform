"""Answer pipeline: section lookup with legal status, hybrid search, not-found, citation check."""
import hashlib
import math
import re
from pathlib import Path

import pytest

from careplatform import config, lakehouse
from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.gateway.gateway import Answer
from careplatform.gold import embed_chunks
from careplatform.ingestion import fetch_to_landing, register_landing
from careplatform.rag import answer as rag
from careplatform.rag.retrieve import Index
from careplatform.rag.router import find_section
from careplatform.silver import build_silver
from fakes import FakeBCLaws, make_response

SAMPLE = (Path(__file__).parent / "fixtures" / "bc_regulation_sample.xml").read_bytes()
TINY = b"""<?xml version="1.0"?><act xmlns="http://www.gov.bc.ca/2013/bclegislation"><content>
<section><marginalnote>Purpose</marginalnote><num>1</num><text>This Act sets out rules for care.</text></section>
</content></act>"""
DIM = 1024


def bag_of_words(text: str) -> list[float]:
    """A stand-in embedding: each word switches on one slot. Shared words = similar vectors."""
    v = [0.0] * DIM
    for w in re.findall(r"[a-z]+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class WordServing:
    """Fake embedding endpoint that returns bag-of-words vectors."""
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, params=None, timeout=None, json=None):
        self.calls.append(json["input"])
        return make_response(200, {"data": [{"index": i, "embedding": bag_of_words(t)}
                                            for i, t in enumerate(json["input"])]})


class FakeGateway:
    """Records what would be sent to a model and replies with a scripted answer."""
    def __init__(self, reply="A licensee must hold a fire drill at least once a month [1]."):
        self.reply, self.calls = reply, []

    def chat(self, purpose, messages, prompt_version, data_classification="confidential", max_tokens=None):
        self.calls.append({"purpose": purpose, "messages": messages, "prompt_version": prompt_version,
                           "data_classification": data_classification})
        return Answer(self.reply, "fake-model", "fake", 10, 5, 1, False, "call-1")


@pytest.fixture
def world(lakehouse_tmp, monkeypatch):
    # bag-of-words vectors score lower than the real model's; the real threshold is calibrated
    # on real scores (settings.yaml), so the tests use a threshold that fits the fake vectors
    monkeypatch.setitem(config.settings()["rag"], "min_vector_score", 0.2)
    bc = FakeBCLaws({"02075_01": TINY, "96_2009": SAMPLE, "189_2019": TINY.replace(b"Act", b"Regulation")})
    conn = BCLawsConnector(run_id="x", session=bc, sleep=lambda s: None)
    fetch_to_landing.fetch(lakehouse.landing_root() / "incoming", connector=conn)
    register_landing.run()
    build_silver.run()
    serving = WordServing()

    def embedder():
        return embed_chunks.DatabricksEmbedder(run_id="x", host="https://ws.example",
                                               headers={"Authorization": "Bearer t"}, session=serving,
                                               sleep=lambda s: None)
    embed_chunks.run(embedder())
    return Index.from_lakehouse(), embedder()


def test_router_reads_section_and_law():
    assert find_section("What does section 13 of the Residential Care Regulation say?").number == "13"
    assert find_section("what does s. 12 say").source_id is None
    assert find_section("Is s.12 of the Act in force?").source_id == "bc_ccala_2002"
    assert find_section("How often are fire drills?") is None
    assert find_section("s. 5 of the Assisted Living Regulation").source_id == "bc_reg_189_2019"
    assert find_section("s. 5 of the Community Care and Assisted Living Act").source_id == "bc_ccala_2002"
    assert find_section("section 26 of 96/2009").source_id == "bc_reg_96_2009"


def test_configured_prompt_file_exists():
    """The prompt named in settings.yaml must exist, or every question would fail at run time."""
    version = config.settings()["rag"]["prompt_version"]
    assert "NOT_FOUND" in rag.load_prompt(version) and "[1]" in rag.load_prompt(version)


def test_search_only_sees_live_law(world):
    index, _ = world
    refs = set(index.rows["unit_ref"])
    assert "s. 13" in refs and "s. 15" not in refs and "s. 30-31" not in refs   # not in force, repealed


def test_fire_drill_question_is_answered_with_citation(world):
    index, embedder = world
    gw = FakeGateway()
    a = rag.ask("How often must a fire drill be held?", index, gw, embedder)
    assert a.route == "search" and "[1]" in a.text
    assert a.sources[0].unit_ref == "s. 13"
    call = gw.calls[0]
    assert call["purpose"] == "rag" and call["prompt_version"] == "answer-v1"
    assert call["data_classification"] == "confidential"                     # so never Gemini
    assert "Use only the numbered sources" in call["messages"][0]["content"]
    assert "[1]" in call["messages"][1]["content"] and "once a month" in call["messages"][1]["content"]


def test_not_in_force_section_is_reported_without_a_model_call(world):
    index, embedder = world
    gw = FakeGateway()
    a = rag.ask("What does section 15 of the Residential Care Regulation say?", index, gw, embedder)
    assert a.route == "status" and "not in force" in a.text and "no legal effect" in a.text
    assert gw.calls == []


def test_repealed_section_is_reported(world):
    index, embedder = world
    gw = FakeGateway()
    for q in ("Explain section 30-31 of the Residential Care Regulation", "What does section 30 say?"):
        a = rag.ask(q, index, gw, embedder)
        assert a.route == "status" and "repealed" in a.text and "no longer applies" in a.text
    assert gw.calls == []


def test_live_section_lookup_goes_to_model_with_that_section(world):
    index, embedder = world
    gw = FakeGateway("The licensee must cooperate with an inspection [1].")
    a = rag.ask("What does section 12 of the Residential Care Regulation require?", index, gw, embedder)
    assert a.route == "lookup" and a.sources[0].unit_ref == "s. 12"
    assert "cooperate with an inspection" in gw.calls[0]["messages"][1]["content"]


def test_off_topic_question_is_not_found_without_a_model_call(world):
    index, embedder = world
    gw = FakeGateway()
    a = rag.ask("What is the speed limit on Highway 1?", index, gw, embedder)
    assert a.route == "not_found" and a.text == rag.NOT_FOUND and gw.calls == []


def test_model_saying_not_found_is_passed_on(world):
    index, embedder = world
    a = rag.ask("How often must a fire drill be held?", index, FakeGateway("NOT_FOUND"), embedder)
    assert a.route == "not_found" and a.text == rag.NOT_FOUND and a.sources == []


def test_answer_without_citation_is_never_shown(world):
    index, embedder = world
    a = rag.ask("How often must a fire drill be held?", index,
                FakeGateway("Fire drills are monthly, as far as I know."), embedder)
    assert a.route == "ungrounded" and a.text == rag.NOT_FOUND


def test_citation_numbers_outside_the_sources_are_ignored(world):
    index, embedder = world
    a = rag.ask("How often must a fire drill be held?", index,
                FakeGateway("Monthly [9]."), embedder)
    assert a.route == "ungrounded"
