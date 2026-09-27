# 11 Risk Register

Likelihood and impact on a 1 (low) to 3 (high) scale. Score = likelihood x impact.

| ID | Risk | L | I | Score | Controls | Owner | Status |
|----|------|:-:|:-:|:-:|----------|-------|--------|
| R-01 | Personal information from inspection reports ends up in answers | 2 | 3 | 6 | PII scan on every chunk (DQ-S-006), flagged chunks excluded from gold, incident runbook | Data Steward | Open |
| R-02 | App gives a confident wrong answer about a regulation | 3 | 2 | 6 | Mandatory citations, refusal when nothing found, not-legal-advice notice, eval gate on judge score and faithfulness | AI Model Owner | Open |
| R-03 | API key leaked through git | 1 | 3 | 3 | `.gitignore`, `.env.example` only, GitHub push protection, key rotation runbook | Data Engineer | Open |
| R-04 | Runaway cost from retry loop or large reprocessing | 2 | 2 | 4 | Per-run cost cap, budget alerts, token caps, incremental processing | Platform Owner | Open |
| R-05 | Regulations amended after load, app answers from old text | 2 | 2 | 4 | App shows data load date, source versioning in bronze, scheduled reload | Regulations Data Owner | Open |
| R-06 | Source terms of use do not allow redistribution | 1 | 2 | 2 | Raw files never committed, `license_note` per source, Council approval | Governance Council | Open |
| R-07 | LLM extraction drifts outside the graph schema | 2 | 2 | 4 | Allowed value rules DQ-G-003 and DQ-G-005 are critical, prompt versioning | AI Model Owner | Open |
| R-08 | Judge model is biased or lenient, quality looks better than it is | 2 | 2 | 4 | Human calibration sample, agreement threshold, judge prompt versioned | Evaluator | Open |
| R-09 | Fabric trial ends and prod notebooks go inactive | 3 | 1 | 3 | Code lives in git, local mode works without Fabric, tables are open Delta | Platform Owner | Open |
| R-10 | Neo4j free instance paused or deleted when idle | 3 | 1 | 3 | Graph can be fully rebuilt from gold in one command | Data Engineer | Open |
| R-11 | Single person holds every role, no real separation of duties | 3 | 1 | 3 | Accepted for portfolio build. Roles documented so they can be split. | Platform Owner | Accepted |
| R-12 | GitHub token leaked or over-scoped | 1 | 2 | 2 | Fine-grained token, one repo, read-only, 90 day expiry, never logged (I-8), key rotation runbook | Data Engineer | Open |
| R-13 | GitHub or BC Laws API outage or rate limit stops a load | 2 | 1 | 2 | Retries with backoff, rate limit wait, clear failure, watermark not moved so next run catches up | Data Engineer | Open |
| R-14 | BC Laws API changes its URL scheme or format | 1 | 2 | 2 | Paths in settings.yaml not code, XML to HTML fallback, clear failure, BC and Ontario run separately | Data Engineer | Open |
| R-15 | Mixing up BC and Ontario rules in answers | 2 | 3 | 6 | jurisdiction on every row (DQ-B-007), jurisdiction on graph entities (step 4), eval questions per province | AI Model Owner | Open |
| R-16 | Databricks deploy token leaked or expires unnoticed | 1 | 2 | 2 | GitHub secret only, 90 day lifetime, EX-002 expiry, deploy fails loudly on 401 | Platform Owner | Open |
| R-17 | Free Edition quota shuts compute for the day | 2 | 1 | 2 | One small daily job, `max_concurrent_runs: 1`, laptop mode still works | Data Engineer | Open |
| R-18 | Change reaches prod without review | 1 | 3 | 3 | Prod only from tags, GitHub `prod` environment requires approval, branch protection on main | Platform Owner | Open |
| R-20 | Shared model endpoint rate limit (HTTP 429) slows or fails the embedding step | 2 | 1 | 2 | Batches of 8, pause between calls, 429 retried with backoff, every attempt in ops.api_call_log; provisioned throughput is the paid fix | Data Engineer | Open |
| R-21 | Embedding model changed or retired by the platform, old and new vectors mixed | 1 | 3 | 3 | Model name on every vector, DQ-G-002 checks length, change = re-embed everything in one PR (ADR-010) | AI Model Owner | Open |
Reviewed at every release.
