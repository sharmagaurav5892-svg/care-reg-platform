# 02 Data Classification and Handling

## 1. Classification levels

| Level | Meaning | Examples on this platform |
|-------|---------|---------------------------|
| **Public** | Already published by its owner. No harm if shared. | Regulation text, public inspection reports, chunks and entities derived from them |
| **Internal** | Created by the platform. Low harm if shared, but not meant for publishing as-is. | Run logs, DQ results, eval results, LLM call metrics |
| **Confidential** | Could identify a person or reveal something they typed. | Raw user questions typed into the app, API keys |
| **Restricted** | Personal health information, resident or employee data. | **Not allowed on this platform at all** |

Every table in `config/catalog.yaml` must carry one of these levels. The test suite fails if a table is missing one or uses a level not in this list.

## 2. Handling rules

| Rule | Public | Internal | Confidential |
|------|:-:|:-:|:-:|
| Can be committed to git | Derived samples only | No | Never |
| Can be sent to an external LLM | Yes | Yes | Only after redaction |
| Can appear in the Power BI dashboard | Yes | Yes | No, aggregated only |
| Can be shared in the public README | Yes | Aggregates only | No |
| Encryption at rest | Platform default | Platform default | Platform default plus Key Vault for secrets |

## 3. What can be sent to an LLM

Sending text to Azure OpenAI is a data transfer to a third party processor, so it is controlled.

- Allowed: source document text (Public), eval questions written by the Evaluator (Internal).
- Allowed after redaction: questions typed by app users. The gateway runs a redaction step (emails, phone numbers, names after "resident", health card patterns) before the call.
- Never allowed: anything Restricted. Since Restricted data is banned from the platform, this should never come up. If it does, it is an incident. See [runbooks](runbooks/).

Azure OpenAI deployments used here are configured with the default abuse monitoring retention. That fact is recorded in the model inventory in [06 AI governance](06_ai_governance.md).

## 4. Source licensing

Public does not mean free to redistribute. Each source in `config/sources.yaml` carries:

- `license_note`: what the terms of use say
- `redistribute_raw`: whether raw files may be republished (default `false`)

Raw source files stay in `data/landing/`, which is git-ignored. Only code, config, and small derived samples go into the repo.

## 5. Personal information check

Public inspection reports can contain names of staff or residents, even if rare. Silver processing runs a PII scan on every chunk (DQ rule `DQ-S-006`). Any chunk flagged is:

1. Marked `pii_flag = true` in `silver.chunks`
2. Excluded from gold and from the graph
3. Listed in `ops.dq_results` for the Data Steward to review
