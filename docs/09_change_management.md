# 09 Change Management

## 1. Branching

- `main` is always deployable. Nobody pushes to it directly.
- Work happens on short branches: `feat/bronze-ingest`, `fix/chunk-overlap`, `gov/add-source-x`.
- Every change goes through a pull request, even solo. The PR is the record of what changed and why.

## 2. What every PR must pass (CI)

Defined in `.github/workflows/ci.yml`. Deploys are in `.github/workflows/deploy.yml`.

1. `pytest`: governance checks, unit tests, and bundle consistency checks (`tests/test_bundle.py`)
2. Catalog doc is regenerated and matches the YAML
3. The wheel builds
4. `databricks bundle validate` against the workspace
5. No secrets in the diff (GitHub push protection)

## 3. PR checklist

The PR template (`.github/pull_request_template.md`) asks:

- Does this add or change a table? Catalog updated?
- Does this add or change a DQ rule? Threshold justified?
- Does this change a prompt or model? New version created, eval results attached?
- Does this add a source? Council approval recorded in `sources.yaml`?
- Any cost impact? Estimate included?

## 4. Environments

| Env | Where | Deployed by | Used for |
|-----|-------|-------------|----------|
| local | Laptop, Delta files in `data/` | You, by hand | Writing code, running tests |
| dev | Databricks, catalog `care_reg_dev` | GitHub Actions on every merge to `main` | Integration testing on real APIs and Spark |
| prod | Databricks, catalog `care_reg_prod` | GitHub Actions on a `v*` tag, after manual approval | Demo, dashboard, scheduled daily run |

Nothing is deployed to Databricks by hand. The bundle (`databricks.yml`, `resources/`) is the only way in. See [ADR-007](adr/ADR-007-databricks-bundles-cicd.md) and the [deploy runbook](runbooks/deploy_databricks.md).

Promotion to prod is a tagged release (`v0.2.0`, `v0.3.0`), created only after the eval gates in [06](06_ai_governance.md) pass.

## 5. Change types

| Type | Example | Approval |
|------|---------|----------|
| Standard | Bug fix, doc update | PR with passing CI |
| Governance | New source, classification change, new DQ rule | PR plus Council sign off noted in PR |
| Model | New prompt version or model | PR plus eval results attached |
| Emergency | Rotate a leaked key | Do it now, PR after, note in exceptions log |
