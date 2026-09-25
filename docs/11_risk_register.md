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
| R-13 | GitHub API outage or rate limit stops a load | 2 | 1 | 2 | Retries with backoff, rate limit wait, clear failure, watermark not moved so next run catches up | Data Engineer | Open |

Reviewed at every release.
