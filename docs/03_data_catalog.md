# 03 Data Catalog

> Generated from `config/catalog.yaml`. Do not edit by hand.
> Regenerate with `python -m careplatform.governance.render_catalog`.

## Domains and owners

| Domain | Data Owner | Data Steward |
|--------|-----------|--------------|
| regulations | Regulations Data Owner | Regulations Data Steward |
| ai_operations | AI Operations Owner | AI Operations Steward |
| evaluation | Evaluation Owner | Evaluator |
| platform | Platform Owner | Data Engineer |

## Table summary

| Table | Layer | Domain | Classification | Retention (days) | Grain | DQ rules |
|-------|-------|--------|----------------|------------------|-------|----------|
| [bronze.raw_documents](#bronzeraw_documents) | bronze | regulations | Public | 1825 | one row per unique file (by content hash) | 8 |
| [silver.document_units](#silverdocument_units) | silver | regulations | Public | 1825 | one row per unit per version of its text | 2 |
| [silver.chunks](#silverchunks) | silver | regulations | Public | 365 | one row per chunk (active or retired) | 7 |
| [silver.cross_references](#silvercross_references) | silver | regulations | Public | 1825 | one row per link per unit version | 1 |
| [gold.chunk_embeddings](#goldchunk_embeddings) | gold | regulations | Public | 365 | one row per chunk per embedding model | 2 |
| [gold.entities](#goldentities) | gold | regulations | Public | 365 | one row per entity mention per chunk | 1 |
| [gold.relationships](#goldrelationships) | gold | regulations | Public | 365 | one row per relationship mention per chunk | 2 |
| [gold.llm_call_log](#goldllm_call_log) | gold | ai_operations | Internal | 180 | one row per model call attempt | 1 |
| [gold.eval_questions](#goldeval_questions) | gold | evaluation | Internal | 1825 | one row per question per version | 1 |
| [gold.eval_results](#goldeval_results) | gold | evaluation | Internal | 1825 | one row per question per eval run per pipeline variant | 2 |
| [ops.run_log](#opsrun_log) | ops | platform | Internal | 1825 | one row per run | 1 |
| [ops.dq_results](#opsdq_results) | ops | platform | Internal | 1825 | one row per rule per run | 1 |
| [ops.api_call_log](#opsapi_call_log) | ops | platform | Internal | 180 | one row per HTTP attempt | 1 |
| [ops.watermarks](#opswatermarks) | ops | platform | Internal | 1825 | one row per source system and scope | 1 |

## bronze.raw_documents

File register. One row per unique source file pulled by any connector (BC Laws API, GitHub API). Raw bytes stay in data/landing.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 1825 days  
- **Grain:** one row per unique file (by content hash)  
- **Primary key:** doc_id  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| doc_id | string | no | SHA-256 of file bytes. Same content is never loaded twice. |
| source_id | string | no | Key into config/sources.yaml |
| source_system | string | no | Connector that fetched it (bclaws | github) |
| file_name | string | no | File name |
| source_ref | string | no | Where it lives in the source. Document id for BC Laws |
| source_version | string | yes | Version marker from the source. ETag or Last-Modified for BC Laws |
| remote_hash | string | yes | Hash the source gives before download (Git blob SHA). Null when the API has none. |
| landing_path | string | no | Path under the landing root where the bytes were saved (data/landing locally |
| source_url | string | no | Link a person can open to see exactly what was loaded |
| doc_type | string | no | act | regulation | inspection_report |
| jurisdiction | string | no | ON or BC |
| mime_type | string | no | Guessed from the file extension |
| file_size_bytes | long | no | Size in bytes |
| ingested_at | timestamp | no | UTC time the row was written |
| load_id | string | no | run_id from ops.run_log that loaded this file |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-B-001 | critical | uniqueness | The same file (by content hash) must never be registered twice. |
| DQ-B-002 | critical | validity | Empty files are rejected. |
| DQ-B-003 | critical | validity | Every file must map to a known document type. |
| DQ-B-004 | critical | consistency | Every source_id must exist in config/sources.yaml and be approved. |
| DQ-B-005 | critical | accuracy | The landing file must exist and still hash to its doc_id. Bronze promises we can rebuild from these files. |
| DQ-B-006 | critical | completeness | Every file must record where it lives in the source system, or we lose lineage back to it. |
| DQ-B-007 | critical | validity | Only the two provinces in scope. Also catches the YAML ON-becomes-true trap at the data level. |
| DQ-B-008 | critical | validity | Every row must come from a known connector. |

## silver.document_units

One row per version of a section (XML) or page (PDF). History is kept (SCD2), so amended and repealed text is never lost. Only is_current rows feed chunks.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 1825 days  
- **Grain:** one row per unit per version of its text  
- **Primary key:** unit_id  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| unit_id | string | no | SHA-256 of source_id + unit_ref + text_hash. Same text, same id. |
| source_id | string | no | Key into config/sources.yaml. Stable across versions of the law. |
| doc_id | string | no | FK to bronze.raw_documents. The document version this text first appeared in. |
| unit_type | string | no | section | page |
| unit_ref | string | no | Citation-ready reference, unique within a source. e.g. s. 12, Sch. 2, s. 1, p. 7 |
| unit_order | int | no | Position in the document |
| context_path | string | yes | Where the unit sits, e.g. Part 4 Care and Supervision > Division 2 Staffing |
| heading | string | yes | Section heading (marginal note). Null for schedule sections and pages. |
| text | string | yes | Clean text with subsection numbers inline. Repealed subsections removed. Tables rendered row by row. |
| char_count | int | no | Length of text |
| token_estimate | int | no | char_count / settings chunking.chars_per_token. An estimate, not a model count (ADR-009). |
| text_hash | string | no | SHA-256 of text. How a changed section is detected between versions. |
| history_note | string | yes | Enactment and amendment notes from the source |
| is_repealed | boolean | no | True when the whole unit is repealed. Kept for the record |
| extraction_method | string | no | xml | pypdf |
| valid_from | timestamp | no | UTC time the platform first saw this text. System time, NOT the legal effective date (ADR-009). |
| valid_to | timestamp | yes | UTC time it was replaced. Null while current. |
| is_current | boolean | no | True for the version in force now. One per source_id + unit_ref. |
| load_id | string | no | run_id that wrote this version |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-S-001 | critical | completeness | Every unit that is still law must have text. An empty one means the parser missed the markup, and that law would silently vanish from answers. |
| DQ-S-007 | critical | uniqueness | Exactly one current version per source_id + unit_ref. Two current versions means answers could quote amended law. |

## silver.chunks

Retrieval chunks built from current, non-repealed units. Usually one per section; long sections are split at subsection boundaries. Rows are never deleted; replaced chunks are retired (is_active false), so gold knows exactly what to add and what to remove.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 365 days  
- **Grain:** one row per chunk (active or retired)  
- **Primary key:** chunk_id  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| chunk_id | string | no | SHA-256 of unit_id + chunk_index + chunker_version. Stable while the text and the chunking logic are unchanged, so gold is not rebuilt for nothing. |
| unit_id | string | no | FK to silver.document_units |
| doc_id | string | no | FK to bronze.raw_documents |
| source_id | string | no | Key into config/sources.yaml |
| unit_ref | string | no | Citation shown with answers, e.g. s. 43 |
| chunk_index | int | no | 0 for a whole unit; 0 |
| context_header | string | no | Label put in front of the text, e.g. [BC | Residential Care Regulation | Part 4 > Division 2 | s. 43 Fire drills] |
| text | string | no | context_header + chunk text. This is what gets embedded and sent to the LLM. |
| token_estimate | int | no | Estimated tokens of text (header included) |
| text_hash | string | no | SHA-256 of normalized text |
| pii_flag | boolean | no | True if PII scan found something. Excluded from gold. |
| chunker_version | string | no | Version of the chunking logic that built it (settings chunking.chunker_version). Bumping it rebuilds every chunk. |
| is_active | boolean | no | True while the chunk reflects current |
| retired_at | timestamp | yes | UTC time it stopped being active (text amended |
| load_id | string | no | run_id that created the chunk. Unchanged chunks keep their original load_id. |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-S-002 | critical | completeness | No empty chunks. |
| DQ-S-003 | warning | validity | Chunks should fall in the size band. Lower bound is 20, not 50, because short sections are valid law (25 of 279 BC sections are under 50 tokens, profiled 2026-09-26); the context header alone is about 20, so below that means a parsing fault. |
| DQ-S-004 | critical | consistency | Every chunk must trace back to a registered file. |
| DQ-S-005 | warning | uniqueness | Duplicate chunk text above 5 percent points to repeated headers or a chunking bug. |
| DQ-S-006 | warning | validity | PII flags above 1 percent of chunks need Data Steward review. Flagged chunks never reach gold. |
| DQ-S-008 | critical | consistency | Every chunk must come from a unit that is current and not repealed. This is the rule that stops the app quoting old or cancelled law. |
| DQ-S-010 | critical | consistency | Every active chunk must be built by the current chunking logic. A mix means a rebuild stopped halfway and search would return two styles of the same law. |

## silver.cross_references

Links from a unit to another law, read from the source markup (no LLM). Feeds REFERENCES edges in the knowledge graph.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 1825 days  
- **Grain:** one row per link per unit version  
- **Primary key:** ref_id  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| ref_id | string | no | SHA-256 of unit_id + position of the link in the unit |
| unit_id | string | no | FK to silver.document_units (the unit containing the link) |
| source_id | string | no | Law the link is in |
| unit_ref | string | no | Unit the link is in |
| target_href | string | no | Link target as published, e.g. /legislation/96405_01 |
| target_doc_id | string | yes | BC document id parsed from the href, e.g. 96405_01. Null if the link is not to legislation. |
| target_text | string | no | Link text, e.g. Representation Agreement Act |
| load_id | string | no | run_id that produced the row |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-S-009 | critical | consistency | Every cross reference must belong to a unit we hold. |

## gold.chunk_embeddings

Vector embedding per chunk.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 365 days  
- **Grain:** one row per chunk per embedding model  
- **Primary key:** chunk_id, embedding_model  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| chunk_id | string | no | FK to silver.chunks |
| embedding_model | string | no | Deployment name and version |
| embedding_dim | int | no | Vector length |
| vector | array<float> | no | The embedding |
| load_id | string | no | run_id that produced the row |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-G-001 | critical | completeness | Missing embeddings mean silently missing answers. |
| DQ-G-002 | critical | validity | Vector length must match the configured embedding model. |

## gold.entities

Entities extracted from chunks by the LLM using a fixed schema.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 365 days  
- **Grain:** one row per entity mention per chunk  
- **Primary key:** entity_id, source_chunk_id  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| entity_id | string | no | Hash of entity_type + normalized_name |
| entity_type | string | no | Must be in the allowed entity schema |
| name | string | no | As written in the text |
| normalized_name | string | no | Lower case |
| source_chunk_id | string | no | FK to silver.chunks. Record-level lineage. |
| extraction_model | string | no | Model deployment used |
| prompt_version | string | no | Version of the extraction prompt |
| load_id | string | no | run_id that produced the row |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-G-003 | critical | validity | The LLM must stay inside the entity schema. Anything else is a prompt problem. |

## gold.relationships

Relationships between entities extracted from chunks.

- **Owner:** Regulations Data Owner  
- **Steward:** Regulations Data Steward  
- **Classification:** Public  
- **Retention:** 365 days  
- **Grain:** one row per relationship mention per chunk  
- **Primary key:** rel_id  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| rel_id | string | no | Hash of src + type + dst + chunk |
| src_entity_id | string | no | FK to gold.entities |
| rel_type | string | no | Must be in the allowed relationship schema |
| dst_entity_id | string | no | FK to gold.entities |
| source_chunk_id | string | no | FK to silver.chunks |
| prompt_version | string | no | Version of the extraction prompt |
| load_id | string | no | run_id that produced the row |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-G-004 | critical | consistency | No relationships pointing at entities that do not exist. |
| DQ-G-005 | critical | validity | Relationship types must stay inside the graph schema. |

## gold.llm_call_log

One row per model call through the LLM gateway. No raw prompt text by default (ADR-004).

- **Owner:** AI Operations Owner  
- **Steward:** AI Operations Steward  
- **Classification:** Internal  
- **Retention:** 180 days  
- **Grain:** one row per model call attempt  
- **Primary key:** call_id  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| call_id | string | no | UUID |
| called_at | timestamp | no | UTC |
| app | string | no | rag | extraction | judge |
| provider | string | no | azure_openai | fallback |
| model | string | no | Deployment name |
| prompt_tokens | int | yes | From the API response |
| completion_tokens | int | yes | From the API response |
| latency_ms | int | no | Wall clock time |
| status | string | no | ok | error | timeout |
| error_type | string | yes | Error class if status is not ok |
| retry_count | int | no | Retries before this result |
| fallback_used | boolean | no | True if a fallback provider answered |
| cost_usd | double | no | Calculated from token counts and config/pricing |
| request_hash | string | no | SHA-256 of redacted prompt. Lets you spot repeats without storing text. |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-G-006 | warning | validity | Negative cost means a pricing config error. |

## gold.eval_questions

Hand-written gold question set with expected answers and sources.

- **Owner:** Evaluation Owner  
- **Steward:** Evaluator  
- **Classification:** Internal  
- **Retention:** 1825 days  
- **Grain:** one row per question per version  
- **Primary key:** question_id, version  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| question_id | string | no | Stable ID like Q-001 |
| version | int | no | Bumped when the question or answer changes |
| question | string | no | The question |
| expected_answer | string | no | Reference answer |
| expected_sources | array<string> | no | doc_id and section refs that support the answer |
| category | string | no | lookup | relationship | multi_hop | negative |
| author | string | no | Who wrote it |
| created_at | timestamp | no | UTC |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-G-007 | critical | completeness | Every gold question must list at least one supporting source. |

## gold.eval_results

Judge scores and human labels per question per pipeline run.

- **Owner:** Evaluation Owner  
- **Steward:** Evaluator  
- **Classification:** Internal  
- **Retention:** 1825 days  
- **Grain:** one row per question per eval run per pipeline variant  
- **Primary key:** eval_run_id, question_id, variant  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| eval_run_id | string | no | FK to ops.run_log |
| question_id | string | no | FK to gold.eval_questions |
| variant | string | no | vector | graph | hybrid |
| answer | string | no | What the app answered |
| retrieved_chunk_ids | array<string> | no | What the retriever returned |
| judge_score | int | yes | 1 to 5 from the judge |
| judge_model | string | yes | Model used as judge |
| judge_prompt_version | string | yes | Version of the judge prompt |
| human_label | int | yes | 1 to 5 from a person |
| latency_ms | int | no | End to end answer time |
| cost_usd | double | no | Total cost for this answer |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-G-008 | critical | validity | Judge scores must be on the 1 to 5 scale. Anything else means the judge output was not parsed. |
| DQ-G-009 | critical | consistency | Every result must belong to a known gold question. |

## ops.run_log

One row per pipeline run. Ties every table back to the code and config that built it.

- **Owner:** Platform Owner  
- **Steward:** Data Engineer  
- **Classification:** Internal  
- **Retention:** 1825 days  
- **Grain:** one row per run  
- **Primary key:** run_id  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| run_id | string | no | UUID. Written into load_id on every row the run touches. |
| pipeline | string | no | e.g. bronze_ingest |
| started_at | timestamp | no | UTC |
| ended_at | timestamp | yes | UTC |
| status | string | no | RUNNING | SUCCEEDED | FAILED |
| rows_in | long | yes | Rows read |
| rows_out | long | yes | Rows written |
| git_commit | string | yes | Short commit hash of the code that ran |
| config_hash | string | no | Hash of the config files at run time |
| error_message | string | yes | Set when status is FAILED |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-O-001 | warning | timeliness | No run should sit in RUNNING for more than 2 hours. Stuck runs get flagged. |

## ops.dq_results

One row per DQ rule evaluated per run.

- **Owner:** Platform Owner  
- **Steward:** Data Engineer  
- **Classification:** Internal  
- **Retention:** 1825 days  
- **Grain:** one row per rule per run  
- **Primary key:** run_id, rule_id  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| run_id | string | no | FK to ops.run_log |
| rule_id | string | no | FK to config/dq_rules.yaml |
| table_name | string | no | Table checked |
| dimension | string | no | completeness | validity | uniqueness | consistency | timeliness | accuracy |
| severity | string | no | critical | warning |
| status | string | no | pass | fail |
| observed_value | double | yes | What the check measured |
| threshold | double | yes | What it was compared to |
| failed_rows | long | yes | Rows that broke the rule |
| checked_at | timestamp | no | UTC |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-O-002 | critical | consistency | Every rule_id logged must exist in config/dq_rules.yaml. Catches renamed or deleted rules. |

## ops.api_call_log

One row per HTTP attempt made by any connector, including retries. Auth headers are never logged.

- **Owner:** Platform Owner  
- **Steward:** Data Engineer  
- **Classification:** Internal  
- **Retention:** 180 days  
- **Grain:** one row per HTTP attempt  
- **Primary key:** call_id  
- **Write mode:** append

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| call_id | string | no | UUID |
| run_id | string | no | FK to ops.run_log |
| connector | string | no | e.g. github |
| method | string | no | HTTP method |
| endpoint | string | no | Path without host or query string |
| status_code | int | yes | HTTP status |
| attempt | int | no | 1 for the first try |
| latency_ms | int | no | Time for this attempt |
| response_bytes | long | yes | Size of the response body |
| rate_limit_remaining | int | yes | Requests left in the current rate limit window |
| error_type | string | yes | Exception name or HTTPnnn when the attempt failed |
| called_at | timestamp | no | UTC |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-O-003 | warning | validity | More than 10 percent failed calls means the source API is struggling. Retries hide it, this surfaces it. |

## ops.watermarks

High-water mark per source for incremental loads. Only moved after a successful, DQ-passed load.

- **Owner:** Platform Owner  
- **Steward:** Data Engineer  
- **Classification:** Internal  
- **Retention:** 1825 days  
- **Grain:** one row per source system and scope  
- **Primary key:** source_system, scope  
- **Write mode:** merge

| Column | Type | Nullable | Description |
|--------|------|:--------:|-------------|
| source_system | string | no | e.g. github |
| scope | string | no | What the mark covers. owner/repo@branch for GitHub |
| watermark_value | string | no | Last loaded position. Commit SHA for GitHub |
| updated_at | timestamp | no | UTC |
| load_id | string | no | run_id that moved the mark |

**Data quality rules**

| Rule | Severity | Dimension | Description |
|------|----------|-----------|-------------|
| DQ-O-004 | critical | completeness | A blank watermark would make the next run reload everything or skip everything. |
