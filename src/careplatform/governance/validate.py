"""Validates the governance config.

Each function returns a list of problems (empty list = all good). The tests
call these, and you can also run them by hand:

    python -m careplatform.governance.validate
"""
from __future__ import annotations

import sys

from careplatform import config

REQUIRED_TABLE_FIELDS = [
    "name", "layer", "domain", "description", "classification",
    "retention_days", "grain", "primary_key", "write_mode", "columns",
]
REQUIRED_COLUMN_FIELDS = ["name", "type", "nullable", "description"]
REQUIRED_RULE_FIELDS = ["id", "table", "dimension", "severity", "check", "description"]
CONNECTOR_FIELDS = {"github": ["repo_path"], "bclaws": ["document_id"]}
ALLOWED_JURISDICTIONS = {"ON", "BC"}
REQUIRED_SOURCE_FIELDS = [
    "source_id", "connector", "title", "publisher", "url", "doc_type", "jurisdiction",
    "classification", "license_note", "redistribute_raw", "status",
]
# every row in these layers must carry a load_id back to ops.run_log
LINEAGE_LAYERS = {"bronze", "silver", "gold"}
LINEAGE_EXEMPT = {"gold.llm_call_log", "gold.eval_questions", "gold.eval_results"}


def check_catalog(cat: dict | None = None) -> list[str]:
    cat = cat or config.catalog()
    problems: list[str] = []
    allowed_cls = set(cat["allowed_classifications"])
    allowed_layers = set(cat["allowed_layers"])
    domains = set(cat["domains"])
    seen: set[str] = set()

    for t in cat["tables"]:
        name = t.get("name", "<unnamed>")
        for field in REQUIRED_TABLE_FIELDS:
            if field not in t or t[field] in (None, "", []):
                problems.append(f"{name}: missing {field}")
        if name in seen:
            problems.append(f"{name}: defined twice")
        seen.add(name)

        if t.get("classification") not in allowed_cls:
            problems.append(f"{name}: classification {t.get('classification')!r} not allowed")
        if t.get("layer") not in allowed_layers:
            problems.append(f"{name}: layer {t.get('layer')!r} not allowed")
        if not name.startswith(f"{t.get('layer')}."):
            problems.append(f"{name}: name must start with its layer")
        if t.get("domain") not in domains:
            problems.append(f"{name}: domain {t.get('domain')!r} has no owner defined")
        if not isinstance(t.get("retention_days"), int) or t["retention_days"] <= 0:
            problems.append(f"{name}: retention_days must be a positive integer")

        col_names = []
        for c in t.get("columns", []):
            for field in REQUIRED_COLUMN_FIELDS:
                if field not in c:
                    problems.append(f"{name}.{c.get('name', '?')}: missing {field}")
            col_names.append(c.get("name"))
        for pk in t.get("primary_key", []):
            if pk not in col_names:
                problems.append(f"{name}: primary key {pk} is not a column")
        if t.get("layer") in LINEAGE_LAYERS and name not in LINEAGE_EXEMPT:
            if "load_id" not in col_names:
                problems.append(f"{name}: missing load_id column (lineage policy, docs/05)")

    return problems


def check_dq_rules(rules: dict | None = None, cat: dict | None = None) -> list[str]:
    rules = rules or config.dq_rules()
    cat = cat or config.catalog()
    problems: list[str] = []
    tables = {t["name"]: {c["name"] for c in t["columns"]} for t in cat["tables"]}
    ids: set[str] = set()

    for r in rules["rules"]:
        rid = r.get("id", "<no id>")
        for field in REQUIRED_RULE_FIELDS:
            if field not in r:
                problems.append(f"{rid}: missing {field}")
        if rid in ids:
            problems.append(f"{rid}: duplicate rule id")
        ids.add(rid)

        if r.get("severity") not in rules["allowed_severities"]:
            problems.append(f"{rid}: severity {r.get('severity')!r} not allowed")
        if r.get("dimension") not in rules["allowed_dimensions"]:
            problems.append(f"{rid}: dimension {r.get('dimension')!r} not allowed")

        table = r.get("table")
        if table not in tables:
            problems.append(f"{rid}: table {table} is not in the catalog")
            continue
        col = r.get("column")
        if col and col not in tables[table]:
            problems.append(f"{rid}: column {col} is not in {table}")
        needs = {
            "ratio_min": ["expression", "threshold"], "ratio_max": ["expression", "threshold"],
            "min_value": ["column", "threshold"], "max_value": ["column", "threshold"],
            "row_count_min": ["threshold"], "allowed_values": ["column", "values"],
            "unique": ["column"], "not_null": ["column"], "foreign_key": ["column", "references"],
        }.get(r.get("check"), [])
        for field in needs:
            if field not in r:
                problems.append(f"{rid}: check {r.get('check')} needs {field}")
        if r.get("check") == "foreign_key":
            ref = r.get("references", "")
            ref_table, _, ref_col = ref.rpartition(".")
            if ref_table not in tables or ref_col not in tables[ref_table]:
                problems.append(f"{rid}: references {ref} which is not in the catalog")

    # every table must have at least one DQ rule
    covered = {r["table"] for r in rules["rules"]}
    for t in tables:
        if t not in covered:
            problems.append(f"{t}: has no DQ rules")
    return problems


def check_sources(src: dict | None = None) -> list[str]:
    src = src or config.sources()
    problems: list[str] = []
    allowed_doc_types = {"act", "regulation", "inspection_report"}
    root = config.settings()["ingestion"]["github"]["root_path"]
    ids: set[str] = set()
    paths: set[str] = set()
    for s in src["sources"]:
        sid = s.get("source_id", "<no id>")
        for field in REQUIRED_SOURCE_FIELDS:
            if field not in s:
                problems.append(f"{sid}: missing {field}")
        if sid in ids:
            problems.append(f"{sid}: duplicate source_id")
        ids.add(sid)
        # YAML reads bare ON/OFF/YES/NO as booleans. Every text field must really be text.
        for field in ("source_id", "jurisdiction", "doc_type", "repo_path", "document_id", "status"):
            if field in s and not isinstance(s[field], str):
                problems.append(f"{sid}: {field} must be a string, got {s[field]!r} (quote it in YAML)")
        conn = s.get("connector")
        if conn not in CONNECTOR_FIELDS:
            problems.append(f"{sid}: unknown connector {conn!r}")
            continue
        for field in CONNECTOR_FIELDS[conn]:
            if not s.get(field):
                problems.append(f"{sid}: connector {conn} needs {field}")
        if s.get("jurisdiction") not in ALLOWED_JURISDICTIONS:
            problems.append(f"{sid}: jurisdiction {s.get('jurisdiction')!r} not in {sorted(ALLOWED_JURISDICTIONS)}")
        # the same repo folder or BC document must not be claimed by two sources
        key = s.get("repo_path") or s.get("document_id")
        if key in paths:
            problems.append(f"{sid}: {key} is already used by another source")
        paths.add(key)
        if conn == "github":
            rp = s.get("repo_path", "")
            if not rp.startswith(root) or not rp.endswith("/"):
                problems.append(f"{sid}: repo_path must sit under {root} and end with /")
        if s.get("doc_type") not in allowed_doc_types:
            problems.append(f"{sid}: doc_type {s.get('doc_type')!r} not allowed")
        if s.get("classification") != "Public":
            problems.append(f"{sid}: only Public sources may be ingested")
        if s.get("status") == "approved" and not (s.get("approved_by") and s.get("approved_on")):
            problems.append(f"{sid}: approved sources need approved_by and approved_on")
    return problems


def run_all() -> list[str]:
    return check_catalog() + check_dq_rules() + check_sources()


if __name__ == "__main__":
    issues = run_all()
    if issues:
        print("Governance checks FAILED:")
        for i in issues:
            print(f"  - {i}")
        sys.exit(1)
    print("Governance checks passed.")
