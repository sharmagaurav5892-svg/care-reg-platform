# ADR-004: Do not store raw user questions in the call log by default

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decider:** Platform Owner, with Data Owner (AI Operations)

## Context

Logging every prompt and response is the easiest way to debug an LLM app. But users type anything, including names and health details. Storing that turns an Internal log table into a Confidential one, with all the access and retention rules that come with it.

## Decision

`gold.llm_call_log` stores metrics only: tokens, latency, cost, status, and a SHA-256 hash of the redacted prompt. No raw text.

For debugging, a developer can turn on a local-only debug log that never leaves the laptop and is deleted after 7 days.

Eval runs are different: gold questions are written by the Evaluator, contain no personal data, and are stored in full in `gold.eval_results`.

## Consequences

- Good: the call log stays Internal and can feed a dashboard freely.
- Good: the hash still lets you spot repeated questions and cache hits.
- Bad: harder to debug a bad production answer. Mitigated by the eval set: bad answers get turned into gold questions.
