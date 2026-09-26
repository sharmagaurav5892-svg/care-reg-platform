"""End to end bronze ingestion against the fake GitHub API (Ontario source)."""
import pandas as pd
import pytest

from careplatform import lakehouse
from careplatform.connectors.github import GitHubConnector
from careplatform.dq.engine import run_checks
from careplatform.ingestion import bronze_ingest, watermarks
from careplatform.runlog import PipelineFailed
from fakes import FakeGitHub

RHA = b"%PDF-1.4 Retirement Homes Act text"
OREG = b"%PDF-1.4 O. Reg. 166/11 text"
FILES = {
    "regulations/on_rha_2010/rha_2010.pdf": RHA,
    "regulations/on_oreg_166_11/oreg_166_11.pdf": OREG,
    "regulations/on_oreg_166_11/README.md": b"notes",                          # housekeeping
    "regulations/on_ltc_inspection_reports/report_001.pdf": b"%PDF name",     # source not approved
    "regulations/random_folder/thing.pdf": b"%PDF ?",                          # unregistered path
    "regulations/on_fltca_2021/fltca.docx": b"docx bytes",                     # extension not allowed
    "other/outside_root.pdf": b"%PDF outside",                                 # outside root_path
}


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.commit(FILES)
    conn = GitHubConnector("gaurav", "care-reg-source-docs", "main", run_id="x",
                           token="TOKEN123", session=fake, sleep=lambda s: None)
    return fake, conn


def test_first_load(lakehouse_tmp, gh):
    fake, conn = gh
    s = bronze_ingest.run(connector=conn)

    assert s["result"] == "succeeded"
    assert s["loaded"] == 2
    assert s["skipped"] == {"housekeeping": 1, "source_not_approved": 1,
                            "unregistered_path": 1, "extension_not_allowed": 1}

    bronze = lakehouse.read("bronze.raw_documents")
    assert set(bronze["source_id"]) == {"on_rha_2010", "on_oreg_166_11"}
    # landing files exist and lineage columns are filled
    for _, row in bronze.iterrows():
        assert (lakehouse.landing_root() / row["landing_path"]).read_bytes() in (RHA, OREG)
        assert row["source_version"] == fake.head
        assert row["remote_hash"] and row["source_system"] == "github"
        assert row["source_url"].startswith("https://github.com/gaurav/care-reg-source-docs/blob/")

    run = lakehouse.read("ops.run_log").iloc[0]
    assert run["status"] == "SUCCEEDED" and run["rows_out"] == 2
    assert run["pipeline"] == "bronze_ingest:github"
    assert set(bronze["load_id"]) == {run["run_id"]}          # every row points to its run

    assert watermarks.get("github", conn.scope) == fake.head
    dq = lakehouse.read("ops.dq_results")
    assert set(dq["status"]) == {"pass"}


def test_unapproved_source_is_never_downloaded(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    blob_calls = [u for u, _ in fake.requests if "/git/blobs/" in u]
    assert len(blob_calls) == 2                               # only the two approved files


def test_rerun_with_no_new_commit_costs_one_call(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    before = len(fake.requests)
    s = bronze_ingest.run(connector=conn)
    assert s["result"] == "no changes at source, nothing to do"
    assert len(fake.requests) - before == 1
    assert len(lakehouse.read("bronze.raw_documents")) == 2


def test_new_commit_only_downloads_changed_files(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    amended = b"%PDF-1.4 O. Reg. 166/11 text, amended 2026"
    fake.commit({**FILES, "regulations/on_oreg_166_11/oreg_166_11.pdf": amended})
    before = len(fake.requests)

    s = bronze_ingest.run(connector=conn)
    new_calls = [u for u, _ in fake.requests[before:]]
    assert s["loaded"] == 1
    assert s["skipped"]["unchanged"] == 1
    assert sum("/git/blobs/" in u for u in new_calls) == 1   # only the amended file
    bronze = lakehouse.read("bronze.raw_documents")
    assert len(bronze) == 3                                  # both versions kept (docs/08 section 3)


def test_same_content_under_new_name_is_skipped(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    fake.commit({**FILES, "regulations/on_rha_2010/rha_copy.pdf": RHA})
    before = len(fake.requests)
    s = bronze_ingest.run(connector=conn)
    # identical bytes = identical Git blob SHA, so it is skipped before any download
    assert s["loaded"] == 0 and s["skipped"]["unchanged"] == 3
    assert not any("/git/blobs/" in u for u, _ in fake.requests[before:])


def test_full_refresh_redownloads_but_dedupes_by_content(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    s = bronze_ingest.run(connector=conn, full_refresh=True)
    assert s["loaded"] == 0 and s["skipped"]["duplicate_content"] == 2
    assert len(lakehouse.read("bronze.raw_documents")) == 2


def test_dq_failure_blocks_load_and_keeps_watermark(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    first_head = fake.head
    # someone edits a landing file by hand: bronze can no longer be trusted
    row = lakehouse.read("bronze.raw_documents").iloc[0]
    (lakehouse.landing_root() / row["landing_path"]).write_bytes(b"tampered")
    fake.commit({**FILES, "regulations/on_rha_2010/new_schedule.pdf": b"%PDF new"})

    with pytest.raises(PipelineFailed, match="DQ-B-005"):
        bronze_ingest.run(connector=conn)

    assert len(lakehouse.read("bronze.raw_documents")) == 2          # nothing new written
    assert watermarks.get("github", conn.scope) == first_head        # watermark did not move
    runs = lakehouse.read("ops.run_log").sort_values("started_at")
    assert list(runs["status"]) == ["SUCCEEDED", "FAILED"]
    assert "DQ-B-005" in runs.iloc[-1]["error_message"]


def test_api_calls_are_logged_even_when_run_fails(lakehouse_tmp, gh):
    fake, conn = gh
    fake.queue(404, body={"message": "Not Found"})
    with pytest.raises(Exception):
        bronze_ingest.run(connector=conn)
    log = lakehouse.read("ops.api_call_log")
    assert len(log) == 1 and log.iloc[0]["status_code"] == 404
    assert lakehouse.read("ops.run_log").iloc[0]["status"] == "FAILED"


def test_transient_errors_are_retried_during_ingest(lakehouse_tmp, gh):
    fake, conn = gh
    fake.queue(503)
    fake.queue(429, headers={"Retry-After": "1"})
    s = bronze_ingest.run(connector=conn)
    assert s["result"] == "succeeded"
    log = lakehouse.read("ops.api_call_log")
    assert sorted(log["attempt"].tolist())[-1] == 3


def test_token_is_sent_but_never_stored(lakehouse_tmp, gh):
    fake, conn = gh
    bronze_ingest.run(connector=conn)
    assert fake.requests[0][1]["Authorization"] == "Bearer TOKEN123"
    log = lakehouse.read("ops.api_call_log")
    assert "TOKEN123" not in log.to_csv()


def test_dq_gate_catches_unapproved_source(lakehouse_tmp):
    """Backstop: even if the plan step had a bug, DQ-B-004 stops unapproved data."""
    df = pd.DataFrame([{
        "doc_id": "a" * 64, "source_id": "on_ltc_inspection_reports", "source_system": "github",
        "file_name": "x.pdf", "source_ref": "regulations/x.pdf", "landing_path": "nowhere",
        "source_url": "u", "source_version": "c", "remote_hash": "b", "doc_type": "inspection_report",
        "jurisdiction": "ON", "mime_type": "application/pdf", "file_size_bytes": 10,
        "ingested_at": pd.Timestamp.now(tz="UTC"), "load_id": "r",
    }])
    report = run_checks("bronze.raw_documents", df, run_id="r", write=False)
    assert {x["rule_id"] for x in report.critical_failures} >= {"DQ-B-004", "DQ-B-005"}


def test_schema_comes_from_catalog(lakehouse_tmp):
    with pytest.raises(ValueError, match="not in catalog"):
        lakehouse.append("ops.watermarks", pd.DataFrame([{
            "source_system": "g", "scope": "s", "watermark_value": "v",
            "updated_at": pd.Timestamp.now(tz="UTC"), "load_id": "r", "surprise": 1}]))
