"""Loads settings and governance config.

Everything the pipelines need to know comes through here, so there is exactly
one place that reads config files. That also lets us hash the config for
ops.run_log.config_hash: if two runs have the same hash, they ran with the
same rules.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

import yaml

# repo root = two levels up from src/careplatform/
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

CONFIG_FILES = ["settings.yaml", "catalog.yaml", "dq_rules.yaml", "sources.yaml"]


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
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
        h.update((CONFIG_DIR / name).read_bytes())
    return h.hexdigest()[:16]


def load_env() -> None:
    """Load .env if present. Secrets only ever come from the environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value
