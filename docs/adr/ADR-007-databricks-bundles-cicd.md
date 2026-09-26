# ADR-007: Run on Databricks, deployed by Asset Bundles through GitHub Actions

- **Status:** Accepted
- **Date:** 2026-09-25
- **Decider:** Platform Owner

## Context

The platform ran only on a laptop. The goal is to run it the way a company would: on a cloud lakehouse, deployed from Git by CI/CD, with dev and prod kept apart, for as close to $0 as possible.

We tried the Microsoft Fabric trial first. Microsoft declined it for this tenant ("A Fabric trial isn't available for your account"), which is common for new tenants. A pay-as-you-go Fabric F2 capacity works but costs about $0.36 to $0.40 per hour while running.

## Options

1. **Fabric F2, pay as you go** for the whole build. Most Microsoft-aligned, roughly $10 to $15 if paused carefully, and about $9 a day if forgotten.
2. **Databricks Free Edition** for the build. $0, no card. Real Unity Catalog, serverless jobs, Delta, Asset Bundles.
3. **Azure Databricks** through the Azure portal. The enterprise version, but it bills for compute and Azure VMs.

## Decision

Option 2 for the build, plus a short Fabric F2 deployment at the end (a few hours, under $5) to prove the same Delta tables and package run there too.

Deployment is **Databricks Asset Bundles**, driven only by GitHub Actions:

| Event | What happens |
|---|---|
| Pull request | `ci`: tests, governance checks, wheel builds, `bundle validate` |
| Merge to `main` | `deploy`: tests, then `bundle deploy -t dev` into `care_reg_dev` |
| Tag `v*` | `deploy`: tests, **manual approval**, then `bundle deploy -t prod` into `care_reg_prod` |

Everything in Databricks (schemas, volume, jobs, the wheel) comes from the bundle. The only manual step is creating the two catalogs once (`infra/bootstrap_catalogs.sql`), which in a company is the platform team's job.

The same code runs in both places. `runtime.py` flags tell it where it is; `lakehouse.py` writes local Delta files on a laptop and Unity Catalog tables on Databricks.

## Consequences

- Good: real CI/CD. Nothing reaches Databricks except through a merged, tested commit. Prod needs a person to approve.
- Good: `catalog.yaml` is now published into Unity Catalog (table and column comments, classification and owner tags) on every job run. The YAML is still the source of truth (ADR-003); Unity Catalog is where people browse it.
- Good: every run records the deployed commit (`--git-commit ${bundle.git.commit}`), so lineage reaches from a row back to the exact code.
- Bad: **one workspace, two catalogs.** A company would use separate dev and prod workspaces. The bundle is written so only the `host` would change per target.
- Bad: **personal access token instead of a service principal with OIDC.** Free Edition doesn't support the keyless setup. Recorded as exception EX-002 with an expiry.
- Bad: Free Edition has daily compute quotas and blocks outbound internet until identity is verified. A quota breach stops the workspace for the day; jobs are small and run once daily.
- Bad: Spark code paths (`lakehouse.py` databricks mode) can't be unit tested in CI without a Spark cluster. They are covered by the dev deployment and its first job run: integration testing, as in a company.
