# 04 Data Quality Framework

## 1. Why

A RAG system fails quietly. If half a PDF didn't extract, nothing crashes; the app just gives worse answers and nobody knows why. Data quality checks are how we find out before a user does.

## 2. Dimensions

| Dimension | Question it answers | Example rule |
|-----------|---------------------|--------------|
| Completeness | Is anything missing? | Every non-PII chunk has an embedding (DQ-G-001) |
| Validity | Is the value allowed? | Entity type is in the schema (DQ-G-003) |
| Uniqueness | Is anything duplicated? | Same file never registered twice (DQ-B-001) |
| Consistency | Do tables agree with each other? | Every chunk points to a real file (DQ-S-004) |
| Timeliness | Is it stuck or late? | No run in RUNNING over 2 hours (DQ-O-001) |
| Accuracy | Is it right? | Measured through the eval harness, not row checks (see [06](06_ai_governance.md)) |

## 3. Severity

| Severity | What happens | Who is told |
|----------|--------------|-------------|
| **critical** | Load stops. Staging data is kept for debugging. Nothing is promoted. `ops.run_log.status = FAILED`. | Data Steward and Data Engineer |
| **warning** | Load continues. Result written to `ops.dq_results`. | Data Steward, via dashboard |

A rule starts as a warning when we are not sure of the right threshold. After a few runs of real data the Data Steward either promotes it to critical or tunes the threshold. That change goes through a PR like any other code.

## 4. Where rules live

All rules are in [`config/dq_rules.yaml`](../config/dq_rules.yaml). The full list per table is in the generated [data catalog](03_data_catalog.md). Rule IDs follow `DQ-<layer>-<number>`:

- `B` bronze, `S` silver, `G` gold, `O` ops

Rule IDs are never reused. A retired rule is deleted from the YAML, and its history stays in `ops.dq_results`.

## 5. How a check runs

1. Pipeline writes its output to a staging table.
2. DQ engine loads every rule for that table from the YAML.
3. Each rule produces one row in `ops.dq_results` with the observed value, threshold, and failed row count.
4. If any critical rule failed, the run stops. Otherwise staging is promoted.

## 6. Scorecard

The Power BI dashboard (step 10) shows, per table:

- Pass rate over the last 30 runs
- Current failing rules
- Trend of each observed value against its threshold

## 7. Handling a failure

See [runbooks/dq_failure.md](runbooks/dq_failure.md).

## 8. Governance checks on the rules themselves

`tests/test_governance_config.py` makes sure:

- every rule points to a table and column that exist in the catalog
- every foreign key rule points to a real table and column
- every catalog table has at least one rule
- rule IDs are unique and severities and dimensions are from the allowed lists
