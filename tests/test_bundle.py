"""The Databricks bundle must agree with the code and the catalog.

These run in CI with no Databricks connection. They catch the mistakes that
would otherwise only show up as a failed job in the workspace.
"""
import importlib
import re
import tomllib
from pathlib import Path

import yaml

from careplatform import config

ROOT = Path(__file__).resolve().parents[1]


def _load(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def _resources():
    merged: dict = {}
    for f in sorted((ROOT / "resources").glob("*.yml")):
        for kind, items in (yaml.safe_load(f.read_text(encoding="utf-8"))["resources"]).items():
            merged.setdefault(kind, {}).update(items)
    return merged


def _entry_points():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def test_bundle_includes_resources_folder():
    assert "resources/*.yml" in _load("databricks.yml")["include"]


def test_schemas_match_catalog_layers():
    schemas = {s["name"] for s in _resources()["schemas"].values()}
    assert schemas == set(config.catalog()["allowed_layers"])


def test_every_schema_uses_the_catalog_variable():
    for s in _resources()["schemas"].values():
        assert s["catalog_name"] == "${var.catalog}"


def test_landing_volume_is_in_bronze():
    v = _resources()["volumes"]["landing"]
    assert v["schema_name"] == "${resources.schemas.bronze.name}" and v["name"] == "landing"


def test_every_wheel_task_points_at_a_real_function():
    eps = _entry_points()
    for job in _resources()["jobs"].values():
        for task in job["tasks"]:
            wt = task["python_wheel_task"]
            assert wt["package_name"] == "careplatform"
            target = eps[wt["entry_point"]]                   # KeyError = entry point missing
            module, func = target.split(":")
            assert callable(getattr(importlib.import_module(module), func))


def test_every_task_runs_in_databricks_mode_with_the_target_catalog():
    for job in _resources()["jobs"].values():
        for task in job["tasks"]:
            params = task["python_wheel_task"]["parameters"]
            assert params[params.index("--mode") + 1] == "databricks"
            assert params[params.index("--catalog") + 1] == "${var.catalog}"
            assert "--config-dir" in params and "--git-commit" in params


def test_bronze_job_never_runs_twice_at_once():
    assert _resources()["jobs"]["bronze_ingest"]["max_concurrent_runs"] == 1


def test_only_prod_is_scheduled():
    job = _resources()["jobs"]["bronze_ingest"]
    assert job["schedule"]["pause_status"] == "PAUSED"
    prod = _load("databricks.yml")["targets"]["prod"]
    assert prod["resources"]["jobs"]["bronze_ingest"]["schedule"]["pause_status"] == "UNPAUSED"


def test_target_catalogs_are_bootstrapped():
    targets = _load("databricks.yml")["targets"]
    sql = (ROOT / "infra" / "bootstrap_catalogs.sql").read_text()
    created = set(re.findall(r"CREATE CATALOG IF NOT EXISTS (\w+)", sql))
    for name, t in targets.items():
        assert t["variables"]["catalog"] in created, f"target {name} catalog not in bootstrap"
