"""Bronze ingestion: source APIs -> landing files -> bronze.raw_documents.

Run it:
    python -m careplatform.ingestion.bronze_ingest                      # every enabled connector
    python -m careplatform.ingestion.bronze_ingest --connector bclaws   # just one
    python -m careplatform.ingestion.bronze_ingest --full-refresh

Each connector gets its OWN run in ops.run_log (bronze_ingest:github,
bronze_ingest:bclaws). If BC Laws is down, Ontario still loads, and the
failure is visible on its own. That is failure isolation.

For each connector, in order:

  1. Start a run in ops.run_log (status RUNNING).
  2. connector.fetch(): the connector talks to its API, applies the
     governance gate (unapproved sources are never downloaded) and its own
     change detection (commit watermark for GitHub, 304 Not Modified for
     BC Laws), and hands back only new or changed files.
  3. Hash each file (SHA-256 = doc_id). Same content already loaded? Skip.
  4. Save bytes to data/landing/<system>/<source_id>/<doc_id[:12]>/<file>.
  5. Run DQ rules on bronze + the new rows. Any critical failure: stop,
     write nothing to bronze, keep landing files for debugging.
  6. Append new rows to bronze, THEN save the connector's watermarks.
  7. Always (even on failure): flush API calls to ops.api_call_log and
     close the run as SUCCEEDED or FAILED.
"""
from __future__ import annotations

import argparse
import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from careplatform import config, lakehouse
from careplatform.connectors.base import SourceConnector
from careplatform.dq.engine import run_checks
from careplatform.ingestion import watermarks
from careplatform.runlog import PipelineFailed, start_run

TABLE = "bronze.raw_documents"


def build_connector(name: str, run_id: str) -> SourceConnector:
    if name == "github":
        from careplatform.connectors.github import GitHubConnector
        return GitHubConnector.from_settings(run_id)
    if name == "bclaws":
        from careplatform.connectors.bclaws import BCLawsConnector
        return BCLawsConnector.from_settings(run_id)
    raise ValueError(f"Unknown connector {name!r}")


def run(connector_name: str | None = None, full_refresh: bool = False,
        connector: SourceConnector | None = None) -> dict:
    """Load one connector. Pass `connector` in tests to use a fake API."""
    name = connector.source_system if connector else connector_name
    summary: dict = {"connector": name}
    with start_run(f"bronze_ingest:{name}") as r:
        conn = connector or build_connector(name, r.run_id)
        conn.client.run_id = r.run_id
        try:
            existing = lakehouse.read(TABLE)
            res = conn.fetch(config.sources()["sources"], existing, full_refresh)
            summary.update(res.info)

            if res.info.get("up_to_date"):
                r.rows_in, r.rows_out = 0, 0
                summary["result"] = "no changes at source, nothing to do"
                return summary

            known_docs = set(existing["doc_id"])
            landing_root = config.REPO_ROOT / config.settings()["lakehouse"]["landing_root"]
            now = datetime.now(timezone.utc)
            skipped = res.skipped
            rows = []
            for f in res.items:
                doc_id = hashlib.sha256(f.data).hexdigest()
                if doc_id in known_docs:
                    skipped["duplicate_content"] += 1
                    continue
                known_docs.add(doc_id)

                src = f.source
                dest = landing_root / name / src["source_id"] / doc_id[:12] / f.file_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.data)

                rows.append({
                    "doc_id": doc_id,
                    "source_id": src["source_id"],
                    "source_system": name,
                    "file_name": f.file_name,
                    "source_ref": f.source_ref,
                    "source_version": f.source_version,
                    "remote_hash": f.remote_hash,
                    "landing_path": Path(dest).relative_to(config.REPO_ROOT).as_posix(),
                    "source_url": f.source_url,
                    "doc_type": src["doc_type"],
                    "jurisdiction": src["jurisdiction"],
                    "mime_type": mimetypes.guess_type(f.file_name)[0] or "application/octet-stream",
                    "file_size_bytes": len(f.data),
                    "ingested_at": now,
                    "load_id": r.run_id,
                })

            new = pd.DataFrame(rows)
            candidate = pd.concat([existing, new], ignore_index=True) if rows else existing
            report = run_checks(TABLE, candidate, r.run_id)
            summary["dq"] = report.summary()
            if not report.passed:
                failed = ", ".join(x["rule_id"] for x in report.critical_failures)
                raise PipelineFailed(f"Critical DQ rules failed: {failed}. Nothing written to bronze.")

            lakehouse.append(TABLE, new)
            for scope, value in res.watermarks:           # only after data is safely written
                watermarks.set(name, scope, value, r.run_id)

            r.rows_in, r.rows_out = len(res.items) + sum(skipped.values()), len(new)
            summary.update(loaded=len(new), skipped=dict(skipped), result="succeeded")
            return summary
        finally:
            summary["api_calls"] = len(conn.client.calls)
            conn.client.flush_log()


def run_all(full_refresh: bool = False, only: str | None = None) -> list[dict]:
    """Run each enabled connector separately. One failing doesn't stop the others."""
    names = [only] if only else config.settings()["ingestion"]["enabled_connectors"]
    results = []
    for n in names:
        try:
            results.append(run(n, full_refresh=full_refresh))
        except Exception as e:
            results.append({"connector": n, "result": f"FAILED: {e}"})
    return results


def main() -> None:
    p = argparse.ArgumentParser(description="Load source documents from source APIs into bronze.")
    p.add_argument("--connector", choices=["github", "bclaws"], help="run only this connector")
    p.add_argument("--full-refresh", action="store_true",
                   help="Ignore watermarks and re-check everything (identical content is still skipped).")
    a = p.parse_args()
    results = run_all(full_refresh=a.full_refresh, only=a.connector)
    for s in results:
        print(f"\n=== bronze ingest: {s['connector']}")
        for k, v in s.items():
            if k == "dq":
                print("  dq checks:\n" + v)
            elif k != "connector":
                print(f"  {k}: {v}")
    if any(str(s.get("result", "")).startswith("FAILED") for s in results):
        print("\nAt least one connector failed. See ops.run_log, ops.dq_results and ops.api_call_log.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
