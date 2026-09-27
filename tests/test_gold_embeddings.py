"""Gold embeddings: only new chunks are embedded, retired ones are retired, every call is audited."""
from pathlib import Path

import pytest

from careplatform import lakehouse
from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.gold import embed_chunks
from careplatform.ingestion import fetch_to_landing, register_landing
from careplatform.runlog import PipelineFailed
from careplatform.silver import build_silver
from fakes import FakeBCLaws, make_response

SAMPLE = (Path(__file__).parent / "fixtures" / "bc_regulation_sample.xml").read_bytes()
TINY = b"""<?xml version="1.0"?><act xmlns="http://www.gov.bc.ca/2013/bclegislation"><content>
<section><marginalnote>Purpose</marginalnote><num>1</num><text>This Act sets out rules for care.</text></section>
</content></act>"""
DIM = 1024


class FakeServing:
    """Fake Databricks model serving: POST .../invocations -> one vector per input text."""

    def __init__(self, dim=DIM):
        self.dim, self.calls, self.queued = dim, [], []

    def post(self, url, headers=None, params=None, timeout=None, json=None):
        self.calls.append(json["input"])
        if self.queued:
            return self.queued.pop(0)
        data = [{"index": i, "embedding": [float(len(t) % 7)] * self.dim} for i, t in enumerate(json["input"])]
        return make_response(200, {"data": data})


@pytest.fixture
def world(lakehouse_tmp):
    """Bronze + silver built from the sample, and a fake embedding endpoint."""
    bc = FakeBCLaws({"02075_01": TINY, "96_2009": SAMPLE, "189_2019": TINY.replace(b"Act", b"Regulation")})

    def load_and_build():
        conn = BCLawsConnector(run_id="x", session=bc, sleep=lambda s: None)
        fetch_to_landing.fetch(lakehouse.landing_root() / "incoming", connector=conn)
        register_landing.run()
        build_silver.run()

    load_and_build()
    serving = FakeServing()
    pauses = []

    def embedder():
        return embed_chunks.DatabricksEmbedder(run_id="x", host="https://ws.example", headers={"Authorization": "Bearer t"},
                                               session=serving, sleep=pauses.append)
    return bc, load_and_build, serving, pauses, embedder


def active_chunks():
    c = lakehouse.read("silver.chunks")
    return c[c["is_active"]]


def test_first_run_embeds_every_active_chunk_in_batches(world):
    _, _, serving, pauses, embedder = world
    n = len(active_chunks())
    s = embed_chunks.run(embedder())
    assert s["result"] == "succeeded" and s["embedded"] == n and s["retired"] == 0

    assert all(len(batch) <= 8 for batch in serving.calls)                 # batch_size from settings
    assert len(serving.calls) == -(-n // 8)                                # ceil(n / 8) calls
    assert pauses.count(1.0) == len(serving.calls) - 1                     # paced between calls

    emb = lakehouse.read("gold.chunk_embeddings")
    assert set(emb["embedding_dim"]) == {DIM} and emb["is_active"].all()
    log = lakehouse.read("ops.api_call_log")
    log = log[log["connector"] == "databricks_fm"]                         # the fetch's BC Laws calls are in here too
    assert len(log) == len(serving.calls) and set(log["method"]) == {"POST"}
    assert not any("Bearer" in str(v) for v in log.astype(str).values.ravel())   # token never logged


def test_rerun_makes_no_api_calls(world):
    *_, serving, _, embedder = world
    embed_chunks.run(embedder())
    before = len(serving.calls)
    s = embed_chunks.run(embedder())
    assert s["embedded"] == 0 and s["retired"] == 0 and len(serving.calls) == before


def test_amended_section_embeds_one_and_retires_one(world):
    bc, load_and_build, serving, _, embedder = world
    embed_chunks.run(embedder())
    bc.docs["96_2009"] = SAMPLE.replace(b"at least once a month", b"at least every two weeks")
    load_and_build()
    before = len(serving.calls)
    s = embed_chunks.run(embedder())
    assert s["embedded"] == 1 and s["retired"] == 1
    assert serving.calls[before:] == [[t for t in active_chunks()["text"] if "every two weeks" in t]]


def test_rate_limit_is_retried_and_logged(world):
    *_, serving, pauses, embedder = world
    serving.queued = [make_response(429, {"error_code": "REQUEST_LIMIT_EXCEEDED"})]
    s = embed_chunks.run(embedder())
    assert s["result"] == "succeeded"
    log = lakehouse.read("ops.api_call_log")
    log = log[log["connector"] == "databricks_fm"]
    assert (log["status_code"] == 429).sum() == 1 and (log["attempt"] == 2).sum() == 1


def test_wrong_vector_length_writes_nothing(world):
    *_, serving, _, embedder = world
    serving.dim = 768                                                    # a different model's size
    with pytest.raises(PipelineFailed, match="DQ-G-002"):
        embed_chunks.run(embedder())
    assert lakehouse.read("gold.chunk_embeddings").empty
