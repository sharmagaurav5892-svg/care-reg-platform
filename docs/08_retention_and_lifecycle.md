# 08 Retention and Lifecycle

Retention periods are set per table in [`config/catalog.yaml`](../config/catalog.yaml) and listed in the [catalog](03_data_catalog.md). This doc explains the reasoning.

## 1. Retention by layer

| Layer | Retention | Why |
|-------|-----------|-----|
| Landing files | Same as bronze | Needed to rebuild everything |
| Bronze | 5 years | Legal texts change. Keeping old versions lets us answer "what did the rule say on date X". |
| Silver | 1 year | Rebuildable from bronze, so no reason to keep longer |
| Gold (content) | 1 year | Rebuildable |
| Gold (eval) | 5 years | Quality history is evidence; losing it means you can't show improvement over time |
| `gold.llm_call_log` | 180 days | Operational metrics. Long enough for trend and cost analysis. |
| Ops | 5 years | Audit trail |

## 2. How deletion works

- Delta tables: rows past retention are deleted by a scheduled cleanup job, then `VACUUM` removes old files. Vacuum retention is 7 days so recent mistakes can still be undone with Delta time travel.
- Every cleanup run writes to `ops.run_log` with rows deleted.
- Neo4j: a full graph rebuild from gold replaces old content.

## 3. Source versioning

When a regulation is amended, the new file gets a new `doc_id` (different hash). Both versions stay in bronze. Silver and gold only use the latest version per `source_id` unless a query asks for a date.

## 4. Decommissioning

If the platform is shut down:

1. Export `ops` and eval tables as evidence
2. Delete Azure OpenAI and Neo4j resources
3. Rotate and then delete all keys
4. Delete lakehouse data
5. Record the decommission in `ops.run_log` export
