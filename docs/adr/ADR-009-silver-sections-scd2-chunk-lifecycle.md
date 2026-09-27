# ADR-009: Silver is built from law sections, keeps history, and never deletes chunks

- **Status:** Accepted
- **Date:** 2026-09-27
- **Decider:** Platform Owner, Regulations Data Steward

## Context

The first silver design (step 1) assumed PDF sources: `silver.document_pages` with `page_no`, and chunks with `page_start`/`page_end`, sized by `tiktoken`.

Before building silver we profiled the real bronze files (2026-09-26/27):

| Finding | Evidence |
|---|---|
| BC Laws gives **XML**, not PDF. No pages; the natural unit is the **section**. | 279 `section` elements across 3 laws |
| A typical section is **200 to 330 tokens**, below a 600 token chunk target | 62,000 estimated tokens / 279 sections |
| 2 sections are over 1,200 tokens; 25 are under 50 | Both long ones are in the Act |
| Repeal happens at **two levels**: whole sections (heading "Repealed"), and single subsections inside live sections | s. 30-31 of the Act; s. 12(1) of B.C. Reg. 96/2009 |
| **Schedules restart numbering** at s. 1 | 10 schedules; "s. 1" appears in the body and in schedules |
| One real **rules table** (s. 32, bathing facilities) and one bookkeeping table (`conseqhead`, consequential amendments) | `entry`/`trow` inside a paragraph vs inside `conseqhead` |
| Cross references and amendment history are **in the markup** | 103 `link` elements, 42 `hnote` in 96/2009 |
| `tiktoken` cannot download its vocabulary on Databricks Free Edition | `Connection reset by peer`, notebook test S2 |

## Decision

**1. One unit table for both formats.** `silver.document_units`: one row per section (XML) or page (PDF, Ontario later), with `unit_type` saying which. `unit_ref` is citation-ready and unique within a law (`s. 12`, `Sch. B, s. 1`).

**2. History is kept with SCD Type 2 on units.** `unit_id = sha(source_id, unit_ref, text_hash)`. A changed section closes the old row (`valid_to`, `is_current = false`) and adds a new one. Nothing is overwritten. DQ-S-007 (critical): one current version per section.

**3. Repeal at both levels.** Whole-section repeal: kept with `is_repealed = true`, never chunked. Subsection repeal: that piece is dropped from `text`, its note kept in `history_note`, the rest of the section stays live. DQ-S-008 (critical): chunks only from current, non-repealed units.

**4. Chunk = section.** Long sections (over `max_tokens`) are split at line boundaries (a line is a subsection or paragraph), never mid-sentence. Every chunk starts with a **context header**: `[jurisdiction | law | Part > Division | section heading]`.

**5. Chunks have a lifecycle; rows are never deleted.** Each run computes the desired chunks and diffs them against the table by `chunk_id`: new ones are inserted, ones no longer wanted are retired (`is_active = false`, `retired_at`), the rest are untouched and keep their original `load_id`. A quiet day writes nothing.

**6. Chunking logic is versioned.** `chunk_id = sha(unit_id, chunk_index, chunker_version)`, with `chunker_version` in `settings.yaml`. Changing the chunking or header logic means bumping it in the same PR; the next run retires every chunk and inserts the rebuilt ones. DQ-S-010 (critical): every active chunk is on the current version.

**7. Token counts are estimates** (`characters / chars_per_token`, column `token_estimate`). Used only for sizing. Billed tokens come from the model responses and are logged by the LLM gateway (`gold.llm_call_log`).

**8. Cross references come from the markup**, not an LLM: `silver.cross_references`, one row per `<link>`, with the target law id parsed from the href.

## Consequences

- Good: citations are exact (`Residential Care Regulation, s. 12`), and a chunk read on its own says what law and section it is.
- Good: the app cannot be fed amended or repealed text (two critical DQ rules), and the old text is still there for "what changed" questions.
- Good: gold can be incremental: embed active chunks that have no embedding, remove embeddings of retired chunks.
- Good: lineage is true. `load_id` on a chunk is the run that created it, not the last run that happened to rewrite it.
- Bad: **system time only.** `valid_from` is when the platform first saw the text, not the legal effective date. An amendment effective 1 March and published 5 March shows `valid_from` 6 March. Bitemporal history (adding legal valid time) is a known gap.
- Bad: **schema changes need a migration plan.** Adding the lifecycle columns was done by deleting a local, rebuildable table. That is not acceptable once silver runs in prod; a migration step (`ALTER TABLE ... ADD COLUMNS` run by CI, plus backfill) is needed before the first prod release.
- Bad: the schedule label (`Sch. A`) comes from the schedule's title element; checked on the real files, but a new BC format could break it. DQ-S-007 would catch a clash.
- Neutral: Ontario PDFs will reuse both tables with `unit_type = page`; the page parser is future work.
