# 09 Change Management

## 1. Branching

- `main` is always deployable. Nobody pushes to it directly.
- Work happens on short branches: `feat/bronze-ingest`, `fix/chunk-overlap`, `gov/add-source-x`.
- Every change goes through a pull request, even solo. The PR is the record of what changed and why.

## 2. What every PR must pass (CI)

Defined in `.github/workflows/ci.yml`:

1. `pytest`: governance checks plus unit tests
2. Catalog doc is regenerated and matches the YAML
3. No secrets in the diff (GitHub push protection)

## 3. PR checklist

The PR template (`.github/pull_request_template.md`) asks:

- Does this add or change a table? Catalog updated?
- Does this add or change a DQ rule? Threshold justified?
- Does this change a prompt or model? New version created, eval results attached?
- Does this add a source? Council approval recorded in `sources.yaml`?
- Any cost impact? Estimate included?

## 4. Environments

| Env | Where | Data | Used for |
|-----|-------|------|----------|
| dev | Local mode, laptop | Full public dataset | Building and testing |
| prod | Fabric mode, trial capacity | Same | Demo and dashboard |

Promotion to prod is a tagged release (`v0.1.0`, `v0.2.0`), created only after the eval gates in [06](06_ai_governance.md) pass.

## 5. Change types

| Type | Example | Approval |
|------|---------|----------|
| Standard | Bug fix, doc update | PR with passing CI |
| Governance | New source, classification change, new DQ rule | PR plus Council sign off noted in PR |
| Model | New prompt version or model | PR plus eval results attached |
| Emergency | Rotate a leaked key | Do it now, PR after, note in exceptions log |
