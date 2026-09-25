"""Bronze ingestion: GitHub API -> landing files -> bronze.raw_documents.

Run it:
    python -m careplatform.ingestion.bronze_ingest
    python -m careplatform.ingestion.bronze_ingest --full-refresh

What happens, in order:

  1. Start a run in ops.run_log (status RUNNING).
  2. Ask GitHub for the latest commit on the branch.
     Same as the saved watermark? Stop here. Nothing changed.
  3. List every file in the repo at that commit.
  4. Decide, for each file, whether to take it. Files are skipped when:
       - their folder isn't mapped to any source in config/sources.yaml
       - their source isn't approved yet   <- governance gate, BEFORE download
       - the extension or size isn't allowed
       - their Git blob SHA is already in bronze (unchanged file)
  5. Download only the files that are left.
  6. Hash each file (SHA-256 = doc_id). Same content already loaded under
     another name? Skip it.
  7. Save the bytes to data/landing/github/<source>/<commit>/<file>.
  8. Run DQ rules on bronze + the new rows. Any critical failure: stop,
     write nothing to bronze, keep the landing files for debugging.
  9. Append the new rows to bronze and move the watermark forward.
 10. Always (even on failure): flush every API call to ops.api_call_log and
     close the run as SUCCEEDED or FAILED.
"""
from __future__ import annotations

import argparse
import hashlib
import mimetypes
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from careplatform import config, lakehouse
from careplatform.connectors.github import GitHubConnector, RemoteFile
from careplatform.dq.engine import run_checks
from careplatform.ingestion import watermarks
from careplatform.runlog import PipelineFailed, start_run

TABLE = "bronze.raw_documents"


def _source_for(path: str, sources: list[dict]) -> dict | None:
    """Longest matching repo_path wins, so nested folders map correctly."""
    matches = [s for s in sources if path.startswith(s["repo_path"])]
    return max(matches, key=lambda s: len(s["repo_path"])) if matches else None


def plan(files: list[RemoteFile], existing: pd.DataFrame) -> tuple[list[tuple[RemoteFile, dict]], Counter]:
    """Decide which files to download. Pure function, easy to test."""
    ing = config.settings()["ingestion"]
    allowed_ext = {e.lower() for e in ing["allowed_extensions"]}
    max_bytes = ing["max_file_size_mb"] * 1024 * 1024
    sources = config.sources()["sources"]
    known_blobs = set(existing["git_blob_sha"])

    todo, skipped = [], Counter()
    for f in files:
        name = PurePosixPath(f.path).name
        if name.startswith(".") or name.lower() == "readme.md":
            skipped["housekeeping"] += 1
            continue
        src = _source_for(f.path, sources)
        if src is None:
            skipped["unregistered_path"] += 1
        elif src["status"] != "approved":
            skipped["source_not_approved"] += 1
        elif PurePosixPath(f.path).suffix.lower() not in allowed_ext:
            skipped["extension_not_allowed"] += 1
        elif f.size > max_bytes:
            skipped["too_large"] += 1
        elif f.blob_sha in known_blobs:
            skipped["unchanged"] += 1
        else:
            todo.append((f, src))
    return todo, skipped


def run(full_refresh: bool = False, connector: GitHubConnector | None = None) -> dict:
    summary: dict = {}
    with start_run("bronze_ingest") as r:
        gh = connector or GitHubConnector.from_settings(r.run_id)
        gh.client.run_id = r.run_id
        try:
            head = gh.head_commit()
            last = watermarks.get("github", gh.scope)
            summary.update(commit=head[:12], previous_watermark=(last or "none")[:12])

            if head == last and not full_refresh:
                r.rows_in, r.rows_out = 0, 0
                summary["result"] = "no new commits, nothing to do"
                return summary

            root = config.settings()["ingestion"]["github"]["root_path"]
            files = gh.list_files(head, prefix=root)
            existing = lakehouse.read(TABLE)
            todo, skipped = plan(files, existing if not full_refresh else existing.iloc[0:0])

            known_docs = set(existing["doc_id"])
            landing_root = config.REPO_ROOT / config.settings()["lakehouse"]["landing_root"]
            now = datetime.now(timezone.utc)
            rows = []
            for f, src in todo:
                data = gh.download(f)
                doc_id = hashlib.sha256(data).hexdigest()
                if doc_id in known_docs:
                    skipped["duplicate_content"] += 1
                    continue
                known_docs.add(doc_id)

                name = PurePosixPath(f.path).name
                dest = landing_root / "github" / src["source_id"] / head[:12] / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

                rows.append({
                    "doc_id": doc_id,
                    "source_id": src["source_id"],
                    "source_system": "github",
                    "file_name": name,
                    "repo_path": f.path,
                    "landing_path": Path(dest).relative_to(config.REPO_ROOT).as_posix(),
                    "source_url": gh.html_url(head, f.path),
                    "commit_sha": head,
                    "git_blob_sha": f.blob_sha,
                    "doc_type": src["doc_type"],
                    "jurisdiction": src["jurisdiction"],
                    "mime_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "file_size_bytes": len(data),
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
            watermarks.set("github", gh.scope, head, r.run_id)

            r.rows_in, r.rows_out = len(files), len(new)
            summary.update(files_seen=len(files), loaded=len(new), skipped=dict(skipped),
                           result="succeeded")
            return summary
        finally:
            summary["api_calls"] = len(gh.client.calls)
            gh.client.flush_log()


def main() -> None:
    p = argparse.ArgumentParser(description="Load source documents from GitHub into bronze.")
    p.add_argument("--full-refresh", action="store_true",
                   help="Ignore the watermark and re-check every file (still skips identical content).")
    args = p.parse_args()
    try:
        s = run(full_refresh=args.full_refresh)
    except Exception as e:
        print(f"\nRun FAILED: {e}\nSee ops.run_log and ops.dq_results for details.")
        raise SystemExit(1)
    print("\nBronze ingest")
    for k, v in s.items():
        if k == "dq":
            print("  dq checks:\n" + v)
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
