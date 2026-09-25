# Runbook: Personal information found

**Trigger:** PII appears in an answer, in gold, or in Neo4j, or DQ-S-006 goes above threshold.

1. **Contain.** Stop the app. Find every `chunk_id` involved.
2. **Remove.** Set `pii_flag = true` on those chunks in silver, delete their rows from gold, delete their nodes from Neo4j (`MATCH (c:Chunk {chunk_id: $id}) DETACH DELETE c`).
3. **Check spread.** Search `gold.eval_results` for the same text. Remember `gold.llm_call_log` has no raw text (ADR-004), so it does not need cleaning.
4. **Fix the cause.** Improve the PII scan patterns, add the missed case as a unit test.
5. **Record.** Add an entry to the risk register notes for R-01 with date, what was found, and what changed.
6. **Restart** the app only after the rerun passes DQ-S-006.
