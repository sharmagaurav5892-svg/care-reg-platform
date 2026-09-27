"""Gold: embed active silver chunks with a Databricks-hosted model (ADR-010).

    python -m careplatform.gold.embed_chunks

Only what changed is embedded:

    wanted    = active, non-PII chunks in silver.chunks
    have      = active embeddings for this model
    to embed  = wanted but not had      -> call the model, insert
    to retire = had but no longer wanted -> is_active = false, retired_at = now
    unchanged = both                     -> untouched, no API call

Calls go through ApiClient: batches of models.embeddings.batch_size, a pause between
calls, 429 and 5xx retried with backoff, every attempt logged to ops.api_call_log.
The endpoint runs inside Databricks: no API key, and the text never leaves the platform.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from careplatform import config, lakehouse, runtime
from careplatform.connectors.http import ApiClient
from careplatform.dq.engine import run_checks
from careplatform.runlog import PipelineFailed, start_run

CHUNKS, TABLE = "silver.chunks", "gold.chunk_embeddings"
KEY = ["chunk_id", "embedding_model"]


def settings() -> dict:
    return config.settings()["models"]["embeddings"]


def wanted_chunks(chunks: pd.DataFrame) -> pd.DataFrame:
    """Chunks that must be searchable: active, and not flagged for personal information."""
    return chunks[chunks["is_active"].astype(bool) & ~chunks["pii_flag"].astype(bool)]


class DatabricksEmbedder:
    """POST /serving-endpoints/<endpoint>/invocations on the workspace's own model serving."""

    source_system = "databricks_fm"

    def __init__(self, run_id: str, host: str | None = None, headers: dict | None = None,
                 session=None, sleep=None):
        if host is None or headers is None:
            from databricks.sdk.core import Config
            cfg = Config()          # the job's own identity on Databricks; your CLI login on a laptop
            host, headers = cfg.host, cfg.authenticate()
        kwargs = {"session": session}
        if sleep:
            kwargs["sleep"] = sleep
        self.client = ApiClient(host, connector=self.source_system, run_id=run_id,
                                headers=headers, **kwargs)
        self.endpoint = settings()["endpoint"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.post(f"/serving-endpoints/{self.endpoint}/invocations", json={"input": texts})
        data = resp.json()["data"]
        data = sorted(data, key=lambda d: d.get("index", 0))      # keep vectors in input order
        return [d["embedding"] for d in data]


def run(embedder=None) -> dict:
    cfg = settings()
    model = cfg["endpoint"]
    summary: dict = {"model": model}
    with start_run("gold_embeddings") as r:
        emb = embedder or DatabricksEmbedder(r.run_id)
        emb.client.run_id = r.run_id
        try:
            now = datetime.now(timezone.utc)
            wanted = wanted_chunks(lakehouse.read(CHUNKS))
            existing = lakehouse.read(TABLE)
            active = existing[existing["is_active"].astype(bool) & (existing["embedding_model"] == model)]

            todo = wanted[~wanted["chunk_id"].isin(set(active["chunk_id"]))]
            retire = active[~active["chunk_id"].isin(set(wanted["chunk_id"]))].copy()
            retire["is_active"] = False
            retire["retired_at"] = now

            rows, texts, ids = [], list(todo["text"]), list(todo["chunk_id"])
            size, pause = cfg["batch_size"], cfg["min_seconds_between_calls"]
            for i in range(0, len(texts), size):
                if i:
                    emb.client._sleep(pause)                    # pace ourselves under the QPS limit
                batch = texts[i:i + size]
                vectors = emb.embed(batch)
                if len(vectors) != len(batch):
                    raise PipelineFailed(f"Asked for {len(batch)} vectors, got {len(vectors)}. Nothing written.")
                for chunk_id, v in zip(ids[i:i + size], vectors):
                    rows.append({"chunk_id": chunk_id, "embedding_model": model, "embedding_dim": len(v),
                                 "vector": [float(x) for x in v], "is_active": True,
                                 "retired_at": None, "load_id": r.run_id})

            cols = list(lakehouse.schema_for(TABLE).names)
            new = pd.DataFrame(rows, columns=cols)
            merge = pd.concat([x for x in (new, retire[cols]) if len(x)], ignore_index=True) \
                if len(new) or len(retire) else new
            keys = set(map(tuple, merge[KEY].values))
            keep = existing[[tuple(k) not in keys for k in existing[KEY].values]] if len(existing) else existing
            after = pd.concat([keep, merge], ignore_index=True) if len(merge) else existing

            report = run_checks(TABLE, after[after["is_active"].astype(bool)] if len(after) else after, r.run_id)
            summary["dq"] = report.summary()
            if not report.passed:
                failed = ", ".join(x["rule_id"] for x in report.critical_failures)
                raise PipelineFailed(f"Critical DQ rules failed on {TABLE}: {failed}. Nothing written.")
            if len(merge):
                lakehouse.upsert(TABLE, merge)

            r.rows_in, r.rows_out = len(wanted), len(merge)
            summary.update(embedded=len(new), retired=len(retire),
                           unchanged=len(wanted) - len(todo), result="succeeded")
            return summary
        finally:
            summary["api_calls"] = len(emb.client.calls)
            emb.client.flush_log()


def main() -> None:
    p = argparse.ArgumentParser(description="Embed new silver chunks with the configured embedding model.")
    runtime.add_runtime_args(p)
    a = p.parse_args()
    runtime.apply(a)
    s = run()
    print("\n=== gold: embeddings")
    for k, v in s.items():
        print(f"  {k}: {v}" if k != "dq" else "  dq checks:\n" + v)


if __name__ == "__main__":
    main()
