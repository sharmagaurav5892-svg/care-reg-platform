"""The runner-side fetch: files plus a manifest, no state, nothing on failure."""
import hashlib
import json

import pytest

from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.ingestion import fetch_to_landing, watermarks
from fakes import FakeBCLaws

DOCS = {
    "02075_01": b"<act>Community Care and Assisted Living Act</act>",
    "96_2009": b"<reg>Residential Care Regulation</reg>",
    "189_2019": b"<reg>Assisted Living Regulation</reg>",
}


@pytest.fixture
def bc():
    fake = FakeBCLaws(DOCS)
    return fake, BCLawsConnector(run_id="x", session=fake, sleep=lambda s: None)


def test_writes_every_document_and_a_matching_manifest(tmp_path, bc):
    _, conn = bc
    batch = fetch_to_landing.fetch(tmp_path, connector=conn)
    m = json.loads((batch / "_manifest.json").read_text())

    assert m["manifest_version"] == 1 and m["connector"] == "bclaws"
    assert batch.name == m["load_id"]
    assert {f["source_ref"] for f in m["files"]} == set(DOCS)
    for f in m["files"]:
        data = (batch / f["file_name"]).read_bytes()
        assert f["sha256"] == hashlib.sha256(data).hexdigest()
        assert f["size_bytes"] == len(data)


def test_never_reads_state(tmp_path, bc, monkeypatch):
    """The runner has no lakehouse. Touching watermarks there would crash."""
    def boom(*a, **k):
        raise AssertionError("fetch_to_landing must not read watermarks")
    monkeypatch.setattr(watermarks, "get", boom)
    fake, conn = bc
    fetch_to_landing.fetch(tmp_path, connector=conn)
    assert not any("If-None-Match" in h for _, h in fake.requests)


def test_manifest_keeps_the_api_audit_trail(tmp_path, bc):
    fake, conn = bc
    fake.html_only.add("96_2009")          # /xml 404, then the HTML fallback
    batch = fetch_to_landing.fetch(tmp_path, connector=conn)
    m = json.loads((batch / "_manifest.json").read_text())

    assert len(m["api_calls"]) == 4
    assert sorted(c["status_code"] for c in m["api_calls"]) == [200, 200, 200, 404]
    assert all(c["run_id"] == m["load_id"] for c in m["api_calls"])


def test_api_failure_leaves_no_manifest(tmp_path, bc):
    fake, conn = bc
    del fake.docs["189_2019"]               # 404 on both /xml and HTML
    with pytest.raises(Exception):
        fetch_to_landing.fetch(tmp_path, connector=conn)
    assert not list(tmp_path.rglob("_manifest.json"))