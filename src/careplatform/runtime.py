"""Command-line switches every pipeline shares.

The same code runs on your laptop and as a Databricks job. The job passes
these flags (see resources/jobs.yml) so the code knows where it is:

    --mode databricks         write to Unity Catalog instead of local Delta files
    --catalog care_reg_dev    which Unity Catalog catalog (dev or prod)
    --config-dir <path>       where the bundle synced config/ to
    --git-commit <sha>        the commit that was deployed, for ops.run_log

On your laptop you pass none of them and everything defaults to local mode.
"""
from __future__ import annotations

import argparse
import os

from careplatform import config


def add_runtime_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("runtime")
    g.add_argument("--mode", choices=["local", "databricks", "fabric", "sql"],
                   help="where tables live (default: settings.yaml lakehouse.mode)")
    g.add_argument("--catalog", help="Unity Catalog catalog in databricks mode")
    g.add_argument("--config-dir", help="folder holding settings.yaml, catalog.yaml, ...")
    g.add_argument("--git-commit", help="deployed commit, recorded in ops.run_log")


def apply(args: argparse.Namespace) -> None:
    """Must run before anything reads config."""
    if args.config_dir:
        config.use_config_dir(args.config_dir)
    if args.mode:
        os.environ["CAREPLATFORM_LAKEHOUSE_MODE"] = args.mode
    if args.catalog:
        os.environ["CAREPLATFORM_CATALOG"] = args.catalog
    if args.git_commit:
        os.environ["CAREPLATFORM_GIT_COMMIT"] = args.git_commit
