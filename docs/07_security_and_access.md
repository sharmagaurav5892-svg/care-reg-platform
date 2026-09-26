# 07 Security and Access

## 1. Secrets

| Secret | Local mode | Fabric mode |
|--------|-----------|-------------|
| Azure OpenAI key | `.env` (git-ignored), exception EX-001 | Azure Key Vault, read via managed identity |
| Neo4j password | `.env` | Azure Key Vault |
| Fallback provider key | `.env` | Azure Key Vault |
| GitHub token | `.env` | Databricks secret scope `care-reg` (key `github-token`) |
| Databricks deploy token | Never on the laptop | GitHub Actions secret `DATABRICKS_TOKEN` only (EX-002) |

Rules:

- No secret in code, config YAML, notebooks or commit history.
- GitHub secret scanning and push protection turned on for the repo.
- Keys rotated at the end of the build, and any time one might have leaked.
- The GitHub token is a fine-grained personal access token: one repo only, Contents read-only, 90 day expiry. A classic token with full `repo` scope is not allowed.
- Where a service supports keyless auth (Entra ID for Azure OpenAI and OneLake), use that in Fabric mode instead of keys.

## 2. Roles and access

Least privilege: each role gets the smallest access that lets it do its job.

| Role | Landing / Bronze | Silver | Gold | Ops | Neo4j | Azure OpenAI |
|------|:-:|:-:|:-:|:-:|:-:|:-:|
| Data Engineer | Read / write | Read / write | Read / write | Read / write | Admin | Call |
| Data Steward | Read | Read | Read | Read | Read | None |
| AI Model Owner | None | Read | Read / write | Read | Read / write | Call |
| Evaluator | None | Read | Read / write (eval tables only) | Read | Read | Call |
| App service identity | None | None | Read | Write (llm_call_log only) | Read | Call via gateway only |
| Dashboard viewer | None | None | Read (aggregates) | Read | None | None |

In Fabric mode these map to workspace roles (Admin, Member, Contributor, Viewer) plus OneLake data access roles on folders. In local mode the separation is documented only (single user).

## 3. Network and service settings

- Neo4j AuraDB uses encrypted `neo4j+s://` connections only.
- Azure OpenAI resource: key auth disabled once Entra ID auth is working in Fabric mode.
- The Streamlit app runs locally for the demo. If deployed, it goes behind authentication.

## 4. Audit

| Event | Where recorded |
|-------|----------------|
| Every pipeline run | `ops.run_log` |
| Every external API call and retry | `ops.api_call_log` |
| Every model call | `gold.llm_call_log` |
| Every config change | Git history and PRs |
| Every deploy | GitHub Actions run history (who, which commit, which target, who approved prod) |
| Every job run in Databricks | Job run history, and `ops.run_log.git_commit` |
| Access changes | Fabric admin audit log (Fabric mode) |
