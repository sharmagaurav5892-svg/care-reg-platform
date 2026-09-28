# Runbook: rebuild silver after a schema change

## When to use this

A change adds or changes columns on a **silver** table (for example `in_force` on `silver.document_units`, 2026-09-27). Existing Delta tables don't have the new column, so the next merge would fail with a schema mismatch.

## Why a rebuild is acceptable here

Silver is **fully derivable from bronze** (the medallion rule: bronze is the source of truth, everything above it can be rebuilt). Rebuilding re-parses the same bronze files and produces the same `unit_id`s and `chunk_id`s for unchanged text, so gold does **not** re-embed them.

What a rebuild loses: silver's own history (`valid_from` dates, retired chunks) before the rebuild. Fine in dev with days of history. **Not acceptable in prod once history matters**: there, use a migration instead (`ALTER TABLE ... ADD COLUMNS`, backfill, then deploy), reviewed in a PR. A proper migration step is on the hardening list (ADR-009, consequences).

## Steps (dev)

1. Merge the PR and wait for **deploy** to be green. Don't run the job in between; if the schedule fires first, silver fails before writing anything and the rebuild below still works.
2. SQL Editor:

   ```sql
   DROP TABLE IF EXISTS care_reg_dev.silver.chunks;
   DROP TABLE IF EXISTS care_reg_dev.silver.cross_references;
   DROP TABLE IF EXISTS care_reg_dev.silver.document_units;
   DELETE FROM care_reg_dev.ops.watermarks WHERE source_system = 'silver';
   ```

   Deleting the silver watermarks makes silver re-parse every law on the next run.
3. Actions > fetch-bclaws > Run workflow.
4. Check: `silver:bclaws` SUCCEEDED; `gold_embeddings` shows `embedded` only for chunks whose text changed and `retired` for chunks that are no longer wanted.

## Steps (laptop)

```powershell
Remove-Item -Recurse -Force data\lakehouse\silver
python -m careplatform.show --sql "SELECT count(*) FROM ops_watermarks WHERE source_system = 'silver'"
```

Local watermarks live in `data/lakehouse/ops/watermarks`; the simplest reset is to delete `data\lakehouse\ops\watermarks` too (bronze and gold watermarks are rebuilt from their own checks).
