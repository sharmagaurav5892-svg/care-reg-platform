# ADR-010: Embed chunks with a Databricks-hosted model, incrementally

- **Status:** Accepted
- **Date:** 2026-09-27
- **Decider:** AI Model Owner, Platform Owner

## Context

Gold needs a vector per chunk for semantic search. The original plan (step 1) was Azure OpenAI `text-embedding-3-small`. That plan was replaced by Gemini (primary) and Claude (fallback) for LLM calls. Before choosing an embedding model we tested, in a Databricks notebook (2026-09-27):

| Check | Result |
|---|---|
| Can Databricks reach external model APIs? | Yes: generativelanguage.googleapis.com, api.anthropic.com, api.openai.com all open (unlike bclaws.gov.bc.ca) |
| Does the workspace host models? | Yes: embeddings `databricks-gte-large-en`, `databricks-bge-large-en`, `databricks-qwen3-embedding-0-6b`; chat models including Llama 3.3 70B and gpt-oss |
| Real call to `databricks-gte-large-en` | 1024 dimensions, about 1 second |
| Does it read our longest chunk (1,030 tokens) in full? | Yes. The full chunk and its first half give clearly different vectors (difference 104.5), so nothing is silently cut off |
| Batch size | 1, 2, 4, 8 texts per call: HTTP 200. 16: HTTP 429 `REQUEST_LIMIT_EXCEEDED: Exceeded workspace QPS rate limit` |

## Decision

1. **Model:** `databricks-gte-large-en`, 1024 dimensions, set in `settings.yaml` (`models.embeddings`).
2. **Why hosted, not Gemini or Claude:** no API key to manage, the text never leaves the platform, usage is governed and logged by Databricks, and it costs nothing on Free Edition. Embedding is bulk work over every chunk; keeping bulk traffic inside the platform is the cleaner default. This mirrors the enterprise pattern of using models through the data platform's own serving layer.
3. **Calls go through our `ApiClient`**, not the Databricks SDK's own retry loop: batches of 8, 1 second between calls, 429 and 5xx retried with backoff and `Retry-After`, every attempt logged to `ops.api_call_log`. (The SDK retried a 429 silently for 5 minutes in the notebook test before failing.)
4. **Incremental, following the chunk lifecycle.** Each run embeds only active, non-PII chunks that have no active vector for this model, and retires vectors whose chunk is no longer active. Unchanged chunks are never re-embedded; a quiet day makes zero calls.
5. **One model at a time.** Vectors from different models are not comparable. Every row records `embedding_model`; changing the model means re-embedding everything in one reviewed PR.
6. **DQ gates (critical):** G-001 every wanted chunk has an active vector, G-002 every vector has the configured length, G-010 no active vector for a retired or PII-flagged chunk.

## Consequences

- Good: $0, no secrets, data stays in the governed platform.
- Good: amended, repealed or PII-flagged text can't be found through the vector index (G-010).
- Good: every model call is in the same audit table as every other API call, with method, status, attempt and latency.
- Bad: tied to what Databricks hosts. If the endpoint is retired, all chunks must be re-embedded with a new model (R-21).
- Bad: the shared endpoint is rate-limited (R-20). Fine for hundreds of chunks; tens of thousands would need provisioned throughput (paid) or a longer run.
- Neutral: queries at search time must use the same model. The retrieval step reads the model name from the same setting.
- Not yet: token counts and list-price cost for embedding calls go to `gold.llm_call_log` when the LLM gateway is built.
