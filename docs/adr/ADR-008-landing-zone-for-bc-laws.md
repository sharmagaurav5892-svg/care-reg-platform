# ADR-008: Fetch BC Laws outside Databricks, land it, register it inside

- **Status:** Accepted
- **Date:** 2026-09-26
- **Decider:** Platform Owner
- **Supersedes:** the in-Databricks BC fetch from ADR-006 (the connector itself is kept)

## Context

The first dev job failed with `Temporary failure in name resolution` for `www.bclaws.gov.bc.ca`. A notebook socket test showed Databricks Free Edition only allows outbound traffic to a short allowlist (pypi.org and api.github.com: OK; bclaws.gov.bc.ca and google.com: blocked). Verifying identity did not change it. The platform can't call the BC Laws API from inside Databricks.

We didn't test outbound access before building the connector. That was a mistake, and the checks below were run before any new code this time.

## Feasibility checks (2026-09-26)

| Check | Question | Result |
|---|---|---|
| F1 | Can an outside machine write into the landing volume? | Yes, via `databricks fs cp` (the folder must exist first: `fs mkdir`) |
| F2 | Can we reach BC Laws, and does it support ETag? | Reachable. **No ETag.** `Last-Modified` is always "now", so it's useless. Content is byte-stable: two downloads gave the same SHA-256. |
| F3 | Can Databricks read files landed from outside? | Yes, as plain files under `/Volumes/...` |
| F4 | Does all of that work from a GitHub-hosted runner, with the deploy token? | Yes. `200`, 234,607 bytes, same SHA-256 as from a laptop in Ontario, upload OK. Run: Actions > spike-runner-feasibility #1 (branch deleted, run kept as evidence). |

## Options

1. **Landing zone (edge fetch).** A GitHub Actions runner fetches, uploads to the UC volume, and a Databricks task registers it.
2. **Paid Databricks** (or Azure Databricks) with open egress. Simplest code, but it costs money every day it runs.
3. **Fetch on a laptop** and upload by hand. Free, but not a pipeline.

## Decision

Option 1.

```
GitHub Actions (daily, 05:30 Toronto)                Databricks job bronze_ingest
  fetch_to_landing: download every approved doc  ->   register_landing:
  hash, write files, write _manifest.json LAST          find complete batches past the watermark
  upload files, then the manifest, to                   re-hash every file against the manifest
    landing/incoming/<connector>/<date>/<batch>/        skip content already in bronze
  bundle run bronze_ingest (and wait)                   DQ gate, append, log API calls, move watermark
```

Rules:

- **The runner holds no state.** It downloads everything every time. The lakehouse decides what's new by content hash. A failed runner can never leave the two sides out of step.
- **The manifest is the done-marker, and goes up last.** A folder without one is ignored.
- **The manifest is a versioned contract** (`manifest_version: 1`). Both sides import the same constants.
- **Integrity:** every file is re-hashed in Databricks and compared with the manifest. A mismatch stops the load and writes nothing.
- **Governed attributes come from our config, not the runner.** `doc_type` and `jurisdiction` are looked up in `sources.yaml`.
- **The audit trail survives.** The runner's API calls travel in the manifest and land in `ops.api_call_log` under the register run's `run_id`.
- **Change detection is content hash**, because BC Laws has no usable ETag or Last-Modified (F2).

## Consequences

- Good: $0, and the same pattern companies use for partner file drops (drop zone, done-marker, checksum, dedupe, watermark).
- Good: fetch and register can be tested separately, and together on a laptop.
- Good: `ops.run_log` shows how data arrived (`bronze_register:bclaws` vs `bronze_ingest:bclaws`).
- Bad: about 700 KB is downloaded and uploaded every day, even when nothing changed. Tiny for 3 documents. Revisit if a source is large.
- Bad: two systems in the chain (GitHub and Databricks). The workflow waits for the job, so one red run shows the whole failure.
- Bad: the deploy token (EX-002) is now also used by a daily scheduled workflow, not just deploys. Same expiry, same rotation runbook.
- Bad: landed batches pile up in the volume. Retention for `landing/incoming` needs adding to docs/08 before prod.
- Neutral: Ontario (GitHub connector) still runs inside Databricks, because api.github.com is on the allowlist.