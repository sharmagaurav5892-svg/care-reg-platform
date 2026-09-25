"""End to end bronze ingestion against the fake BC Laws API (British Columbia source)."""
import json

import pytest

from careplatform import lakehouse
from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.ingestion import bronze_ingest, watermarks
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


def test_first_load_pulls_all_three_documents(lakehouse_tmp, bc):
    fake, conn = bc
    s = bronze_ingest.run(connector=conn)
    assert s["result"] == "succeeded" and s["loaded"] == 3

    bronze = lakehouse.read("bronze.raw_documents")
    assert set(bronze["source_id"]) == {"bc_ccala_2002", "bc_reg_96_2009", "bc_reg_189_2019"}
    assert set(bronze["jurisdiction"]) == {"BC"}
    assert set(bronze["source_system"]) == {"bclaws"}
    assert set(bronze["source_ref"]) == set(DOCS)                 # document ids, as strings
    assert bronze["remote_hash"].isna().all()                     # BC Laws gives no pre-download hash
    assert all(v.startswith('"') for v in bronze["source_version"])  # ETag saved as the version
    assert set(bronze["file_name"]) == {f"{d}.xml" for d in DOCS}

    run = lakehouse.read("ops.run_log").iloc[0]
    assert run["pipeline"] == "bronze_ingest:bclaws"
    wm = json.loads(watermarks.get("bclaws", "96_2009"))
    assert wm["etag"] and wm["last_modified"]


def test_no_api_key_is_sent(lakehouse_tmp, bc):
    fake, conn = bc
    bronze_ingest.run(connector=conn)
    for _, headers in fake.requests:
        assert "Authorization" not in headers
        assert headers["User-Agent"].startswith("care-reg-platform")


def test_rerun_uses_conditional_requests_and_gets_304(lakehouse_tmp, bc):
    fake, conn = bc
    bronze_ingest.run(connector=conn)
    before = len(fake.requests)
    s = bronze_ingest.run(connector=conn)

    new = fake.requests[before:]
    assert len(new) == 3 and all("If-None-Match" in h for _, h in new)
    assert s["loaded"] == 0 and s["skipped"] == {"not_modified": 3}
    log = lakehouse.read("ops.api_call_log")
    assert (log["status_code"] == 304).sum() == 3


def test_amended_regulation_is_loaded_and_old_version_kept(lakehouse_tmp, bc):
    fake, conn = bc
    bronze_ingest.run(connector=conn)
    fake.docs["96_2009"] = b"<reg>Residential Care Regulation, amended 2026</reg>"
    s = bronze_ingest.run(connector=conn)
    assert s["loaded"] == 1 and s["skipped"]["not_modified"] == 2
    bronze = lakehouse.read("bronze.raw_documents")
    assert (bronze["source_ref"] == "96_2009").sum() == 2          # both versions in bronze


def test_server_without_caching_falls_back_to_content_hash(lakehouse_tmp, bc):
    """If the server ignores ETags we still download, but identical content is not stored twice."""
    fake, conn = bc
    fake.send_validators = False
    bronze_ingest.run(connector=conn)
    s = bronze_ingest.run(connector=conn)
    assert s["loaded"] == 0 and s["skipped"]["duplicate_content"] == 3
    assert len(lakehouse.read("bronze.raw_documents")) == 3


def test_xml_missing_falls_back_to_html(lakehouse_tmp, bc):
    fake, conn = bc
    fake.html_only.add("02075_01")
    s = bronze_ingest.run(connector=conn)
    assert s["loaded"] == 3 and s["fell_back_to_html"] == ["02075_01"]
    bronze = lakehouse.read("bronze.raw_documents")
    row = bronze[bronze["source_ref"] == "02075_01"].iloc[0]
    assert row["file_name"] == "02075_01.html" and row["mime_type"] == "text/html"


def test_server_errors_are_retried(lakehouse_tmp, bc):
    fake, conn = bc
    fake.queued.append(__import__("fakes").make_response(503))
    s = bronze_ingest.run(connector=conn)
    assert s["loaded"] == 3
    log = lakehouse.read("ops.api_call_log")
    assert list(log.sort_values("called_at")["attempt"][:2]) == [1, 2]


def test_one_connector_failing_does_not_block_the_other(lakehouse_tmp, monkeypatch, bc):
    """GitHub not configured (no owner) must not stop BC from loading."""
    fake, conn = bc
    real_build = bronze_ingest.build_connector

    def build(name, run_id):
        if name == "bclaws":
            return conn
        return real_build(name, run_id)          # github: raises, owner not set

    monkeypatch.setattr(bronze_ingest, "build_connector", build)
    monkeypatch.delenv("GITHUB_OWNER", raising=False)
    results = bronze_ingest.run_all()

    by_name = {r["connector"]: r for r in results}
    assert by_name["bclaws"]["result"] == "succeeded"
    assert by_name["github"]["result"].startswith("FAILED")
    runs = lakehouse.read("ops.run_log")
    assert set(zip(runs["pipeline"], runs["status"])) == {
        ("bronze_ingest:bclaws", "SUCCEEDED"), ("bronze_ingest:github", "FAILED")}
