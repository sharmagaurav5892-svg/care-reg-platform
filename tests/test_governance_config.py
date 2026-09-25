"""Governance checks. These run in CI on every push and must pass before merge.

If one of these fails, the fix is almost always in config/, not in this file.
"""
import copy

from careplatform import config
from careplatform.governance import render_catalog, validate


def test_catalog_is_complete():
    assert validate.check_catalog() == []


def test_dq_rules_are_valid():
    assert validate.check_dq_rules() == []


def test_sources_are_valid():
    assert validate.check_sources() == []


def test_catalog_doc_is_up_to_date():
    """docs/03 must match what the YAML would generate. Stops hand edits."""
    current = render_catalog.OUT.read_text(encoding="utf-8")
    assert current == render_catalog.render(), (
        "docs/03_data_catalog.md is stale. "
        "Run: python -m careplatform.governance.render_catalog"
    )


# --- the checks themselves must catch problems, not just pass ---

def test_missing_owner_domain_is_caught():
    cat = copy.deepcopy(config.catalog())
    cat["tables"][0]["domain"] = "nobody"
    assert any("has no owner" in p for p in validate.check_catalog(cat))


def test_bad_classification_is_caught():
    cat = copy.deepcopy(config.catalog())
    cat["tables"][0]["classification"] = "Secret"
    assert any("classification" in p for p in validate.check_catalog(cat))


def test_missing_load_id_is_caught():
    cat = copy.deepcopy(config.catalog())
    t = next(t for t in cat["tables"] if t["name"] == "silver.chunks")
    t["columns"] = [c for c in t["columns"] if c["name"] != "load_id"]
    assert any("load_id" in p for p in validate.check_catalog(cat))


def test_rule_on_unknown_table_is_caught():
    rules = copy.deepcopy(config.dq_rules())
    rules["rules"][0]["table"] = "silver.does_not_exist"
    assert any("not in the catalog" in p for p in validate.check_dq_rules(rules))


def test_non_public_source_is_caught():
    src = copy.deepcopy(config.sources())
    src["sources"][0]["classification"] = "Confidential"
    assert any("only Public" in p for p in validate.check_sources(src))


def test_config_hash_is_stable():
    assert config.config_hash() == config.config_hash()
    assert len(config.config_hash()) == 16


def test_yaml_boolean_trap_is_caught():
    """jurisdiction: ON (unquoted) loads as True. The validator must reject it."""
    src = copy.deepcopy(config.sources())
    src["sources"][0]["jurisdiction"] = True
    assert any("must be a string" in p for p in validate.check_sources(src))
