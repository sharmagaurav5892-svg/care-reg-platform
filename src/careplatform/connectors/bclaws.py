"""BC Laws API connector (British Columbia legislation, public, no key).

API docs: https://www.bclaws.gov.bc.ca/civix/template/complete/api/index.html
Licence: reuse permitted under the BC King's Printer licence (see sources.yaml).

Each source in config/sources.yaml names a `document_id`, for example
96_2009 for the Residential Care Regulation. For each one we call:

    GET /civix/document/id/complete/statreg/{document_id}/xml

If that returns 404 (some documents are only published as HTML), we fall
back to the HTML version at the same path without /xml. Both are logged.

How we avoid downloading unchanged laws (no commit SHA here, unlike GitHub):

  1. HTTP conditional request. We send the ETag and Last-Modified values
     the server gave us last time, as If-None-Match / If-Modified-Since.
     If the law hasn't changed, the server answers 304 Not Modified with
     no body. Cheap for both sides.
  2. If the server doesn't support that (no ETag, no Last-Modified, or it
     ignores them), we download and compare the SHA-256 with what's in
     bronze. Same hash = skipped by the pipeline as duplicate content.

The watermark per document is a small JSON blob: {"etag": ..., "last_modified": ...}.

Be a polite client: one request per document per run, conditional where
possible, a clear User-Agent, and never in parallel. It's a public service.
"""
from __future__ import annotations

import json
from collections import Counter

import pandas as pd

from careplatform import config
from careplatform.connectors.base import FetchedFile, FetchResult, SourceConnector, gate
from careplatform.connectors.http import ApiClient, ApiError


class BCLawsConnector(SourceConnector):
    source_system = "bclaws"

    def __init__(self, run_id: str, session=None, sleep=None):
        cfg = config.settings()["ingestion"]["bclaws"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.document_path = cfg["document_path"]
        headers = {"User-Agent": cfg["user_agent"]}
        kwargs = {"session": session}
        if sleep:
            kwargs["sleep"] = sleep
        self.client = ApiClient(self.base_url, connector=self.source_system, run_id=run_id,
                                headers=headers, **kwargs)

    @classmethod
    def from_settings(cls, run_id: str) -> "BCLawsConnector":
        return cls(run_id)

    def doc_url(self, document_id: str, fmt: str = "xml") -> str:
        path = self.document_path.format(document_id=document_id)
        return f"{self.base_url}{path}/xml" if fmt == "xml" else f"{self.base_url}{path}"

    def fetch(self, sources: list[dict], existing: pd.DataFrame, full_refresh: bool) -> FetchResult:
        from careplatform.ingestion import watermarks

        approved, skipped = gate(sources, self.source_system)
        res = FetchResult(skipped=Counter(skipped))
        res.info["documents_checked"] = len(approved)

        for src in approved:
            doc_id = src["document_id"]
            # full refresh never reads state, so it also runs where there is no lakehouse (fetch_to_landing)
            last = {} if full_refresh else json.loads(watermarks.get(self.source_system, doc_id) or "{}")
            conditional = {}
            if not full_refresh:
                if last.get("etag"):
                    conditional["If-None-Match"] = last["etag"]
                if last.get("last_modified"):
                    conditional["If-Modified-Since"] = last["last_modified"]

            fmt = "xml"
            try:
                resp = self.client.get(self.doc_url(doc_id, "xml"), headers=conditional)
            except ApiError as e:
                if e.status_code != 404:
                    raise
                fmt = "html"
                res.info.setdefault("fell_back_to_html", []).append(doc_id)
                resp = self.client.get(self.doc_url(doc_id, "html"), headers=conditional)

            if resp.status_code == 304:
                res.skipped["not_modified"] += 1
                continue
            if not resp.content:
                raise ApiError(f"BC Laws returned an empty body for {doc_id}", resp.status_code)

            etag = resp.headers.get("ETag")
            last_modified = resp.headers.get("Last-Modified")
            res.items.append(FetchedFile(
                source=src,
                file_name=f"{doc_id}.{fmt}",
                source_ref=doc_id,
                source_url=self.doc_url(doc_id, "html"),
                data=resp.content,
                source_version=etag or last_modified,
            ))
            res.watermarks.append((doc_id, json.dumps({"etag": etag, "last_modified": last_modified})))
        return res
