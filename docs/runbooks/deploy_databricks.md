# Runbook: Set up and run Databricks deployment

One-time setup, then how deploys work day to day. See ADR-007 for why it's built this way.

## One-time setup

### 1. Workspace
- Databricks Free Edition workspace, signed in with a personal account.
- **Verify identity** (top right, LinkedIn). Without it, outbound internet is blocked and the BC Laws API can't be reached.

### 2. Catalogs (the only manual step)
SQL Editor → Serverless Starter Warehouse → run `infra/bootstrap_catalogs.sql`.

If `CREATE CATALOG` is not permitted on your tier, stop and record it here; the fallback is to set both targets' `catalog` variable to `workspace`.

### 3. Deploy token
Profile → Settings → Developer → Access tokens → Generate new token.
- Comment `github-actions-deploy`, lifetime **90 days** (exception EX-002 expires with it).
- Never paste it into files, chat or commits.

### 4. GitHub secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:
- `DATABRICKS_HOST`: the workspace URL, e.g. `https://dbc-xxxx.cloud.databricks.com`
- `DATABRICKS_TOKEN`: the token from step 3

### 5. GitHub environments
Repo → Settings → Environments:
- **New environment** `dev`. No rules.
- **New environment** `prod`. Tick **Required reviewers**, add yourself. Deploys to prod now wait for your approval.

### 6. Branch protection (so CI actually blocks bad changes)
Repo → Settings → Branches → Add rule for `main`:
- Require a pull request before merging
- Require status checks to pass: `governance-and-tests`

## Day to day

| You do | What runs |
|---|---|
| Push a branch, open a pull request | `ci`: tests, wheel build, bundle validate |
| Merge the pull request | `deploy`: tests, deploy to `care_reg_dev` |
| `git tag v0.2.0 && git push --tags` | `deploy`: tests, waits for approval, deploy to `care_reg_prod` |

After a deploy, in Databricks: **Jobs & Pipelines → care-reg bronze ingest (dev) → Run now**.

## Checking a run

- Job run page: each task's output and errors.
- SQL Editor:
  ```sql
  SELECT pipeline, status, rows_out, git_commit, started_at
  FROM care_reg_dev.ops.run_log ORDER BY started_at DESC;

  SELECT connector, status_code, count(*) FROM care_reg_dev.ops.api_call_log GROUP BY ALL;

  SELECT source_id, source_ref, file_name, ingested_at FROM care_reg_dev.bronze.raw_documents;
  ```
- Catalog Explorer → `care_reg_dev` → any table: description, column comments and tags come from `config/catalog.yaml`.

## Adding Ontario (GitHub connector) on Databricks

Needs the GitHub token in a Databricks secret scope (not in the job, not in the repo):
```
databricks secrets create-scope care-reg
databricks secrets put-secret care-reg github-token
databricks secrets put-secret care-reg github-owner
```
Then add a `bronze_github` task to `resources/jobs.yml` with `--connector github`, in a pull request.

## When a deploy fails

| Error mentions | Likely cause | Fix |
|---|---|---|
| `401` / `invalid access token` | Token expired or wrong | New token (step 3), update the GitHub secret |
| `catalog ... does not exist` | Bootstrap not run | Step 2 |
| `already exists` on a schema | Schema was created by hand before the bundle | Drop it (empty) or `databricks bundle deployment bind` it |
| Job fails reaching `bclaws.gov.bc.ca` | Identity not verified | Step 1 |
| Workspace compute unavailable | Free Edition daily quota hit | Wait until tomorrow |
   | `numpy.core.multiarray failed to import` (job task) | The wheel listed pandas/pyarrow/numpy, so pip upgraded one inside the job and broke the preinstalled set | Keep them out of `pyproject.toml` dependencies (enforced by `test_wheel_does_not_reinstall_runtime_libraries`) |
