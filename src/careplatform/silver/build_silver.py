"""Silver: bronze XML -> silver.document_units (SCD2) -> silver.chunks + silver.cross_references.

    python -m careplatform.silver.build_silver

For each approved BC source, take its newest document in bronze. If silver has
already processed that exact document (watermark), skip it. Otherwise:

  1. Parse the XML into units (bclaws_xml.parse).
  2. Compare with the current units for that source (SCD2):
       same unit_ref, same text   -> nothing to do
       same unit_ref, new text    -> close the old row (valid_to, is_current=false), add the new one
       new unit_ref               -> add it
       unit_ref gone from the law -> close the old row
  3. DQ gate on units (S-001 text present, S-007 one current version each). Critical fail: stop.
  4. Merge units, merge cross references (S-009).
  5. Work out the DESIRED chunks from current, non-repealed units: context header + text,
     long units split at line (subsection) boundaries. chunk_id includes chunker_version.
  6. Sync with the table by chunk_id: insert new ones, retire ones no longer desired,
     leave the rest alone (they keep their original load_id). DQ gate on the active
     chunks after the sync (S-002..S-010), then write only the difference.
  7. Move each source's watermark to the document it processed. Last, after data is safe.

Safe to re-run: an unchanged document is skipped by watermark, and even if
re-parsed, identical text produces identical unit_ids and chunk_ids, so nothing is written.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from careplatform import config, lakehouse, runtime
from careplatform.dq.engine import run_checks
from careplatform.ingestion import watermarks
from careplatform.runlog import PipelineFailed, start_run
from careplatform.silver import bclaws_xml

UNITS, CHUNKS, REFS, BRONZE = "silver.document_units", "silver.chunks", "silver.cross_references", "bronze.raw_documents"
WM_SYSTEM = "silver"          # ops.watermarks: (silver, <source_id>) -> doc_id last processed

PII = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                         # email
    re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),                 # phone
    re.compile(r"\b\d{3}[ -]\d{3}[ -]\d{3}\b"),                      # SIN-like
]


def sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _chunking() -> dict:
    return config.settings()["chunking"]


def chunker_version() -> str:
    return str(_chunking()["chunker_version"])


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _chunking()["chars_per_token"]) if text else 0


# ---------------- units ----------------

def units_for_document(doc: pd.Series, xml_bytes: bytes, now, load_id: str) -> tuple[pd.DataFrame, list[dict]]:
    rows, links = [], []
    for u in bclaws_xml.parse(xml_bytes):
        text_hash = sha(u.text)
        unit_id = sha(doc["source_id"], u.unit_ref, text_hash)
        rows.append({
            "unit_id": unit_id,
            "source_id": doc["source_id"],
            "doc_id": doc["doc_id"],
            "unit_type": "section",
            "unit_ref": u.unit_ref,
            "unit_order": u.unit_order,
            "context_path": u.context_path,
            "heading": u.heading,
            "text": u.text,
            "char_count": len(u.text),
            "token_estimate": estimate_tokens(u.text),
            "text_hash": text_hash,
            "history_note": u.history_note,
            "is_repealed": u.is_repealed,
            "in_force": u.in_force,
            "extraction_method": "xml",
            "valid_from": now,
            "valid_to": None,
            "is_current": True,
            "load_id": load_id,
        })
        for i, (href, label) in enumerate(u.links):
            links.append({
                "ref_id": sha(unit_id, str(i)),
                "unit_id": unit_id,
                "source_id": doc["source_id"],
                "unit_ref": u.unit_ref,
                "target_href": href,
                "target_doc_id": bclaws_xml.target_doc_id(href),
                "target_text": label or href,
                "load_id": load_id,
            })
    return pd.DataFrame(rows), links


def scd2(existing: pd.DataFrame, source_id: str, parsed: pd.DataFrame, now) -> tuple[pd.DataFrame, dict]:
    """Rows to merge for one source: closed old versions + new versions. Plus counts."""
    current = existing[(existing["source_id"] == source_id) & existing["is_current"].astype(bool)]
    cur_by_ref = {r.unit_ref: r for r in current.itertuples(index=False)}
    new_by_ref = {r["unit_ref"]: r for r in parsed.to_dict("records")}

    to_merge, stats = [], Counter()
    for ref, new in new_by_ref.items():
        old = cur_by_ref.get(ref)
        if old is not None and old.text_hash == new["text_hash"]:
            stats["unchanged"] += 1
            continue
        if old is not None:
            to_merge.append({**old._asdict(), "valid_to": now, "is_current": False})
            stats["changed"] += 1
        else:
            stats["added"] += 1
        to_merge.append(new)
    for ref, old in cur_by_ref.items():
        if ref not in new_by_ref:
            to_merge.append({**old._asdict(), "valid_to": now, "is_current": False})
            stats["removed"] += 1
    return pd.DataFrame(to_merge, columns=parsed.columns), dict(stats)


def apply_merge(existing: pd.DataFrame, merged: pd.DataFrame, key: str = "unit_id") -> pd.DataFrame:
    """What the table will look like after the merge. Used for the DQ gate before writing."""
    if merged.empty:
        return existing
    keep = existing[~existing[key].isin(merged[key])]
    return pd.concat([keep, merged], ignore_index=True)


# ---------------- chunks ----------------

def _s(v) -> str:
    """Tables hand back None or NaN for empty text; treat both as ''."""
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def _header(u: dict, titles: dict, part: str = "") -> str:
    src = titles.get(u["source_id"], {})
    ctx = _s(u["context_path"])
    if u["unit_ref"].startswith(ctx + ","):      # schedules: "Sch. B" is already in "Sch. B, s. 1"
        ctx = ""
    bits = [src.get("jurisdiction", ""), src.get("title", u["source_id"]), ctx]
    bits.append(f"{u['unit_ref']} {_s(u['heading'])}".strip() + part)
    return "[" + " | ".join(b for b in bits if b) + "]"


def _split(lines: list[str], target_chars: int) -> list[list[str]]:
    """Pack whole lines (subsections, paragraphs) into pieces of about target size. Never cuts a line."""
    pieces, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) > target_chars:
            pieces.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        pieces.append(cur)
    return pieces


def build_chunks(units: pd.DataFrame, load_id: str) -> pd.DataFrame:
    cfg = _chunking()
    cpt = cfg["chars_per_token"]
    version = chunker_version()
    titles = {s["source_id"]: s for s in config.sources()["sources"]}
    live = units[units["is_current"].astype(bool) & ~units["is_repealed"].astype(bool)
                 & units["in_force"].astype(bool)].sort_values(["source_id", "unit_order"])
    rows = []
    for u in live.to_dict("records"):
        text = _s(u["text"])
        if not text.strip():
            continue
        if estimate_tokens(text) <= cfg["max_tokens"]:
            pieces = [text.split("\n")]
        else:
            pieces = _split(text.split("\n"), cfg["target_tokens"] * cpt)
        for i, piece in enumerate(pieces):
            part = f" (part {i + 1} of {len(pieces)})" if len(pieces) > 1 else ""
            header = _header(u, titles, part)
            body = header + "\n" + "\n".join(piece)
            rows.append({
                "chunk_id": sha(u["unit_id"], str(i), version),
                "unit_id": u["unit_id"],
                "doc_id": u["doc_id"],
                "source_id": u["source_id"],
                "unit_ref": u["unit_ref"],
                "chunk_index": i,
                "context_header": header,
                "text": body,
                "token_estimate": estimate_tokens(body),
                "text_hash": sha(re.sub(r"\s+", " ", body).strip().lower()),
                "pii_flag": any(p.search(body) for p in PII),
                "chunker_version": version,
                "is_active": True,
                "retired_at": None,
                "load_id": load_id,
            })
    return pd.DataFrame(rows, columns=list(lakehouse.schema_for(CHUNKS).names))


def sync_chunks(existing: pd.DataFrame, desired: pd.DataFrame, now) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Diff desired chunks against the table by chunk_id.

    Returns (rows to merge, the table after the merge, counts). Unchanged chunks are
    not in the rows to merge, so they keep their original load_id.
    """
    active = existing[existing["is_active"].astype(bool)] if not existing.empty else existing
    active_ids, desired_ids = set(active["chunk_id"]), set(desired["chunk_id"])

    to_insert = desired[~desired["chunk_id"].isin(active_ids)]           # new, or re-activated
    to_retire = active[~active["chunk_id"].isin(desired_ids)].copy()
    to_retire["is_active"] = False
    to_retire["retired_at"] = now

    merge = pd.concat([to_insert, to_retire], ignore_index=True) if len(to_retire) else to_insert
    after = apply_merge(existing, merge, key="chunk_id") if not existing.empty else to_insert
    counts = {"inserted": len(to_insert), "retired": len(to_retire),
              "unchanged": len(active_ids & desired_ids)}
    return merge, after, counts


# ---------------- the run ----------------

def _gate(table: str, df: pd.DataFrame, run_id: str, summary: dict) -> None:
    report = run_checks(table, df, run_id)
    summary.setdefault("dq", []).append(f"{table}:\n{report.summary()}")
    if not report.passed:
        failed = ", ".join(x["rule_id"] for x in report.critical_failures)
        raise PipelineFailed(f"Critical DQ rules failed on {table}: {failed}. Nothing written to {table}.")


def run() -> dict:
    summary: dict = {}
    with start_run("silver:bclaws") as r:
        now = datetime.now(timezone.utc)
        bronze = lakehouse.read(BRONZE)
        bronze = bronze[bronze["source_system"] == "bclaws"]
        latest = bronze.sort_values("ingested_at").groupby("source_id").tail(1)

        existing = lakehouse.read(UNITS)
        merged_parts, links, processed, stats = [], [], [], Counter()
        for doc in latest.itertuples(index=False):
            doc = pd.Series(doc._asdict())
            if watermarks.get(WM_SYSTEM, doc["source_id"]) == doc["doc_id"]:
                stats["documents_up_to_date"] += 1
                continue
            path = lakehouse.landing_root() / doc["landing_path"]
            parsed, doc_links = units_for_document(doc, path.read_bytes(), now, r.run_id)
            changes, s = scd2(existing, doc["source_id"], parsed, now)
            stats.update(s)
            stats["documents_parsed"] += 1
            merged_parts.append(changes)
            new_ids = set(changes.loc[changes["is_current"], "unit_id"]) if not changes.empty else set()
            links += [x for x in doc_links if x["unit_id"] in new_ids]
            processed.append((doc["source_id"], doc["doc_id"]))

        merged = pd.concat(merged_parts, ignore_index=True) if merged_parts else existing.iloc[0:0]
        after = apply_merge(existing, merged)
        _gate(UNITS, after, r.run_id, summary)
        if not merged.empty:
            lakehouse.upsert(UNITS, merged)
        if links:
            refs = pd.DataFrame(links)
            _gate(REFS, refs, r.run_id, summary)
            lakehouse.upsert(REFS, refs)

        desired = build_chunks(after, r.run_id)
        chunk_merge, chunks_after, chunk_counts = sync_chunks(lakehouse.read(CHUNKS), desired, now)
        active = chunks_after[chunks_after["is_active"].astype(bool)]
        _gate(CHUNKS, active, r.run_id, summary)          # rules apply to active chunks
        if not chunk_merge.empty:
            lakehouse.upsert(CHUNKS, chunk_merge)

        for source_id, doc_id in processed:          # last, after data is safe
            watermarks.set(WM_SYSTEM, source_id, doc_id, r.run_id)

        r.rows_in, r.rows_out = len(latest), len(chunk_merge)
        summary.update(stats=dict(stats), units_current=int(after["is_current"].sum()) if not after.empty else 0,
                       chunks_active=len(active), chunks=chunk_counts,
                       cross_references_added=len(links), result="succeeded")
        return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Build silver (units, chunks, cross references) from bronze BC XML.")
    p.add_argument("--connector", choices=["bclaws"], default="bclaws")
    runtime.add_runtime_args(p)
    a = p.parse_args()
    runtime.apply(a)
    s = run()
    print("\n=== silver: bclaws")
    for k, v in s.items():
        if k == "dq":
            print("  dq checks:\n" + "\n".join(v))
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
