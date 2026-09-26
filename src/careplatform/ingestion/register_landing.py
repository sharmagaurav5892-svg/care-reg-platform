"""Register landed batches in bronze: landing volume -> bronze.raw_documents.

Second half of the landing zone pattern (ADR-008). Runs INSIDE Databricks as
a job task. A GitHub Actions runner has already fetched the documents
(fetch_to_landing.py) and uploaded each batch to:

    <landing>/incoming/<connector>/<YYYY-MM-DD>/<batch_id>/
        96_2009.xml ...
        _manifest.json        <- uploaded last, marks the batch complete

    python -m careplatform.ingestion.register_landing

For each complete batch newer than the watermark, oldest first:
  1. Check the manifest version.
  2. Recompute every file's SHA-256 and compare it with the manifest.
     A mismatch means a corrupted or edited upload: stop, write nothing.
  3. Skip content already in bronze (same hash = same document).
  4. Build bronze rows. The file stays where it landed; landing_path points at it.
Then, once for the whole run:
  5. DQ gate on bronze + new rows. Any critical failure: stop, write nothing.
  6. Append the bronze rows, and the runner's API calls to ops.api_call_log.
  7. Move the watermark to the newest registered batch's fetched_at.

Safe to re-run: registered batches sit behind the watermark, and identical
content is skipped by hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from careplatform import config, lakehouse, runtime
from careplatform.dq.engine import run_checks
from careplatform.ingestion import watermarks
from careplatform.ingestion.fetch_to_landing import MANIFEST_NAME, MANIFEST_VERSION
from careplatform.runlog import PipelineFailed, start_run

TABLE = "bronze.raw_documents"
API_LOG = "ops.api_call_log"
INCOMING = "incoming"          # the drop zone inside the landing volume
WATERMARK_SCOPE = "landing"    # one mark per connector: fetched_at of the last registered batch


def pending_batches(connector: str) -> list[tuple[Path, dict]]:
    """Complete batches (manifest present) newer than the watermark, oldest first."""
    root = lakehouse.landing_root() / INCOMING / connector
    last = watermarks.get(connector, WATERMARK_SCOPE) or ""
    found = []
    for manifest_path in root.glob(f"*/*/{MANIFEST_NAME}"):
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        if m["fetched_at"] > last:     # ISO timestamps in UTC sort correctly as text
            found.append((manifest_path.parent, m))
    return sorted(found, key=lambda b: b[1]["fetched_at"])


def run(connector: str = "bclaws") -> dict:
    summary: dict = {"connector": connector}
    with start_run(f"bronze_register:{connector}") as r:
        batches = pending_batches(connector)
        summary["batches"] = len(batches)
        if not batches:
            r.rows_in, r.rows_out = 0, 0
            summary["result"] = "no new batches"
            return summary

        sources = {s["source_id"]: s for s in config.sources()["sources"]}
        existing = lakehouse.read(TABLE)
        known = set(existing["doc_id"])
        root = lakehouse.landing_root()
        now = datetime.now(timezone.utc)
        rows, calls, skipped, files_in = [], [], Counter(), 0

        for folder, m in batches:
            if m.get("manifest_version") != MANIFEST_VERSION:
                raise PipelineFailed(f"{folder}: manifest_version {m.get('manifest_version')} is not supported")

            for f in m["files"]:
                files_in += 1
                path = folder / f["file_name"]
                if not path.exists():
                    raise PipelineFailed(f"{path}: listed in the manifest but missing. Nothing written.")
                data = path.read_bytes()
                doc_id = hashlib.sha256(data).hexdigest()
                if doc_id != f["sha256"]:
                    raise PipelineFailed(f"{path}: hash does not match the manifest. Nothing written.")
                if doc_id in known:
                    skipped["duplicate_content"] += 1
                    continue
                known.add(doc_id)

                src = sources.get(f["source_id"])
                if src is None:
                    raise PipelineFailed(f"{path}: source_id {f['source_id']!r} is not in sources.yaml")
                rows.append({
                    "doc_id": doc_id,
                    "source_id": src["source_id"],
                    "source_system": connector,
                    "file_name": f["file_name"],
                    "source_ref": f["source_ref"],
                    "source_version": f.get("source_version"),
                    "remote_hash": None,
                    "landing_path": path.relative_to(root).as_posix(),
                    "source_url": f["source_url"],
                    "doc_type": src["doc_type"],
                    "jurisdiction": src["jurisdiction"],
                    "mime_type": mimetypes.guess_type(f["file_name"])[0] or "application/octet-stream",
                    "file_size_bytes": len(data),
                    "ingested_at": now,
                    "load_id": r.run_id,
                })

            # the runner's API calls join this run, so bronze.load_id -> run_log -> api_call_log still links up
            calls += [{**c, "run_id": r.run_id} for c in m["api_calls"]]

        new = pd.DataFrame(rows)
        candidate = pd.concat([existing, new], ignore_index=True) if rows else existing
        report = run_checks(TABLE, candidate, r.run_id)
        summary["dq"] = report.summary()
        if not report.passed:
            failed = ", ".join(x["rule_id"] for x in report.critical_failures)
            raise PipelineFailed(f"Critical DQ rules failed: {failed}. Nothing written to bronze.")

        lakehouse.append(TABLE, new)
        lakehouse.append(API_LOG, pd.DataFrame(calls))
        watermarks.set(connector, WATERMARK_SCOPE, batches[-1][1]["fetched_at"], r.run_id)   # last, after data is safe

        r.rows_in, r.rows_out = files_in, len(new)
        summary.update(loaded=len(new), skipped=dict(skipped), api_calls=len(calls), result="succeeded")
        return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Register landed batches from the landing volume into bronze.")
    p.add_argument("--connector", choices=["bclaws"], default="bclaws")
    runtime.add_runtime_args(p)
    a = p.parse_args()
    runtime.apply(a)

    s = run(a.connector)
    print(f"\n=== bronze register: {s['connector']}")
    for k, v in s.items():
        if k == "dq":
            print("  dq checks:\n" + v)
        elif k != "connector":
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()