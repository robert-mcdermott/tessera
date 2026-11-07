# ADR-0003: Storage in three tiers: ledger, lake, and derived

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Tessera holds two very different kinds of data:

- **Source content** (publications, grants, studies, citation metrics): large, append-mostly, refreshed in batches, read analytically, and always re-obtainable from public sources.
- **Human and governance state** (people, links, decisions, settings, accounts): small, written one record at a time by concurrent users, transactional, and irreplaceable.

The project wants to start simple (SQLite, with a PostgreSQL option) and is interested in Parquet and DuckDB for the append-mostly publication data.

Constraints verified while deciding:

- The DuckDB Python package statically includes only its core functions plus the Parquet, JSON, and ICU extensions. Reading SQLite or PostgreSQL databases, and DuckDB's own full-text search, require extensions downloaded at runtime. That conflicts with offline installs and the minimal-dependency goal.
- A DuckDB database file allows only one read-write process. The server, CLI ingestion, and multiple web workers would contend for it.
- SQLite's FTS5 full-text search ships with Python's standard `sqlite3` module.

## Decision

Use three tiers, separated by **how the data can be recovered**:

1. **Raw zone**: verbatim API payloads as gzip files, one folder per run. Allows replay without re-fetching.
2. **Lake**: normalized source records as **Parquet** files written with **pyarrow**, queried with **DuckDB** in-process. Append-mostly: runs add files, a per-table manifest is swapped atomically, and `current` views select the latest version of each record. DuckDB is used only on Parquet, in-memory, read-only, per process. It never holds a database file lock.
3. **Ledger**: **SQLite** in WAL mode by default, **PostgreSQL** as an option, through **SQLAlchemy Core** with **Alembic** migrations. Holds people, identifiers, units, assertions, curation events, runs, checkpoints, settings, and later accounts and pages.

Plus **derived** artifacts (the FTS5 search index, concept profiles, similarity pairs, metrics), which are rebuildable from the lake and ledger and swapped atomically when rebuilt.

When analytics need curated links (for example, accepted authorships for concept profiles), the build step reads them from the ledger into Arrow and registers them with DuckDB. No DuckDB extension is involved.

## Consequences

- **Backups are simple:** the ledger is the only irreplaceable tier. The lake is a cache of public data with full provenance.
- **Concurrency is simple:** ingestion writes new files while the server reads; many web workers can each hold their own in-memory DuckDB.
- **Exit is simple:** Parquet *is* the export format for source data; the ledger is a standard relational database.
- **Analytics scale well:** DuckDB over columnar Parquet handles millions of publications on a laptop.
- **Two query paths:** developers must know which tier holds what. Profile pages read links from the ledger and content from the lake by key. The data model document makes the split explicit.
- **Compaction is needed:** small files from frequent incremental runs are periodically compacted.

## Alternatives

- **SQLite or PostgreSQL for everything:** simplest to start, but heavy analytics and bulk content would share one transactional store, and Parquet's portability and DuckDB's analytical speed would be lost.
- **DuckDB for everything:** fast and pleasant, but its single-writer file lock conflicts with concurrent curation, background ingestion, and multiple web workers, and it is not built for many small transactional writes.
- **DuckDB attaching the SQLite ledger directly:** convenient, but depends on an extension downloaded at runtime.
- **A dedicated graph database:** the research graph is a domain model, not a storage requirement. Relational links with typed predicates cover it at this scale.
