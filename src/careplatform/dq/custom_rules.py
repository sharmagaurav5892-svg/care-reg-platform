"""Custom DQ rules: checks that need more than a one line comparison.

Each function is registered against a rule id in config/dq_rules.yaml.
Rules for silver and gold tables get their implementations in later steps.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from careplatform import config
from careplatform.dq.engine import CheckOutcome, custom


@custom("DQ-B-004")
def source_is_approved(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    """Every file must come from a source that the Governance Council approved."""
    approved = {s["source_id"] for s in config.sources()["sources"] if s["status"] == "approved"}
    bad = int((~df["source_id"].isin(approved)).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


@custom("DQ-B-005")
def landing_hash_matches(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    """The file on disk must still hash to its doc_id. Catches edited or corrupted landing files."""
    import hashlib

    from careplatform import lakehouse

    root = lakehouse.landing_root()
    bad = 0
    for _, row in df.iterrows():
        path = root / row["landing_path"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != row["doc_id"]:
            bad += 1
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


@custom("DQ-O-001")
def no_stuck_runs(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    started = pd.to_datetime(df["started_at"], utc=True)
    bad = int(((df["status"] == "RUNNING") & (started < cutoff)).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


@custom("DQ-O-002")
def rule_ids_exist(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    known = {r["id"] for r in config.dq_rules()["rules"]}
    bad = int((~df["rule_id"].isin(known)).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)



# ---------------- silver ----------------

@custom("DQ-S-005")
def few_duplicate_chunks(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    """Share of chunks whose text_hash appears more than once."""
    if df.empty:
        return CheckOutcome(True, 0.0, rule["threshold"], 0)
    dupes = int(df["text_hash"].duplicated(keep=False).sum())
    share = dupes / len(df)
    return CheckOutcome(share <= rule["threshold"], round(share, 4), rule["threshold"], dupes)


@custom("DQ-S-007")
def one_current_version(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    """No source_id + unit_ref may have two current rows."""
    cur = df[df["is_current"].astype(bool)]
    bad = int(cur.duplicated(["source_id", "unit_ref"], keep=False).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


@custom("DQ-S-008")
def chunks_only_from_live_units(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    """Every chunk's unit must be current and not repealed."""
    from careplatform import lakehouse

    units = lakehouse.read("silver.document_units")
    live = set(units.loc[units["is_current"].astype(bool) & ~units["is_repealed"].astype(bool), "unit_id"])
    bad = int((~df["unit_id"].isin(live)).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)
    

@custom("DQ-S-010")
def chunks_built_by_current_logic(df: pd.DataFrame, rule: dict) -> CheckOutcome:
    """Every (active) chunk carries the current chunker_version."""
    from careplatform.silver.build_silver import chunker_version

    bad = int((df["chunker_version"].astype(str) != chunker_version()).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)