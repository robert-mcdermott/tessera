# Wave 1: First light (v0.1.0)

**Goal:** prove that a useful, trustworthy research graph can be built from public data alone, with evidence on every link.

**Branch:** `wave-1` · **Target version:** `0.1.0` · **Status:** ◐ slice 1 done, slice 2 next

## Exit criteria

The wave is done when, on a laptop with a fresh checkout:

1. `uv run tessera init` creates a data home and a starter `tessera.toml` from the example.
2. After editing the organization profile, `uv run tessera sync` ingests PubMed, iCite, RePORTER, and ClinicalTrials.gov for a configured window (default: 5 years), resolves people, and links records, recording each run.
3. `uv run tessera serve` opens a polished web app (light and dark) where a user can search everything, browse people, and open profiles whose publications, grants, and studies each carry a status mark and an evidence drawer.
4. A curator can work through the review queue by keyboard, and decisions survive a second `sync`.
5. The test suite passes offline, lint is clean, and an authorship evaluation report exists for the pilot data.

Requirements delivered: REC-01–06, REC-09, CUR-01–04, CUR-07, DIS-01–03, CLN-01, CLN-02, CLN-04, PLT-01–05, UX-01–05.

---

## Slice 1: Foundation ☑

**Goal:** a clean, tested skeleton that every later slice builds on.

- [x] `uv init` package layout (`src/tessera`), Python 3.13, `pyproject.toml` with the runtime dependency budget from [architecture.md](../architecture.md)
- [x] `LICENSE` (Apache 2.0), `NOTICE`, `README.md` (pitch + quickstart stub), `CHANGELOG.md`, `CONTRIBUTING.md`
- [x] Tooling: `ruff` (lint + format) and `pytest` config; a `network` marker for opt-in live tests that is skipped by default
- [x] Private-terms check: `scripts/check_terms.py` scans the working tree, staged changes, or a commit message against a local, untracked term list (`.private/terms.txt`) and fails on a match; `--install-hooks` adds optional git hooks
- [x] Config: `tessera.example.toml` (Example University); loader with `tomllib`, `TESSERA_*` env overrides, validation with clear errors
- [x] Data home layout (`raw/`, `lake/`, `ledger.db`, `derived/`, `logs/`) and `tessera init`
- [x] Core utilities: typed IDs (uuid7), UTC clock, structured logging, error types
- [x] Ledger: SQLAlchemy Core tables from [data-model.md](../data-model.md), Alembic baseline migration, `tessera db upgrade`; verified on SQLite and on PostgreSQL 16 (`scripts/dev-postgres.sh`)
- [x] Lake: Arrow schemas, atomic writer with staging and manifest, DuckDB reader with `current` views, `tessera lake compact`
- [x] API skeleton: app factory, `/api/v1/health`, `/api/v1/meta` (version, branding, organization, mode), `/api/openapi.json`, problem+json errors, static file serving confined to the web root, security headers (CSP). Data timestamps join `/meta` with the connectors in slice 2
- [x] CLI skeleton: `tessera init | doctor | db | lake | serve`, exit codes 0/1/2
- [x] `tessera doctor`: checks Python version, config validity, data home permissions, FTS5 availability, and outbound reachability (opt-in)

**Acceptance (met):** `uv run pytest` and `uv run ruff check` pass; `tessera init && tessera serve` serves a placeholder page and `/api/v1/health`; lake writer is crash-safe in tests (kill between rename and manifest swap leaves readable prior state).

## Slice 2: Connectors ☐

**Goal:** reliable, polite, incremental ingestion of all four wave-1 sources.

- [ ] Shared HTTP client (`httpx`): thread-safe rate limiter per source, retries with exponential backoff and jitter, `Retry-After`, timeouts, `User-Agent` with configured contact, raw payload capture
- [ ] Connector protocol: `search(spec) → batches`, `fetch_ids(ids)`, `count(spec)`, `data_version()`, checkpoint read/write; run records in `ingest_run`
- [ ] **PubMed:** esearch/history + efetch XML; recursive date-window splitting for the 10k cap; affiliation-sweep, person-search, and by-ID modes; XML parser to `pubmed_article`, `pubmed_author`, `pubmed_mesh`, `pubmed_grant`; grant-string normalization to core project numbers
- [ ] **iCite:** batched metrics for PMIDs in the lake
- [ ] **RePORTER:** project search with fiscal-year/agency splitting for the offset cap; publication-link search with batch halving and narrowing; parser to `reporter_project`, `reporter_investigator`, `reporter_publication_link`
- [ ] **ClinicalTrials.gov:** study search with page tokens and ID lookup; parser to `ctgov_*` tables; contact details dropped
- [ ] Incremental checkpoints per source and scope; checkpoints never advance on partial runs; weekly full refresh of current fiscal years for RePORTER
- [ ] `tessera ingest <source> [--since] [--ids]` and `tessera replay <source> <run>` (rebuild lake from raw)
- [ ] Fixtures: synthetic or scrubbed recorded payloads; parser tests; paging and splitting tests against a fake transport; opt-in live contract tests

**Acceptance:** each connector ingests a fixture corpus into the lake idempotently (running twice adds no rows to the current view); interrupted runs resume; a replay from raw produces identical lake content.

## Slice 3: Evidence engine v1 ☐

**Goal:** turn source records into people and evidenced links. See [evidence-engine.md](../evidence-engine.md).

- [ ] Organization profile matching: alias and pattern matcher for affiliations, sponsor names, and site facilities; tested for false positives on look-alike names
- [ ] Name normalization and compatibility; corpus name-ambiguity estimate
- [ ] People bootstrap from grant investigators (NIH profile ID) and the optional roster CSV import
- [ ] Discovered-people clustering from the affiliation sweep (ORCID first, then name + coauthor + topic)
- [ ] Candidate retrieval per person (sweep, person search, grant links)
- [ ] `authorship/v1` features and scoring; evidence items with human-readable explanations; author-slot conflict resolution
- [ ] Source-reported links: `acknowledges`, `references`, `funded_by`, `investigator_on`
- [ ] Inferred `official_of` person–study links
- [ ] Assertion upsert that never changes locked assertions and marks links a source no longer reports
- [ ] `tessera resolve`, `tessera sync` (ingest → resolve → link → build), run lock
- [ ] `tessera eval authorship` with the metrics in the evaluation section

**Acceptance:** on a fixture organization with known answers, strong-band precision ≥ 99% and retrieval recall ≥ 95%; re-running `sync` after curation leaves every decision intact; each assertion has at least one evidence item.

## Slice 4: API and search ☐

**Goal:** a clean, documented API that the web app (and others) can build on.

- [ ] Response conventions: `{data, meta}` envelopes, cursor pagination, sparse field sets, problem+json errors
- [ ] People: list (filters, sort, facets), detail with identifiers, units, and linked items grouped by status
- [ ] Publications, grants, and studies: list and detail with connected entities and provenance
- [ ] Assertions: get with evidence and history; decide (accept/reject/defer/undo) with note; manual add by PMID
- [ ] Review queue endpoint with prioritization and prefetch hints
- [ ] Sources: health, runs, checkpoints, freshness
- [ ] Search: FTS5 index build (`tessera build search`), query sanitization, grouped results, facets with counts, highlighting
- [ ] Self-hosted API reference page (the stock interactive docs load third-party assets, which the CSP forbids); OpenAPI JSON is already at `/api/openapi.json`

**Acceptance:** API tests cover every endpoint; profile p95 < 300 ms and search p95 < 200 ms on the benchmark fixture; malformed search input never produces a server error.

## Slice 5: Web app and design system ☐

**Goal:** a beautiful, fast, accessible app with no frameworks. See [ux.md](../ux.md).

- [ ] `tokens.css` (oklch, light/dark/system), base styles, component styles; generated `theme.css` from branding config
- [ ] Tessera mark and favicon (original SVG, mosaic tile motif); icon sprite (original SVGs)
- [ ] App shell: sidebar, top bar, theme toggle, router with URL state, skip link, focus management
- [ ] Components: entity card and chip, data table (sort, column chooser, dense mode, virtual rows), facets, tabs, drawer, modal, toast, skeleton, empty state, stat tile with sparkline, SVG bar and line charts
- [ ] Command palette (⌘K / Ctrl-K)
- [ ] Pages: Home, Search, People directory, Person profile, Publication, Grant, Study, Admin → Sources
- [ ] Status marks and the evidence drawer on every linked item
- [ ] Responsive layout from 360 px; keyboard and screen-reader pass; reduced-motion support

**Acceptance:** every page works in light and dark themes with AA contrast; no console errors; app shell ≤ 100 KB gzipped; keyboard-only walkthrough of the exit criteria succeeds.

## Slice 6: Curation ☐

**Goal:** curators clear suggestions quickly and confidently.

- [ ] Review queue page: split view, prioritized list, evidence panel with author-slot highlight, keyboard flow (`j k a r d u`), prefetching
- [ ] Bulk accept of strong suggestions within one person, logged per assertion
- [ ] Undo toast and decision history
- [ ] Manual addition of a paper by PMID to a person
- [ ] Discovered-people queue: promote or dismiss singly or in bulk with filters
- [ ] Single-operator mode: actor name from config on every curation event; server bound to localhost by default
- [ ] Profile and search reflect decisions on the next request (search index updated incrementally)

**Acceptance:** a curator can process 100 suggestions in under 10 minutes on the fixture data; every action is reversible and logged.

## Slice 7: Release prep ☐

- [ ] Docs: README quickstart, configuration reference, organization profile guide, curation guide
- [ ] Run the evaluation on pilot data and record the summary (without identifying data) in the release notes
- [ ] Private-terms check over the whole repository; dependency and license review
- [ ] `CHANGELOG.md` for 0.1.0; version bump to `0.1.0`
- [ ] Update this file and the roadmap status
- [ ] PR `wave-1` → `main`, merge, pull, tag `v0.1.0`, publish the release

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-07 | Wave planned. Pilot runs on a laptop or single VM; internal-first; AI deferred to wave 5 and optional; extra public sources approved: iCite and grant–publication links (wave 1), ORCID (wave 2), Grants.gov (wave 5) |
| 2026-11-07 | Slice 1 done. PostgreSQL is tested in a disposable podman/docker container rather than deferred to wave 6. DuckDB runs with extension auto-install and auto-load disabled. |
| 2026-11-07 | To revisit: Starlette's test client now prefers a successor to `httpx` (warning silenced in pytest config); FastAPI pulls in the inert `opentelemetry-api` package (no exporter, nothing is sent). |
