# ADR-006: Add British Columbia through the public BC Laws API

- **Status:** Accepted
- **Date:** 2026-09-25
- **Decider:** Platform Owner, Governance Council (new sources)

## Context

ADR-005 put the Ontario documents in a private GitHub repo because Ontario has no public API for legislation text. That works, but it isn't a true public API. We checked the options:

- **Ontario e-Laws:** website only, no API.
- **Ontario Data Catalogue (CKAN API):** a real public API, but the Long-Term Care ministry's datasets there are almost all restricted or under review. The one open dataset is COVID-19 data frozen in 2023.
- **Ontario LTC inspection reports site:** no API.
- **BC Laws API:** a public API serving full BC legislation as XML (content, document and search endpoints), no key, open licence allowing reuse.

BC regulates the same kind of care under the Community Care and Assisted Living Act, the Residential Care Regulation and the Assisted Living Regulation.

## Options

1. Keep Ontario only via GitHub (ADR-005).
2. Switch to BC only via the BC Laws API.
3. **Both:** BC through its public API, Ontario through GitHub, one pipeline.

## Decision

Option 3. The bronze pipeline became connector-neutral (`connectors/base.py`). Each connector runs as its own pipeline run, so one failing doesn't stop the other.

## Consequences

- Good: two very different integration patterns on one platform.
  - GitHub: token auth, commit watermark, Git blob hash skip.
  - BC Laws: no auth, HTTP conditional requests (ETag, 304 Not Modified), content hash fallback, XML with HTML fallback.
- Good: the graph can answer cross-province questions ("how do BC's assisted living rules compare to Ontario's retirement home rules?"). These are relationship questions, where graph RAG beats plain vector search.
- Good: proves the connector contract works. A third source is one new file.
- Bad: bronze columns had to be generalized (`source_ref`, `source_version`, `remote_hash` replaced GitHub-specific columns). Done before any real data was loaded, so no migration was needed.
- Bad: BC's robots.txt disallows crawlers on some paths. We are not crawling: we call the documented API for three named documents, once per run, with conditional requests and a clear User-Agent (integration rule I-14).
- Bad: two provinces means entity names need a jurisdiction to avoid mixing them up in the graph (handled in step 4).
