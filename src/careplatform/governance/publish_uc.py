"""Publish config/catalog.yaml into Unity Catalog.

Runs as the first task of every Databricks job. For each medallion schema it
sets layer and classification tags. For each table that already exists it
sets the table description, every column comment, and tags for layer,
domain, classification, retention, owner and steward.

So when someone browses Catalog Explorer in Databricks, they see the same
governance metadata that lives in the repo. The YAML stays the single
source of truth (ADR-003); Unity Catalog is where people read it.

Safe to rerun: every statement overwrites, nothing is appended.
"""
from __future__ import annotations

import argparse

from careplatform import config, lakehouse, runtime


def schema_tags() -> dict[str, dict[str, str]]:
    """One classification per layer: the table classification if they all agree, else 'Mixed'."""
    out: dict[str, dict[str, str]] = {}
    for layer in config.catalog()["allowed_layers"]:
        classes = {t["classification"] for t in config.catalog()["tables"] if t["layer"] == layer}
        out[layer] = {
            "layer": layer,
            "classification": classes.pop() if len(classes) == 1 else "Mixed",
        }
    return out


def run() -> dict:
    spark = lakehouse._spark()
    cat = lakehouse.uc_catalog()
    done, skipped = 0, 0
    for layer, tags in schema_tags().items():
        tag_sql = ", ".join(f"'{k}' = '{v}'" for k, v in tags.items())
        try:
            spark.sql(f"ALTER SCHEMA {cat}.{layer} SET TAGS ({tag_sql})")
            done += 1
        except Exception as e:
            print(f"[governance] schema {layer}: skipped ({type(e).__name__})")
            skipped += 1
    tables = 0
    for t in config.catalog()["tables"]:
        if lakehouse.exists(t["name"]):
            lakehouse._publish_governance(t["name"])
            tables += 1
    summary = {"catalog": cat, "schemas_tagged": done, "schemas_skipped": skipped, "tables_published": tables}
    print(summary)
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Publish catalog.yaml metadata into Unity Catalog.")
    runtime.add_runtime_args(p)
    runtime.apply(p.parse_args())
    run()


if __name__ == "__main__":
    main()
