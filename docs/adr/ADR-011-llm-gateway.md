# ADR-011: One LLM gateway for every chat model call

- **Status:** Accepted
- **Date:** 2026-09-27
- **Decider:** AI Model Owner, Platform Owner

## Context

The answer pipeline, the LLM judge and (later) entity extraction all need a chat model. If each one calls a model API directly, there is no single place to decide which model may see which data, to cap spend, to fail over, or to log usage. That's the "shadow AI" problem at small scale.

Tested in a Databricks notebook (2026-09-27):

| Check | Result |
|---|---|
| `databricks-meta-llama-3-3-70b-instruct` | HTTP 200, about 1 s, returns `usage` (prompt and completion tokens), content is plain text |
| `databricks-gpt-oss-120b` | HTTP 200, about 1 s, returns `usage`, content is a **list**: a reasoning block (restates the user's question) then a text block. Reasoning tokens are billed: about 2.5x the tokens of the visible answer |
| Smoke test through the finished gateway (2026-09-29, laptop) | Llama: ok, 1.4 s, 55 in / 28 out tokens. gpt-oss: ok, 2.0 s, 107 in / 160 out tokens (about 5.7x the output for the same one-sentence answer). Gemini: skipped, no key. Log rows and DQ-G-006/G-011 written and passing |
| Both, asked a BC question with no sources | Answered confidently from memory; Llama added a claim not in our laws. Grounding has to be enforced by the answer pipeline, not trusted to the model |

## Decision

1. **Every chat call goes through `careplatform.gateway.Gateway.chat()`.** No other code calls a model API.
2. **Route and fallback from config.** `settings.yaml models.chat.route`: Llama 3.3 70B, then gpt-oss-120b, then Gemini. One quick retry (`max_retries: 1`), then the next model. A user is waiting; a 5-minute retry loop is worse than a fallback.
3. **Data classification decides the models.** Each model lists `allowed_data` (docs/02). User questions are Confidential, so they only go to Databricks-hosted models. Gemini (free tier may use prompts to improve Google's models) is `public` only: eval questions and law text. Restricted data goes to no model.
4. **Authentication.** Databricks-hosted models use the workload's own identity, no key. Gemini uses an API key read through `config.secret("GEMINI_API_KEY")`: `.env` on a laptop, secret scope `care-reg` / `gemini-api-key` on Databricks. The key is only sent in a header, never in a URL, so it can't reach `ops.api_call_log`. If there's no key, Gemini is skipped, not an error.
5. **Clean answers.** Only text blocks are returned. Reasoning blocks are dropped and never logged.
6. **Budget.** `daily_token_budget` (tokens logged today plus this session). Over it, the gateway refuses before sending anything.
7. **Log.** One row per model attempt in `gold.llm_call_log`: purpose, provider, model, prompt version, data classification, tokens, latency, status, retries, fallback, cost, SHA-256 of the messages. No prompt, answer or key (ADR-004). HTTP attempts also go to `ops.api_call_log`. DQ-G-006 (cost) and DQ-G-011 (classification respected) run on every flush.
8. **Cost.** `pricing` in settings.yaml, per 1M tokens. 0 today (Free Edition, free tier). List prices are filled in, in a reviewed PR, before any paid rollout.

## In production

The same gateway would sit in front of a frontier model through an enterprise channel with a data processing agreement and Canadian data residency, for example Azure OpenAI (Canada Central or Canada East) or Claude through Amazon Bedrock or Google Vertex AI. That changes one entry in `route` and one provider class. Which model wins is decided by the eval results (judge score, faithfulness, refusal rate, cost per answer), not by preference.

## Consequences

- Good: one place to change models, enforce data rules, cap spend and see usage. The same log feeds the cost dashboard.
- Good: an outage of one endpoint degrades to the fallback instead of failing the app, and the log shows how often it happens.
- Good: hands-on key management (secret scope, header only, rotation) without putting user data at risk.
- Bad: the budget check reads today's log on first use; two app sessions running at once can overshoot slightly. A shared counter is the fix if that ever matters.
- Bad: temperature 0 makes answers repeatable, not identical; hosted models can still change underneath us (R-23).
- Neutral: prompts live in `config/prompts/` (added with the answer pipeline) and every call logs its version.
- Neutral: on a laptop the Databricks SDK must be installed (`requirements.txt`) for model auth; Databricks jobs already have it, so the wheel does not list it.
