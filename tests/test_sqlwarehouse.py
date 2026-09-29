"""The "sql" lakehouse mode used by the Databricks App: reads, appends with bound parameters,
and refuses everything else (ADR-013). A fake connection stands in for the SQL warehouse."""
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pytest

from careplatform import lakehouse, sqlwarehouse


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def execute(self, sql, params=None):
        self.db.statements.append((sql, params))
        self.last = sql

    def fetchall(self):                                   # SHOW TABLES ... LIKE 'x'
        table = self.last.split("LIKE '")[1].rstrip("'")
        return [("row",)] if table in self.db.tables else []

    def fetchall_arrow(self):
        return self.db.result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeWarehouse:
    def __init__(self, tables):
        self.tables, self.statements, self.result = set(tables), [], None


@pytest.fixture
def wh(monkeypatch):
    db = FakeWarehouse({"llm_call_log", "chunks"})
    monkeypatch.setenv("CAREPLATFORM_LAKEHOUSE_MODE", "sql")
    monkeypatch.setenv("CAREPLATFORM_CATALOG", "care_reg_dev")
    monkeypatch.setattr(sqlwarehouse, "_connect", lambda: FakeConnection(db))
    return db


def log_row(**kw):
    row = {"call_id": "c1", "called_at": datetime(2026, 9, 29, tzinfo=timezone.utc), "run_id": "s1",
           "purpose": "rag", "provider": "databricks", "model": "m", "prompt_version": "answer-v1",
           "data_classification": "confidential", "prompt_tokens": 10, "completion_tokens": None,
           "latency_ms": 5, "status": "ok", "error_type": None, "retry_count": 0,
           "fallback_used": False, "cost_usd": 0.0, "request_hash": "h" * 64}
    row.update(kw)
    return row


def test_read_selects_the_catalog_columns(wh):
    wh.result = pa.table({"chunk_id": ["a"]})
    lakehouse.read("silver.chunks")
    sql, _ = wh.statements[-1]
    assert sql.startswith("SELECT `chunk_id`, `unit_id`")
    assert sql.endswith("FROM `care_reg_dev`.`silver`.`chunks`")


def test_missing_table_reads_as_empty_with_the_right_columns(wh):
    df = lakehouse.read("gold.chunk_embeddings")
    assert df.empty and "vector" in df.columns


def test_append_uses_bound_parameters_not_string_values(wh):
    n = lakehouse.append("gold.llm_call_log", pd.DataFrame([log_row(), log_row(call_id="c2")]))
    sql, params = wh.statements[-1]
    assert n == 2
    assert sql.startswith("INSERT INTO `care_reg_dev`.`gold`.`llm_call_log` (`call_id`, `called_at`")
    assert ":r0_call_id" in sql and ":r1_call_id" in sql and "c2" not in sql      # values never in the SQL text
    assert params["r1_call_id"] == "c2" and params["r0_completion_tokens"] is None


def test_append_checks_the_catalog_schema_first(wh):
    with pytest.raises(ValueError, match="not in catalog"):
        lakehouse.append("gold.llm_call_log", pd.DataFrame([log_row(question="who?")]))
    assert not any(s.startswith("INSERT") for s, _ in wh.statements)


def test_app_never_creates_a_table(wh):
    with pytest.raises(RuntimeError, match="never creates tables"):
        lakehouse.append("ops.dq_results", pd.DataFrame([{"x": 1}]))


@pytest.mark.parametrize("op", ["upsert", "overwrite"])
def test_merge_and_overwrite_are_refused(wh, op):
    with pytest.raises(RuntimeError, match="not allowed in sql mode"):
        getattr(lakehouse, op)("gold.llm_call_log", pd.DataFrame([log_row()]))
    assert wh.statements == []
