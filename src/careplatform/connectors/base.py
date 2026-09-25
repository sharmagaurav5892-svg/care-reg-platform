"""The contract every source connector follows.

The bronze pipeline doesn't know or care whether files come from GitHub,
the BC Laws API, SharePoint or S3. It asks a connector to fetch, and gets
back the same shape every time:

    FetchResult
      items       files that are new or changed, with their bytes
      skipped     counts of why other files were not taken
      watermarks  positions to save, but ONLY after the load succeeds

That separation is the point. Connectors know how to talk to their API.
The pipeline knows the platform rules (hashing, landing, DQ, lineage).
Adding a new source means writing one connector, not a new pipeline.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FetchedFile:
    source: dict              # the entry from config/sources.yaml
    file_name: str
    source_ref: str           # repo path (GitHub) or document id (BC Laws)
    source_url: str           # link a person can open to see exactly what we loaded
    data: bytes
    source_version: str | None = None   # commit SHA (GitHub) or ETag / Last-Modified (BC Laws)
    remote_hash: str | None = None      # Git blob SHA (GitHub); None when the API has no hash


@dataclass
class FetchResult:
    items: list[FetchedFile] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    watermarks: list[tuple[str, str]] = field(default_factory=list)   # (scope, value)
    info: dict = field(default_factory=dict)


class SourceConnector:
    """Base class. Subclasses set source_system and implement fetch()."""

    source_system: str = ""
    client = None   # an ApiClient; the pipeline flushes its call log

    def fetch(self, sources: list[dict], existing: pd.DataFrame, full_refresh: bool) -> FetchResult:
        raise NotImplementedError


def gate(sources: list[dict], source_system: str) -> tuple[list[dict], Counter]:
    """Governance gate: only approved sources for this connector get fetched at all."""
    skipped: Counter = Counter()
    mine = [s for s in sources if s["connector"] == source_system]
    approved = []
    for s in mine:
        if s["status"] == "approved":
            approved.append(s)
        else:
            skipped["source_not_approved"] += 1
    return approved, skipped
