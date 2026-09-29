"""The app's bundle definition must stay least privilege (ADR-013). Runs in CI, no Databricks needed."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = yaml.safe_load((ROOT / "resources" / "app.yml").read_text(encoding="utf-8"))["resources"]["apps"]["care_reg_app"]
LOGS = {"gold.llm_call_log", "ops.api_call_log", "ops.dq_results"}


def resources(kind):
    return [r[kind] for r in APP["resources"] if kind in r]


def env():
    return {e["name"]: e for e in APP["config"]["env"]}


def test_app_runs_the_streamlit_page_in_sql_mode():
    assert APP["config"]["command"] == ["streamlit", "run", "app/app.py"]
    assert (ROOT / "app" / "app.py").exists()
    assert env()["CAREPLATFORM_LAKEHOUSE_MODE"]["value"] == "sql"
    assert env()["CAREPLATFORM_CATALOG"]["value"] == "${var.catalog}"


def test_warehouse_id_comes_from_the_resource_not_the_repo():
    assert env()["DATABRICKS_WAREHOUSE_ID"] == {"name": "DATABRICKS_WAREHOUSE_ID", "value_from": "sql-warehouse"}
    assert [w["permission"] for w in resources("sql_warehouse")] == ["CAN_USE"]


def test_models_can_only_be_queried():
    endpoints = {e["name"]: e["permission"] for e in resources("serving_endpoint")}
    assert set(endpoints.values()) == {"CAN_QUERY"}
    assert "databricks-gte-large-en" in endpoints            # must match the embedding model of the vectors


def test_data_access_is_select_or_append_only_logs():
    for s in resources("uc_securable"):
        table = s["securable_full_name"].replace("${var.catalog}.", "")
        assert s["securable_type"] == "TABLE"
        assert s["permission"] in ("SELECT", "MODIFY")
        if s["permission"] == "MODIFY":
            assert table in LOGS, f"the app may only write to log tables, not {table}"


def test_no_usage_stats_to_third_parties():
    assert env()["STREAMLIT_BROWSER_GATHER_USAGE_STATS"]["value"] == "false"
