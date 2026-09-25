# ADR-003: Catalog and data quality rules as code

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decider:** Platform Owner

## Context

Governance documents drift. A catalog in a wiki is out of date within weeks, and nothing stops a new table from going live without an owner. Tools like Microsoft Purview solve this at enterprise scale, but cost money and are overkill for this build.

## Decision

- `config/catalog.yaml` is the catalog. `docs/03_data_catalog.md` is generated from it.
- `config/dq_rules.yaml` holds every rule. The pipeline reads it; the doc lists it.
- `config/sources.yaml` is the approved source register.
- `tests/test_governance_config.py` runs in CI and blocks a merge if a table has no owner, no classification, no retention, no `load_id`, no DQ rule, or if the generated doc is stale.

## Consequences

- Good: governance can't silently drift, because the build breaks.
- Good: the same YAML could be pushed into Purview or Unity Catalog later through their APIs. This is the pattern: define once, publish anywhere.
- Bad: people have to edit YAML, not a nice UI. Acceptable for a technical team.
