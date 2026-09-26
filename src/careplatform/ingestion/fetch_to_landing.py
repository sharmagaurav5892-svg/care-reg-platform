"""Fetch source documents into a local outbox, with a manifest.

First half of the landing zone pattern (ADR-008). It runs OUTSIDE Databricks,
on a GitHub Actions runner, because Databricks Free Edition blocks outbound
calls to bclaws.gov.bc.ca. The second half, inside Databricks, reads the
manifest and registers the files in bronze.

    python -m careplatform.ingestion.fetch_to_landing --connector bclaws

What it writes:

    <out>/bclaws/<YYYY-MM-DD>/<load_id>/96_2009.html
    <out>/bclaws/<YYYY-MM-DD>/<load_id>/...
    <out>/bclaws/<YYYY-MM-DD>/<load_id>/_manifest.json   <- written LAST

Rules:
  * No state. It never reads watermarks or bronze. It downloads every approved
    document every time. Databricks decides what is new by comparing hashes.
  * The manifest is written last. A folder without one is an unfinished batch,
    and the register step ignores it.
  * Any API failure stops the run before the manifest is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from careplatform import config, runtime
from careplatform.connectors.bclaws import BCLawsConnector
from careplatform.runlog import _git_commit

MANIFEST_NAME = "_manifest.json"
MANIFEST_VERSION = 1


def fetch(out_dir: Path, connector: BCLawsConnector | None = None) -> Path:
    """Fetch every approved BC document. Returns the batch folder."""
    load_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn = connector or BCLawsConnector(run_id=load_id)
    conn.client.run_id = load_id

    res = conn.fetch(config.sources()["sources"], pd.DataFrame(), full_refresh=True)

    batch = Path(out_dir) / conn.source_system / now.strftime("%Y-%m-%d") / load_id
    batch.mkdir(parents=True, exist_ok=True)

    files = []
    for f in res.items:
        (batch / f.file_name).write_bytes(f.data)
        files.append({
            "source_id": f.source["source_id"],
            "file_name": f.file_name,
            "sha256": hashlib.sha256(f.data).hexdigest(),
            "size_bytes": len(f.data),
            "source_ref": f.source_ref,
            "source_url": f.source_url,
            "source_version": f.source_version,
        })

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "load_id": load_id,
        "connector": conn.source_system,
        "fetched_at": now.isoformat(),
        "git_commit": _git_commit(),
        "files": files,
        "skipped": dict(res.skipped),
        "api_calls": conn.client.calls,
    }
    text = json.dumps(manifest, indent=2, default=str)
    (batch / MANIFEST_NAME).write_text(text, encoding="utf-8")
    return batch


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch BC Laws documents into a local outbox with a manifest.")
    p.add_argument("--out", default="data/outbox", help="local folder to write the batch into")
    runtime.add_runtime_args(p)
    a = p.parse_args()
    runtime.apply(a)

    batch = fetch(Path(a.out))
    m = json.loads((batch / MANIFEST_NAME).read_text(encoding="utf-8"))
    print(f"batch:     {batch}")
    print(f"files:     {len(m['files'])}")
    print(f"skipped:   {m['skipped']}")
    print(f"api calls: {len(m['api_calls'])}")


if __name__ == "__main__":
    main()