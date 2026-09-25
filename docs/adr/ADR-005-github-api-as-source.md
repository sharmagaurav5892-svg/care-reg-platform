# ADR-005: Ingest source documents through the GitHub REST API

- **Status:** Accepted
- **Date:** 2026-09-25
- **Decider:** Platform Owner

## Context

Step 1 assumed source files would be downloaded by hand into `data/landing/`. That works, but it's not how enterprise data arrives. Real sources are systems with APIs, auth, rate limits and change history. The Ontario sites don't offer a clean API for regulation text.

## Options

1. **Manual download into landing.** Simple, but no auth, no incremental loads, no change history, nothing to show about integration.
2. **Scrape ontario.ca.** Fragile, unclear terms of use, breaks when the page layout changes.
3. **Keep the files in a private GitHub repo and read them through the GitHub REST API.** The repo becomes a document store with full version history.

## Decision

Option 3. The platform treats the repo like any external system: token auth, rate limit handling, incremental loads by commit watermark, and skipping unchanged files by Git blob SHA.

## Consequences

- Good: real API integration patterns (auth, retries, rate limits, pagination, watermarks) on a free, reliable API.
- Good: every loaded file links to the exact commit it came from. Amendments show up as new commits, so the source has an audit trail of its own.
- Good: "documents in Git" is a real enterprise pattern (policy docs, runbooks, contracts in repos).
- Bad: someone still has to put the files into the repo. Accepted; that is the source system's job, not the platform's.
- Bad: the repo must be private to respect `redistribute_raw: false`, so a token is required. That's a fine-grained, read-only token on one repo.
