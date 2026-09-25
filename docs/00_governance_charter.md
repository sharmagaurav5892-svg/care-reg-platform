# 00 Governance Charter

| Field | Value |
|-------|-------|
| Document owner | Platform Owner |
| Version | 1.0 |
| Status | Approved |
| Review cycle | Every release, or quarterly, whichever comes first |

## 1. Purpose

This charter sets out how data and AI on the CareReg Intelligence Platform are owned, managed, checked and changed. It exists so that anyone picking up the platform knows who decides what, which rules apply, and where the evidence lives.

## 2. Scope

In scope:

- All data stored in the bronze, silver, gold and ops layers
- All AI components: embedding models, extraction prompts, the RAG application, the LLM gateway and the evaluation harness
- All configuration that controls the above (`config/`)
- Derived outputs: Neo4j graph, dashboards, published results

Out of scope:

- Any employer, resident, patient or personal data. This platform must never ingest it. See [02 Data classification](02_data_classification_and_handling.md).

## 3. Principles

1. **Public in, traceable out.** Only approved public sources come in. Every answer must be traceable to a source file and page.
2. **Governance as code.** If a rule matters, it lives in `config/` and a test checks it. A rule that only exists in a document is a wish.
3. **Nothing is promoted without passing checks.** Data moves bronze to silver to gold only when its critical data quality rules pass.
4. **Rebuild from bronze.** Raw files are never changed. Any silver or gold table can be deleted and rebuilt from bronze plus code.
5. **Every run leaves a receipt.** Each pipeline run writes to `ops.run_log` with its git commit and config hash, so any table can be tied to the exact code that produced it.
6. **Models are components, not magic.** Every model and prompt has an owner, a version and an eval score before it goes into use.
7. **Spend is a metric.** Cost is tracked per run and per answer, with hard limits.

## 4. Roles

In this portfolio build one person holds every role. The roles are still kept separate on paper, because the point is to show how the work splits when a team takes over.

| Role | Responsibility |
|------|----------------|
| **Platform Owner** | Accountable for the platform overall. Approves this charter, the risk register and releases. |
| **Data Owner** | Accountable for a dataset: decides who can use it, its classification and retention. One per domain (Regulations, Inspections, AI Operations, Evaluation). |
| **Data Steward** | Day to day quality of a dataset: writes and tunes DQ rules, handles DQ incidents, keeps catalog entries accurate. |
| **Data Engineer (Custodian)** | Builds and runs pipelines, storage and access controls. Does not decide classification. |
| **AI Model Owner** | Accountable for a model or prompt: its version, eval results, known limits and rollback plan. |
| **Evaluator** | Writes gold questions, labels answers by hand, calibrates the judge. Must not be the person who wrote the prompt being evaluated, where possible. |
| **Governance Council** | Lightweight review group. Approves new sources, classification changes, new models and exceptions. |

## 5. RACI

R = Responsible, A = Accountable, C = Consulted, I = Informed

| Activity | Platform Owner | Data Owner | Data Steward | Data Engineer | AI Model Owner | Evaluator | Council |
|----------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Approve a new data source | I | A | C | C | I | I | R |
| Set data classification | I | A | R | C | I | I | C |
| Write or change a DQ rule | I | A | R | C | I | I | I |
| Build or change a pipeline | A | C | C | R | I | I | I |
| Respond to a critical DQ failure | I | A | R | R | I | I | I |
| Add or change a model or prompt | A | C | I | C | R | C | C |
| Maintain the eval gold set | I | C | C | I | C | R | A |
| Approve a release | A | C | C | R | R | C | I |
| Grant access to Confidential data | I | A | C | R | I | I | C |
| Approve an exception to policy | C | C | C | I | C | I | A |
| Monitor and control spend | A | I | I | R | R | I | I |

## 6. Decision rights

| Decision | Who decides | Recorded in |
|----------|-------------|-------------|
| Architecture choices | Platform Owner | `docs/adr/` |
| Source approval | Governance Council | `config/sources.yaml` (`approved_by`, `approved_on`) |
| Classification | Data Owner | `config/catalog.yaml` |
| DQ thresholds | Data Steward, approved by Data Owner | `config/dq_rules.yaml` |
| Model or prompt going live | AI Model Owner, passing eval gate | `docs/06_ai_governance.md` model inventory |
| Policy exceptions | Governance Council | Exceptions log in section 8 |

## 7. Policies that sit under this charter

| Policy | Document |
|--------|----------|
| Data classification and handling | [02](02_data_classification_and_handling.md) |
| Data quality | [04](04_data_quality_framework.md) |
| Lineage | [05](05_lineage.md) |
| AI use and model risk | [06](06_ai_governance.md) |
| Security and access | [07](07_security_and_access.md) |
| Retention | [08](08_retention_and_lifecycle.md) |
| Change management | [09](09_change_management.md) |
| Cost | [10](10_cost_management.md) |

## 8. Exceptions log

Any time a policy is knowingly broken, it gets an entry here with an expiry date. An exception without an expiry date is not allowed.

| ID | Policy | What is being allowed | Reason | Approved by | Expires |
|----|--------|-----------------------|--------|-------------|---------|
| EX-001 | 07 Security | Secrets stored in a local `.env` file instead of Azure Key Vault in local mode | Solo build, no shared infrastructure. `.env` is git-ignored. | Governance Council | When Fabric mode goes live |

## 9. Standards referenced

This platform borrows structure from well known frameworks. It does not claim certification against any of them.

- DAMA-DMBOK: knowledge areas for data governance, quality, metadata and lifecycle
- NIST AI Risk Management Framework 1.0: Govern, Map, Measure, Manage functions for the AI components
- ISO/IEC 42001: AI management system concepts (model inventory, impact review, monitoring)
