# ADR-002: Neo4j for both graph and vector search

- **Status:** Accepted
- **Date:** 2026-09-24
- **Decider:** Platform Owner

## Context

Knowledge graph RAG needs a graph store and a vector index. The common setup is two systems: a graph database plus a vector database (Azure AI Search, Pinecone and so on).

## Options

1. Neo4j plus Azure AI Search. Two systems, two bills, and results have to be joined in app code.
2. Neo4j alone, using its built-in vector index.
3. Fabric graph features. Newer, less documented, and tied to the trial.

## Decision

Option 2. Chunks are nodes with an embedding property and a vector index. Entities link to chunks with `MENTIONED_IN`. One Cypher query can do vector search and then walk the graph from the hits.

## Consequences

- Good: $0 on AuraDB Free, one system, hybrid retrieval in a single query.
- Good: the chunk node carries lineage keys, so graph answers cite sources the same way vector answers do.
- Bad: AuraDB Free has node and relationship limits and pauses when idle. Handled by keeping the graph rebuildable from gold (risk R-10).
- Bad: Neo4j vector search is less tunable than a dedicated search service. Acceptable at this size.
