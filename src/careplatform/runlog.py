"""Every pipeline run leaves a receipt in ops.run_log.

Usage:

    with start_run("bronze_ingest") as run:
        ...do work...
        run.rows_in = 12
        run.rows_out = 4

On enter:  a RUNNING row is written (so a crashed run is visible, see DQ-O-001).
On exit:   the same row is updated to SUCCEEDED, or FAILED with the error.

run.run_id is what every row this run writes carries as load_id. That is the
link that makes record-level lineage work (docs/05_lineage.md).
"""
from __future__ import annotations

import os
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from careplatform import config, lakehouse

TABLE = "ops.run_log"


def _git_commit() -> str | None:
    # on Databricks there is no .git folder; the deploy passes the commit in (runtime.py)
    env = os.getenv("CAREPLATFORM_GIT_COMMIT")
    if env:
        return env[:12]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


class PipelineFailed(Exception):
    """Raised when a pipeline stops on purpose, e.g. a critical DQ rule failed."""


@dataclass
class Run:
    pipeline: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rows_in: int | None = None
    rows_out: int | None = None
    status: str = "RUNNING"
    error_message: str | None = None
    git_commit: str | None = field(default_factory=_git_commit)
    config_hash: str = field(default_factory=config.config_hash)
    ended_at: datetime | None = None

    def as_row(self) -> dict:
        return {
            "run_id": self.run_id,
            "pipeline": self.pipeline,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "git_commit": self.git_commit,
            "config_hash": self.config_hash,
            "error_message": self.error_message,
        }

    def _save(self) -> None:
        lakehouse.upsert(TABLE, pd.DataFrame([self.as_row()]))


@contextmanager
def start_run(pipeline: str):
    run = Run(pipeline=pipeline)
    run._save()
    try:
        yield run
        run.status = "SUCCEEDED"
    except Exception as e:
        run.status = "FAILED"
        run.error_message = f"{type(e).__name__}: {e}"[:2000]
        raise
    finally:
        run.ended_at = datetime.now(timezone.utc)
        run._save()
