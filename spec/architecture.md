# Architecture

Tessera is a **modular monolith**: one Python package, one web process, and a command-line tool that runs the same code for batch work. There are no message brokers, no search clusters, and no microservices. A single machine is the reference deployment.

## System overview

```text
            PUBLIC SOURCES                                   PEOPLE
  PubMed · iCite · NIH RePORTER · ClinicalTrials.gov       browser (vanilla JS app)
  ORCID (wave 2) · Grants.gov (wave 5)                            │
                │                                                 │ HTTPS / JSON
                ▼                                                 ▼
  ┌──────────────────────────────┐                 ┌──────────────────────────────┐
  │ Connectors                    │                 │ Web API (FastAPI + uvicorn)  │
  │ rate limits · retries · paging│                 │ /api/v1 · static web app     │
  │ raw capture · incremental     │                 └──────┬───────────────┬───────┘
  └──────────────┬───────────────┘                        │ reads          │ writes
                 │ verbatim payloads                       │                │ (curation)
                 ▼                                         ▼                ▼
  ┌──────────────────────────────┐   normalize   ┌───────────────┐  ┌──────────────────┐
  │ RAW ZONE                      │ ────────────▶ │ LAKE          │  │ LEDGER           │
  │ gzip JSONL/XML, immutable     │               │ Parquet tables│  │ SQLite (default) │
  │ (replay without re-fetching)  │               │ via DuckDB    │  │ or PostgreSQL    │
  └──────────────────────────────┘               └──────┬────────┘  └────────┬─────────┘
                                                         │                    │
                                         ┌───────────────┴────────────────────┴──┐
                                         │ Evidence engine                        │
                                         │ people bootstrap · authorship scoring  │
                                         │ link inference · assertions → ledger   │
                                         └───────────────┬────────────────────────┘
                                                         ▼
                                         ┌────────────────────────────────────────┐
                                         │ DERIVED (rebuildable)                  │
                                         │ search index (SQLite FTS5) · concept   │
                                         │ profiles · similarity · metrics        │
                                         └────────────────────────────────────────┘
```

## The three data tiers

Tessera separates data by **how it can be recovered**. This is the core architectural idea; see [ADR-0003](decisions/0003-storage-ledger-lake-derived.md).

| Tier | Holds | Technology | Recoverable from | Back up? |
|---|---|---|---|---|
| **Raw zone** | API response bodies as received (registry contact details removed), one folder per ingest run | gzip files on disk | Re-fetching the public APIs | Optional (saves re-fetch time) |
| **Lake** | Normalized source records: articles, authors, grants, investigators, studies, citation metrics | Parquet (pyarrow), queried with DuckDB | Replaying the raw zone | Optional |
| **Ledger** | Everything humans decide or own: people, identifiers, assertions and their review history, units, settings, ingest runs, users | SQLite in WAL mode by default; PostgreSQL option | **Nothing.** This is the irreplaceable tier | **Yes, always** |
| **Derived** | Search index, concept profiles, similarity pairs, metric tables, reporting snapshots | SQLite FTS5 and Parquet | Rebuilding from lake + ledger | No |

Rules that follow from the tiers:

- The **lake is append-mostly**. Ingest runs add new Parquet files; they never edit old ones in place. A `current` view picks the latest version of each record.
- The **ledger never stores bulk source content**. It stores identities, links, decisions, and configuration. Content (titles, abstracts, MeSH terms) is looked up in the lake by key.
- **Derived data is disposable.** `tessera build` regenerates all of it. No user-facing state may live only in derived data.

## Components

All components live in the single `tessera` Python package.

| Component | Responsibility | Key modules |
|---|---|---|
| **Config** | Load `tessera.toml` and environment overrides; validate early | `tessera.config` |
| **Connectors** | One per public source. Shared HTTP client with a thread-safe rate limiter, retries with backoff, `Retry-After` handling, raw capture, and per-source paging quirks | `tessera.connectors.*` |
| **Lake** | Arrow schemas per table; atomic Parquet writer with per-table manifest; DuckDB read layer with `current` views; compaction | `tessera.lake` |
| **Ledger** | SQLAlchemy Core table definitions, Alembic migrations, repository functions | `tessera.ledger` |
| **Evidence engine** | People bootstrap, name normalization, candidate retrieval, feature scoring, link inference, assertion upserts that respect human decisions | `tessera.evidence` |
| **Search** | Build and query the FTS5 index across people, publications, grants, and studies | `tessera.search` |
| **Analytics** *(wave 2+)* | Concept profiles, similarity, networks, portfolio metrics in DuckDB | `tessera.analytics` |
| **Jobs** | Orchestrate `sync` (ingest → resolve → link → build); single-run lock; progress and run records | `tessera.jobs` |
| **API** | FastAPI routers under `/api/v1`; problem+json errors; OpenAPI | `tessera.api` |
| **Web app** | Static vanilla JS single-page app and design system, served by the API process | `tessera/web/static` |
| **CLI** | `tessera init · doctor · sync · ingest · resolve · build · serve · export` | `tessera.cli` |

## Data flow: `tessera sync`

1. **Ingest.** Each enabled connector runs incrementally from its saved checkpoint. Payloads go to the raw zone; normalized records go to the lake. Each run is recorded in the ledger with counts, timings, and errors. A failing source doesn't block the others, and its last good data stays in service, flagged as stale.
2. **Resolve people.** The evidence engine seeds and updates people from configured sources (grant investigators, an optional roster file) and finds candidate publications for each person.
3. **Link.** Source-reported links are recorded (grant ↔ publication, study ↔ publication, study ↔ grant). Inferred links (person ↔ publication, person ↔ study) are scored with evidence. All links are upserted as *assertions* in the ledger. Assertions a human already decided are never changed by automation; new evidence is attached for the reviewer to see.
4. **Build.** Derived artifacts are rebuilt: search index, and in later waves concept profiles, similarity pairs, and metrics. Each artifact is written to a temp path and swapped atomically.

Each step can also run alone (`tessera ingest pubmed`, `tessera resolve`, `tessera build search`).

## Request flow: a profile page

1. The browser asks `GET /api/v1/people/{id}`, then `GET /api/v1/people/{id}/publications`.
2. The API reads the person, their identifiers, and their assertions (with statuses) from the **ledger**. These are live, so a curator's decision shows on the next request.
3. It takes display summaries for the linked publications, grants, and studies from the **serving index** (`derived/search.db`), falling back to the **lake** for anything not indexed (such as unlikely suggestions).
4. It returns one JSON document. The browser renders it.

Detail pages (`/publications/{pmid}`, `/grants/{core}`, `/studies/{nct}`) read full content from the lake by key. DuckDB pushes the key filter through the current-version view into the Parquet scan, so lookups use file statistics rather than scanning tables. Lists, facets, and search come from the serving index.

Measured on live data for a mid-sized research institute (about 70 people, 6,000 papers in the lake, 2,400 indexed): profile p95 6 ms, search p95 20 ms, publication detail p95 113 ms, review queue p95 47 ms.

DuckDB is opened **in-memory per API process**, with read-only views over Parquet. No process ever holds a write lock on the lake, so the CLI can ingest while the server is running, and several API workers can run side by side.

## Runtime and deployment

- **Reference deployment:** one machine (a laptop or a single VM), Python 3.13 managed by `uv`, `tessera serve` running uvicorn, and `tessera sync` run on a schedule by cron/systemd or by the optional built-in scheduler.
- **Data home:** everything lives under one directory (`var/` by default, configurable): `raw/`, `lake/`, `ledger.db`, `derived/`, `logs/`. Copying that directory moves an installation.
- **Network and access:** the server binds to `127.0.0.1` by default and runs in *single-operator mode*: no sign-in, answers only to requests addressed to this machine, and records curation under the configured operator name. With `server.accounts` on, everyone signs in with a local account and a role (admin, curator, researcher, viewer; curators may be limited to a unit or a person), sessions are server-side, every change carries a CSRF token, and one policy table decides who may call each route ([ADR-0009](decisions/0009-local-accounts-and-sessions.md)). Listening beyond this machine requires accounts, and HTTPS in front.
- **Outbound traffic:** only to the configured public APIs. Nothing is sent to analytics, telemetry, or AI services.
- **Containers are optional.** A container image may be provided later; it is never required.

## Directory layout

```text
tessera/
├── pyproject.toml · uv.lock · LICENSE · NOTICE · README.md · CHANGELOG.md
├── tessera.example.toml          # documented example config ("Example University")
├── spec/                         # this folder
├── src/tessera/
│   ├── cli/                      # argparse commands
│   ├── config.py
│   ├── core/                     # ids, clocks, names, logging, errors
│   ├── connectors/               # http base + pubmed, icite, reporter, ctgov, (orcid, grantsgov)
│   ├── lake/                     # arrow schemas, writer, manifest, duckdb reader, compaction
│   ├── ledger/                   # tables, migrations, repositories
│   ├── evidence/                 # bootstrap, names, candidates, features, scoring, linkers
│   ├── search/                   # fts5 index build + query
│   ├── analytics/                # (wave 2+)
│   ├── jobs/                     # sync orchestration, locks, run records
│   ├── api/                      # app factory, routers, response models
│   └── web/static/               # index.html, css/, js/, icons/
├── tests/                        # unit, integration, fixtures (synthetic or scrubbed)
└── var/                          # default data home (gitignored)
```

## Cross-cutting concerns

| Concern | Approach |
|---|---|
| **Configuration** | `tessera.toml` (gitignored) read with stdlib `tomllib`; `TESSERA_*` environment overrides; secrets such as an NCBI API key from environment only |
| **IDs** | Typed, stable internal IDs (`per_…`, `unt_…`, `asr_…`); external IDs (PMID, NCT, core project number, ORCID, NIH profile ID) are mapped, never used as a person's primary key |
| **Time** | All timestamps UTC ISO 8601; source dates keep their precision (year, month, or day) |
| **Errors** | API returns RFC 9457 `application/problem+json`; CLI exit codes 0 ok, 1 error, 2 partial |
| **Logging** | Structured logs to `var/logs`; every ingest and build run has a run ID that appears in logs and in the ledger |
| **Security** | No secrets in the browser; strict Content-Security-Policy with no inline scripts; static file serving confined to the web root; parameterized SQL only; FTS query input sanitized |
| **Testing** | `uv run pytest`. Unit tests never touch the network: connectors are tested against recorded or synthetic fixtures. Live contract tests are opt-in |
| **Portability** | SQLite and PostgreSQL through SQLAlchemy Core; DuckDB only reads Parquet with its built-in extensions, so nothing is downloaded at runtime |

## Dependency budget

Runtime dependencies are limited to the list below. Adding one requires an ADR.

| Package | Why |
|---|---|
| `fastapi`, `uvicorn` | Web API and server (chosen stack) |
| `httpx` | HTTP client for connectors; also used by the API test client |
| `pyarrow` | Parquet read/write and Arrow schemas for the lake |
| `duckdb` | In-process analytics over Parquet |
| `sqlalchemy`, `alembic` | Ledger portability across SQLite and PostgreSQL, plus migrations |

Optional extras: `postgres` (`psycopg`). Development: `pytest`, `ruff`. The browser loads no third-party code.
