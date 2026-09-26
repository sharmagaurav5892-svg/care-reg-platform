"""Loads settings and governance config.

Everything the pipelines need to know comes through here, so there is exactly
one place that reads config files. That also lets us hash the config for
ops.run_log.config_hash: if two runs have the same hash, they ran with the
same rules.

Where the config comes from:
  - On your laptop: the repo's config/ folder.
  - On Databricks: the bundle syncs config/ into the workspace, and the job
    passes --config-dir pointing at it (see careplatform/runtime.py).
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

import yaml

# repo root = two levels up from src/careplatform/ (only meaningful when running from the repo)
REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

CONFIG_FILES = ["settings.yaml", "catalog.yaml", "dq_rules.yaml", "sources.yaml"]

_config_dir_override: Path | None = None


def config_dir() -> Path:
    if _config_dir_override is not None:
        return _config_dir_override
    env = os.getenv("CAREPLATFORM_CONFIG_DIR")
    return Path(env) if env else _DEFAULT_CONFIG_DIR


def use_config_dir(path: str | Path) -> None:
    """Point every config read at another folder, and drop anything already cached."""
    global _config_dir_override
    _config_dir_override = Path(path)
    for fn in (settings, catalog, dq_rules, sources):
        fn.cache_clear()


def _load_yaml(name: str) -> dict:
    with open(config_dir() / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def settings() -> dict:
    return _load_yaml("settings.yaml")


@lru_cache
def catalog() -> dict:
    return _load_yaml("catalog.yaml")


@lru_cache
def dq_rules() -> dict:
    return _load_yaml("dq_rules.yaml")


@lru_cache
def sources() -> dict:
    return _load_yaml("sources.yaml")


def config_hash() -> str:
    """SHA-256 over all governance config files, in a fixed order.

    Written to ops.run_log so any table row can be tied to the exact rules
    that were in force when it was produced.
    """
    h = hashlib.sha256()
    for name in CONFIG_FILES:
        h.update(name.encode())
        h.update((config_dir() / name).read_bytes())
    return h.hexdigest()[:16]


def load_env() -> None:
    """Load .env if present. Secrets only ever come from the environment or a secret store."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


def secret(name: str, scope: str = "care-reg") -> str | None:
    """Get a secret: environment first (laptop, CI), then the Databricks secret scope.

    On Databricks the key is the lower-case, dashed version of the name,
    e.g. GITHUB_TOKEN -> scope 'care-reg', key 'github-token'.
    """
    load_env()
    value = os.getenv(name)
    if value:
        return value
    try:
        from databricks.sdk.runtime import dbutils  # only exists on Databricks
        return dbutils.secrets.get(scope=scope, key=name.lower().replace("_", "-"))
    except Exception:
        return None


def require_env(name: str) -> str:
    value = secret(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in "
            f"(or add it to the Databricks secret scope 'care-reg')."
        )
    return value
