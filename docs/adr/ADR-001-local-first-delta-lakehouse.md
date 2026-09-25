# ADR-001: Local-first Delta lakehouse, portable to Fabric

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decider:** Platform Owner

## Context

The platform should look and behave like an enterprise lakehouse, but the build budget is under $20. A Fabric F2 capacity left on costs about $263 a month. The Fabric trial is free for 60 days, but when it ends, notebooks and pipelines go inactive.

## Options

1. **Fabric only.** Most "Microsoft stack" looking. Locked to the trial window; project dies when it ends.
2. **Local files (CSV/Parquet) only.** Free, but no ACID writes, no merge, no time travel. Doesn't look like a lakehouse.
3. **Delta Lake written by the `deltalake` Python library, locally by default, to OneLake optionally.** Same table format Fabric uses natively.

## Decision

Option 3. All tables are Delta. `settings.yaml -> lakehouse.mode` switches between a local folder and a OneLake `abfss://` path. No Spark required locally.

## Consequences

- Good: $0 to run, survives the Fabric trial, same tables open in Fabric, Databricks or DuckDB.
- Good: demonstrates the idea that storage format matters more than the engine.
- Bad: no Spark scale. Fine for thousands of documents, not millions. If scale is ever needed, the same Delta tables can be processed with Spark in Fabric.
- Bad: Fabric's built-in lineage view only sees what runs in Fabric. Covered by our own record-level lineage (docs/05).
