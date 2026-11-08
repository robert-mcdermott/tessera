# ADR-0007: Search with a derived SQLite FTS5 index

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Tessera needs fast keyword search across people, publications, grants, and studies, with facets and highlighting. The target scale for a single machine is up to about 10,000 people and 1 million publications. Search must work the same whether the ledger is SQLite or PostgreSQL, and must not require an external search service or a runtime-downloaded extension.

## Decision

- Build a **derived search index** at `derived/search.db` using SQLite **FTS5** (porter stemming over unicode61 tokenization) from the lake and ledger.
- The index is **rebuildable** (`tessera build search`), written to a temp file and swapped atomically, and **updated incrementally** for curation changes that affect visibility.
- User input is **sanitized** into a safe FTS5 query (quoted terms, supported prefix syntax only). Malformed input never reaches the engine as raw syntax.
- Facets come from columns stored alongside the full-text rows; counts are computed with ordinary SQL over the matching rows.
- Ranking uses BM25 with per-column weights (title above abstract, name above affiliation), with per-entity boosts tuned in tests.

## Consequences

- No search server to run; search works identically on every deployment.
- One extra artifact to rebuild after ingestion, which is part of `tessera sync`.
- Concept-based relevance ("who works on X") comes from concept profiles (wave 2), not from the keyword index. The two are combined in the expert finder.
- If scale ever outgrows FTS5, the derived-index boundary makes replacing it a contained change.

## Alternatives

- **DuckDB's full-text extension:** downloaded at runtime and rebuilt as a whole on change.
- **PostgreSQL full-text search:** good, but only when the ledger is PostgreSQL, and it would require two implementations.
- **A dedicated search engine:** more capability than needed, plus another service to operate.
