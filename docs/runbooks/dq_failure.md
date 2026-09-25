# Runbook: Critical data quality failure

**Trigger:** A pipeline run ends with `status = FAILED` and at least one critical rule with `status = fail` in `ops.dq_results`.

## 1. Find what failed

```sql
SELECT rule_id, table_name, observed_value, threshold, failed_rows
FROM ops.dq_results
WHERE run_id = :run_id AND status = 'fail'
ORDER BY severity;
```

## 2. Decide what kind of problem it is

| Looks like | Likely cause | Fix |
|------------|--------------|-----|
| DQ-B-001 duplicate doc_id | Same file downloaded twice with a new name | Nothing to fix; the dedupe logic should have skipped it. Check the ingest code. |
| DQ-S-002 empty chunks | Chunker produced a trailing empty chunk | Fix chunker, add a unit test |
| DQ-S-004 orphan chunks | Silver built against an old bronze | Rebuild silver |
| DQ-G-003 / DQ-G-005 schema violation | LLM invented a type | Tighten the prompt, create a new prompt version, rerun extraction |
| DQ-G-001 missing embeddings | API errors mid run | Check `gold.llm_call_log` for errors, rerun (incremental) |

## 3. Fix and rerun

- Staging data from the failed run is kept. Inspect it before deleting.
- Fix goes through a PR (docs/09).
- Rerun. The new run gets a new `run_id`; the failed one stays in the log as evidence.

## 4. If the threshold is wrong, not the data

Change the threshold in `config/dq_rules.yaml` through a PR with a one line reason. The Data Owner approves.
