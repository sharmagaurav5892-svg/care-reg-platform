"""High-water marks for incremental loads.

A watermark is "how far we got last time". For GitHub it is the commit SHA
of the last successful load. Next run compares it with the current head:
same SHA means nothing changed and the run stops after one API call.

The watermark is only moved forward AFTER the data is safely written and
DQ has passed. If a run fails halfway, the old watermark stays, so the next
run redoes the work. Moving it too early is how pipelines lose data.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from careplatform import lakehouse

TABLE = "ops.watermarks"


def get(source_system: str, scope: str) -> str | None:
    df = lakehouse.read(TABLE)
    hit = df[(df["source_system"] == source_system) & (df["scope"] == scope)]
    return None if hit.empty else hit.iloc[0]["watermark_value"]


def set(source_system: str, scope: str, value: str, run_id: str) -> None:  # noqa: A001
    lakehouse.upsert(TABLE, pd.DataFrame([{
        "source_system": source_system,
        "scope": scope,
        "watermark_value": value,
        "updated_at": datetime.now(timezone.utc),
        "load_id": run_id,
    }]))
