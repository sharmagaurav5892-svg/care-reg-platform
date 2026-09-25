"""Data quality engine.

Reads rules for a table from config/dq_rules.yaml, runs them against a
DataFrame, writes one row per rule to ops.dq_results, and tells the caller
whether any critical rule failed.

The pipeline calls it BEFORE writing to the real table. That is the
promotion gate from docs/04: if a critical rule fails, nothing is written.

Built-in check types:
    unique, not_null, min_value, max_value, allowed_values,
    foreign_key, row_count_min, ratio_min, ratio_max
Anything else is `custom` and must have a function registered in
careplatform/dq/custom_rules.py under the rule id.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import duckdb
import pandas as pd

from careplatform import config, lakehouse

RESULTS_TABLE = "ops.dq_results"

# rule_id -> function(df, rule) -> CheckOutcome
CUSTOM: dict[str, Callable[[pd.DataFrame, dict], "CheckOutcome"]] = {}


def custom(rule_id: str):
    """Decorator to register a custom rule implementation."""
    def wrap(fn):
        CUSTOM[rule_id] = fn
        return fn
    return wrap


@dataclass
class CheckOutcome:
    passed: bool
    observed_value: float | None = None
    threshold: float | None = None
    failed_rows: int | None = None


# ---------- built-in checks ----------

def _unique(df, r):
    dupes = int(df[r["column"]].duplicated(keep=False).sum())
    return CheckOutcome(dupes == 0, float(dupes), 0.0, dupes)


def _not_null(df, r):
    col = df[r["column"]]
    bad = int(col.isna().sum() + (col.astype(str).str.strip() == "").sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


def _min_value(df, r):
    bad = int((df[r["column"]] < r["threshold"]).sum())
    observed = float(df[r["column"]].min()) if len(df) else None
    return CheckOutcome(bad == 0, observed, float(r["threshold"]), bad)


def _max_value(df, r):
    bad = int((df[r["column"]] > r["threshold"]).sum())
    observed = float(df[r["column"]].max()) if len(df) else None
    return CheckOutcome(bad == 0, observed, float(r["threshold"]), bad)


def _allowed_values(df, r):
    allowed = set(r["values"])
    col = df[r["column"]]
    ok = col.isin([v for v in allowed if v is not None])
    if None in allowed:
        ok = ok | col.isna()
    bad = int((~ok).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


def _foreign_key(df, r):
    ref_table, _, ref_col = r["references"].rpartition(".")
    parent = lakehouse.read(ref_table)
    bad = int((~df[r["column"]].isin(parent[ref_col])).sum())
    return CheckOutcome(bad == 0, float(bad), 0.0, bad)


def _row_count_min(df, r):
    n = len(df)
    return CheckOutcome(n >= r["threshold"], float(n), float(r["threshold"]), None)


def _ratio(df, r):
    """Share of rows where a SQL boolean expression is true, computed with DuckDB."""
    if df.empty:
        return 1.0
    con = duckdb.connect()
    con.register("t", df)
    return float(con.execute(
        f"SELECT avg(CASE WHEN ({r['expression']}) THEN 1.0 ELSE 0.0 END) FROM t"
    ).fetchone()[0])


def _ratio_min(df, r):
    v = _ratio(df, r)
    return CheckOutcome(v >= r["threshold"], v, float(r["threshold"]), None)


def _ratio_max(df, r):
    v = _ratio(df, r)
    return CheckOutcome(v <= r["threshold"], v, float(r["threshold"]), None)


BUILT_IN = {
    "unique": _unique,
    "not_null": _not_null,
    "min_value": _min_value,
    "max_value": _max_value,
    "allowed_values": _allowed_values,
    "foreign_key": _foreign_key,
    "row_count_min": _row_count_min,
    "ratio_min": _ratio_min,
    "ratio_max": _ratio_max,
}


# ---------- runner ----------

@dataclass
class DQReport:
    results: list[dict]

    @property
    def critical_failures(self) -> list[dict]:
        return [x for x in self.results if x["status"] == "fail" and x["severity"] == "critical"]

    @property
    def warnings(self) -> list[dict]:
        return [x for x in self.results if x["status"] == "fail" and x["severity"] == "warning"]

    @property
    def passed(self) -> bool:
        return not self.critical_failures

    def summary(self) -> str:
        lines = []
        for x in self.results:
            mark = "PASS" if x["status"] == "pass" else ("FAIL" if x["severity"] == "critical" else "WARN")
            lines.append(f"  [{mark}] {x['rule_id']} {x['table_name']} observed={x['observed_value']}")
        return "\n".join(lines)


def rules_for(table: str) -> list[dict]:
    return [r for r in config.dq_rules()["rules"] if r["table"] == table]


def run_checks(table: str, df: pd.DataFrame, run_id: str, write: bool = True) -> DQReport:
    # importing registers the custom rules
    from careplatform.dq import custom_rules  # noqa: F401

    now = datetime.now(timezone.utc)
    results = []
    for r in rules_for(table):
        if r["check"] == "custom":
            fn = CUSTOM.get(r["id"])
            if fn is None:
                raise NotImplementedError(
                    f"{r['id']} is a custom rule with no implementation in dq/custom_rules.py"
                )
        else:
            fn = BUILT_IN[r["check"]]
        out = fn(df, r)
        results.append({
            "run_id": run_id,
            "rule_id": r["id"],
            "table_name": table,
            "dimension": r["dimension"],
            "severity": r["severity"],
            "status": "pass" if out.passed else "fail",
            "observed_value": out.observed_value,
            "threshold": out.threshold,
            "failed_rows": out.failed_rows,
            "checked_at": now,
        })
    if write and results:
        lakehouse.append(RESULTS_TABLE, pd.DataFrame(results))
    return DQReport(results)
