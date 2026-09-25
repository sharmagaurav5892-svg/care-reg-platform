# 10 Cost Management

## 1. Budget

| Item | Tier | Expected cost |
|------|------|---------------|
| Storage and compute (local mode) | Your laptop | $0 |
| Microsoft Fabric (prod mode) | 60 day trial capacity | $0 |
| Neo4j | AuraDB Free | $0 |
| Azure OpenAI embeddings | `text-embedding-3-small`, pay per token | Under $1 |
| Azure OpenAI extraction, answers, judge | `gpt-4.1-mini`, pay per token | $5 to $15 |
| GitHub, Power BI Desktop, Streamlit | Free | $0 |
| **Total target** | | **Under $20 USD** |

New Azure accounts get a one-time credit for the first 30 days, which covers the whole LLM spend if the build happens in that window.

## 2. What it would cost for real

For interview conversations: the same design at small production scale, always on.

| Item | Tier | Monthly (USD, approx) |
|------|------|------|
| Fabric capacity | F2 pay as you go, always on | about $263 |
| Fabric capacity | F2 paused outside work hours | about $70 |
| Neo4j | AuraDB Professional, smallest | about $66 |
| Azure OpenAI | Depends on traffic | Tracked per answer below |

Prices checked September 2026. Recheck the Azure and Neo4j pricing pages before quoting.

## 3. Guardrails

Set in `config/settings.yaml` under `cost_guardrails`:

| Guardrail | Value | Enforced by |
|-----------|-------|-------------|
| Monthly budget | $20 | Azure Cost Management budget with alerts at 50, 80, 100 percent |
| Max cost per pipeline run | $3 | Gateway: stops the run when reached, marks it FAILED |
| Hard spending stop | Azure free account spending limit left on | Azure |
| Token caps | Max output tokens set on every call | Gateway |

## 4. Unit economics

The gateway logs cost per call, so we can report:

- **Cost per answer:** sum of `cost_usd` per question in `gold.eval_results`
- **Cost to build the graph:** sum of extraction calls per `load_id`
- **Cost per 1,000 chunks embedded**

These go on the Power BI dashboard and into the README results table. Being able to say "each answer costs about a tenth of a cent" in an interview is worth more than any diagram.

## 5. Cheap by design

- Hash-based dedupe in bronze means a file is never processed twice
- Embeddings are only computed for new chunks (`merge` on `chunk_id + embedding_model`)
- Extraction runs only on chunks without entities for the current prompt version
- Small model by default; only move up if the eval gate fails
