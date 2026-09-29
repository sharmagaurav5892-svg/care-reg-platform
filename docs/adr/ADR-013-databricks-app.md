# ADR-013: The web app runs as a Databricks App with its own identity and least privilege

- **Status:** Accepted
- **Date:** 2026-09-29
- **Decider:** Platform Owner, AI Model Owner

## Context

The answer pipeline (ADR-012) worked from the terminal. People need a page where they can ask a question and see the cited answer. Checked before building (feasibility spike, 2026-09-29, app `spike-hello`, deleted afterwards):

| Check | Result |
|---|---|
| Are Databricks Apps available on Free Edition? | Yes: up to 3 apps, each stops 24 hours after start or redeploy (restart any time) |
| Does an app start and serve a page? | Yes: compute start 2 to 3 minutes, then deploy in about 4 seconds (download source, load spec, install `requirements.txt`, build, start) |
| Does the app get its own identity? | Yes: a service principal created with the app; its credentials are injected as `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` |
| Can that identity be given our model endpoints? | Yes: serving endpoint resource with **Can query** |
| Can it be given the SQL warehouse? | Yes: SQL warehouse resource with **Can use** (the only warehouse: Serverless Starter Warehouse) |
| Can it be given Unity Catalog tables? | Yes: table resource with SELECT or MODIFY (Databricks SDK `AppResourceUcSecurable`) |

An app has no Spark, so it cannot use the `databricks` lakehouse mode.

## Decision

1. **One Streamlit page, `app/app.py`,** calling the same `ask()` as the terminal. The same file runs on a laptop (local tables, CLI login) and as the app.
2. **App identity, not user identity.** The data it reads (BC law) is Public and the same for everyone, and the audit log must be written by the app, not by whoever is using it. User authorization (acting with the user's own permissions) is the pattern for data that depends on the person; it is not needed here and is noted for inspection data later.
3. **A `sql` lakehouse mode** (`sqlwarehouse.py`) through the SQL warehouse: read the catalog's columns, append with bound parameters, check existence with `SHOW TABLES` so a missing grant is not mistaken for missing data. Create, overwrite and merge are refused in code.
4. **Everything the app may touch is declared in `resources/app.yml`,** deployed by CI:

   | Resource | Permission |
   |---|---|
   | Serverless Starter Warehouse (looked up by name) | Can use |
   | Llama 3.3 70B, gpt-oss-120b, gte-large-en | Can query |
   | silver.chunks, silver.document_units, gold.chunk_embeddings, gold.llm_call_log | SELECT |
   | gold.llm_call_log, ops.api_call_log, ops.dq_results | MODIFY (append) |

   Nothing on bronze, the landing volume or any law table beyond SELECT. `tests/test_app_bundle.py` fails CI if MODIFY is granted on anything but the three log tables, or a model or warehouse permission is raised.
5. **No secrets and no ids in the repo.** Credentials are injected by Databricks; the warehouse id comes from the resource (`value_from`), found by name at deploy time.
6. **App source is the synced repo** (`source_code_path: ..`), so the app uses the same `src/` and `config/` (settings, prompts, sources) as the jobs, from the same commit. `requirements.txt` holds only what the code needs to run; test and laptop tools moved to `requirements-dev.txt` (tested).
7. **The page shows its evidence:** cited sections with legal status and a link to the official BC Laws page, the route and model, the load date of the law, and the not-legal-advice line. If the audit log cannot be written, the page says so and the error goes to the app logs.
8. **No usage data to third parties:** Streamlit's usage statistics are switched off in the app's environment.
9. **Loaded once per start.** The search index is cached per app process; the 24-hour stop and every restart reload it, so the app picks up the latest law after the daily pipeline with no manual step.

## Consequences

- Good: a single file (`resources/app.yml`) answers "what can the app read, write and call"; access review is a PR review.
- Good: the question text never leaves the session except to Databricks-hosted models; only metrics and a hash are stored (ADR-004).
- Good: the same code and prompts as the tested pipeline; the app adds no answer logic.
- Bad: on Free Edition the app stops after 24 hours and must be started before a demo.
- Bad: every question writes to three tables through the warehouse (a few seconds after the answer is shown); fine for low volume, a queue would be needed at scale.
- Confirmed on first deploy (2026-09-29): the table resources were enough; no separate USE CATALOG / USE SCHEMA grants were needed. The app wrote its first audit row to gold.llm_call_log (llama, confidential, answer-v1).
- Neutral: the app is shared only with the deploying user by default (`permissions` in `app.yml`); sharing with others is an explicit change.
