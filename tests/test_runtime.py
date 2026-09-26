"""Runtime switches and the Databricks side of the lakehouse, tested without Spark."""
import argparse

import pytest

from careplatform import config, lakehouse, runtime


RUNTIME_VARS = ("CAREPLATFORM_LAKEHOUSE_MODE", "CAREPLATFORM_CATALOG", "CAREPLATFORM_GIT_COMMIT")


@pytest.fixture
def clean_env():
    """runtime.apply() writes os.environ directly, so undo it by hand after each test."""
    import os
    saved = {v: os.environ.pop(v, None) for v in RUNTIME_VARS}
    yield
    for v, old in saved.items():
        os.environ.pop(v, None)
        if old is not None:
            os.environ[v] = old
    config.use_config_dir(config._DEFAULT_CONFIG_DIR)   # undo any --config-dir


def _args(*argv):
    p = argparse.ArgumentParser()
    runtime.add_runtime_args(p)
    return p.parse_args(list(argv))


def test_defaults_to_local_mode(clean_env):
    runtime.apply(_args())
    assert lakehouse.mode() == "local"


def test_job_flags_switch_to_unity_catalog(clean_env):
    runtime.apply(_args("--mode", "databricks", "--catalog", "care_reg_prod"))
    assert lakehouse.mode() == "databricks"
    assert lakehouse.uc_name("bronze.raw_documents") == "care_reg_prod.bronze.raw_documents"
    assert str(lakehouse.landing_root()).replace("\\", "/") == "/Volumes/care_reg_prod/bronze/landing"


def test_config_dir_flag_reads_config_from_elsewhere(clean_env, tmp_path):
    for f in config.CONFIG_FILES:
        (tmp_path / f).write_bytes((config._DEFAULT_CONFIG_DIR / f).read_bytes())
    (tmp_path / "settings.yaml").write_text(
        (tmp_path / "settings.yaml").read_text().replace("databricks_catalog: care_reg_dev",
                                                         "databricks_catalog: from_synced_config"))
    runtime.apply(_args("--config-dir", str(tmp_path)))
    assert lakehouse.uc_catalog() == "from_synced_config"


def test_git_commit_flag_reaches_the_run_log(clean_env):
    from careplatform import runlog
    runtime.apply(_args("--git-commit", "abcdef1234567890"))
    assert runlog._git_commit() == "abcdef123456"


def test_spark_schema_comes_from_catalog():
    ddl = lakehouse.spark_ddl("ops.watermarks")
    assert ddl.startswith("source_system STRING NOT NULL")
    assert "updated_at TIMESTAMP NOT NULL" in ddl
    assert "NOT NULL" not in lakehouse.spark_ddl("ops.watermarks", constraints=False)


def test_governance_is_published_with_owner_and_classification(clean_env):
    stmts = lakehouse.governance_statements("gold.llm_call_log")
    assert stmts[0].startswith("COMMENT ON TABLE care_reg_dev.gold.llm_call_log IS ")
    tags = stmts[-1]
    assert "'classification' = 'Internal'" in tags
    assert "'data_owner' = 'AI Operations Owner'" in tags
    assert "'retention_days' = '180'" in tags
    # one COMMENT per column
    n_cols = len(lakehouse.table_spec("gold.llm_call_log")["columns"])
    assert sum("ALTER COLUMN" in s for s in stmts) == n_cols


def test_quotes_in_descriptions_are_escaped(clean_env):
    assert lakehouse._sql_str("it's") == "'it\\'s'"


def test_secret_prefers_environment(monkeypatch):
    monkeypatch.setenv("SOME_TEST_SECRET", "from-env")
    assert config.secret("SOME_TEST_SECRET") == "from-env"


def test_missing_secret_off_databricks_is_none(monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    assert config.secret("NOT_SET_ANYWHERE") is None
