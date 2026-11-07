# Wave 1: First light (v0.1.0)

**Goal:** prove that a useful, trustworthy research graph can be built from public data alone, with evidence on every link.

**Branch:** `wave-1` · **Target version:** `0.1.0` · **Status:** ◐ slices 1–5 done, slice 6 next

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

## Slice 2: Connectors ☑

**Goal:** reliable, polite, incremental ingestion of all four wave-1 sources.

- [x] Shared HTTP client (`httpx`): thread-safe rate limiter per source, retries with exponential backoff and jitter, `Retry-After`, timeouts, `User-Agent` with configured contact; raw payload capture with a per-run index
- [x] Connector protocol: `plan(options, checkpoint)`, `fetch(plan) → payloads` (network), and a pure `parse(payload) → records`, so replay uses exactly the ingest parser; run records in `ingest_run`; one data job at a time per data home
- [x] **PubMed:** esearch/history + efetch XML; recursive date-window splitting for the 10k cap; affiliation-sweep (`org`), `query` (for person searches), `ids`, and `linked` (PMIDs that grants or studies cite but the lake lacks) modes; XML parser to `pubmed_article`, `pubmed_author`, `pubmed_mesh`, `pubmed_grant`; grant-string normalization to core project numbers and institute + serial
- [x] **iCite:** batched metrics for PMIDs in the lake that lack metrics or have metrics older than 30 days
- [x] **RePORTER:** project search with fiscal-year/agency splitting for the offset cap; publication-link search with batch halving and narrowing; `org`, `pi`, `ids`, and `query` (validated criteria) modes; parser to `reporter_project`, `reporter_investigator`, `reporter_publication_link`
- [x] **ClinicalTrials.gov:** study search with page tokens and ID lookup; organization search across lead sponsor, collaborators, sites, and official affiliations; parser to `ctgov_*` tables; contact details removed before anything is stored, including the raw zone
- [x] Incremental checkpoints per source and scope; checkpoints never advance on partial, failed, or capped runs; RePORTER re-reads the previous, current, and next fiscal years on every incremental run (amounts and dates on recent awards change)
- [x] `tessera ingest <source|all> [--mode] [--ids] [--query] [--since] [--until] [--full] [--max-results]`, `tessera replay <source> <run>`, and `tessera runs`; long runs publish to the lake every 25 payloads
- [x] Fixtures: synthetic payloads only; parser tests; paging, splitting, halving, and narrowing tests against a fake transport; opt-in live contract tests (`-m network`)

**Acceptance (met):** each connector ingests a fixture corpus into the lake idempotently (running twice writes nothing); an interrupted run keeps what it fetched and the next run continues from the last checkpoint without duplicates; a replay from raw into a fresh data home produces identical lake content. Also verified against the live APIs: 78 grant-years → 1,210 grant–publication links → 1,208 papers fetched, 1,156 of which acknowledge the same grant in PubMed.

## Slice 3: Evidence engine v1 ☑

**Goal:** turn source records into people and evidenced links. See [evidence-engine.md](../evidence-engine.md).

- [x] Organization profile matching: alias, wildcard-pattern, and email-domain matcher for affiliations, sponsor names, and site facilities; `exclusions` for look-alike organizations
- [x] Name normalization and compatibility; corpus name-ambiguity estimate
- [x] People bootstrap from grant investigators (NIH profile ID; contact PIs active, co-investigators candidates until affiliated papers confirm them) and the optional roster CSV import (`tessera people import`)
- [x] Discovered-people clustering from the affiliation sweep (ORCID first, then name + coauthor + topic)
- [x] Candidate retrieval per person (sweep, PubMed person search mode `pubmed:people`, grant links)
- [x] `authorship/v1` features and scoring; evidence items with human-readable explanations; author-slot conflict resolution
- [x] Source-reported links: `acknowledges`, `references`, `funded_by`, `investigator_on`
- [x] Inferred `official_of` person–study links
- [x] Assertion upsert that never changes locked assertions and marks links a source no longer reports
- [x] `tessera resolve`, `tessera sync` (ingest → resolve people → person search and metrics → resolve), run lock; `tessera people list`. Search-index build joins `sync` in slice 4
- [x] `tessera eval authorship` with the metrics in the evaluation section

**Acceptance (met):** on a synthetic organization with known answers, strong-band precision 100% and retrieval recall 100% (targets 99% and 95%), also in a harder same-field-twins variant; re-resolving after curation leaves every decision intact (accept, reject, defer, and undo); every assertion carries at least one evidence item with an explanation. Live check on a mid-sized institute (two-year window): full `tessera sync` in about 100 seconds, 70 active investigators with 1,655 strong suggestions, 2,150 grant–publication links, about 965 discovered people for review.

## Slice 4: API and search ☑

**Goal:** a clean, documented API that the web app (and others) can build on.

- [x] Response conventions: `{data, meta}` envelopes, cursor pagination, sparse field sets, problem+json errors
- [x] People: list (filters, sort, facets), detail with identifiers, units, and linked items grouped by status
- [x] Publications, grants, and studies: list and detail with connected entities and provenance
- [x] Assertions: get with evidence and history; decide (accept/reject/defer/undo) with note; manual add by PMID (fetched from PubMed if not in the lake)
- [x] Review queue endpoint with prioritization (strong by person, then conflicts, then ambiguous names) and prefetch hints; bulk decisions within one person and band
- [x] Sources: health, runs, checkpoints, freshness, evidence and index status, lake tables; `/api/v1/stats` for the home page
- [x] Search: FTS5 search and serving index (`tessera build search`, also run by `sync`), query sanitization, grouped results, facets with counts, highlighting with private-use markers rather than HTML
- [x] Self-hosted API reference page at `/api/docs` (the stock interactive docs load third-party assets, which the CSP forbids); OpenAPI JSON at `/api/openapi.json`

**Acceptance (met):** API tests cover every endpoint (48 tests on a synthetic organization); on live data for a mid-sized institute, profile p95 6 ms and search p95 20 ms (targets 300 ms and 200 ms); 28 hostile search strings (FTS operators, quotes, SQL, NUL, 600-character input) never produce a server error. The 5,000-person, 300,000-paper target remains to be measured at that scale; lookups are indexed, and the review queue filters by score in SQL.

## Slice 5: Web app and design system ☑

**Goal:** a beautiful, fast, accessible app with no frameworks. See [ux.md](../ux.md).

- [x] `tokens.css` (oklch, light/dark/system), base styles, component styles; generated `/theme.css` from branding config (the accent must be a plain CSS color)
- [x] Tessera mark and favicon (original SVG, mosaic tile motif); original line icons, built as inline SVG by a small module rather than a sprite file
- [x] App shell: sidebar, top bar, theme toggle, router with URL state, skip link, focus management
- [x] Components: entity card and chip, data table (sort, dense mode), facets, tabs, drawer, palette dialog, toast, skeleton, empty state, stat tile with sparkline, SVG bar charts with data-table fallbacks. Lists page through results rather than virtualizing rows; a column chooser waits until a table needs one.
- [x] Command palette (⌘K / Ctrl-K, or `/`)
- [x] Pages: Home, Search, People directory, Person profile, Publication, Grant, Study, Admin → Sources
- [x] Status marks and the evidence drawer (with accept, reject, defer, and undo) on every linked item
- [x] Responsive layout from 360 px; keyboard pass; reduced-motion support

**Acceptance (met):** every page checked in light and dark themes on live data; an automated contrast sweep of rendered text over four pages in both themes found nothing under WCAG AA (lowest body-text token 4.87:1); no console errors; app shell 50 KB gzipped (budget 100 KB, enforced by a test); no horizontal scrolling at 360 px. Keyboard: palette, drawer (focus trap, Escape, focus return), mobile menu, and skip link all work without a mouse. The curation half of the exit-criteria walkthrough moves to slice 6 with the review queue. No screen-reader software has been run yet; markup uses labelled controls, live regions, and text alternatives for charts.

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

- [ ] Docs: README quickstart, configuration reference, organization profile guide, curation guide, as Markdown in `docs/` written so the future documentation site can be built from it unchanged (see the roadmap)
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
| 2026-11-07 | Slice 2 done. NIH *contracts* (e.g. `75N9…`, `HHSN…`) are not normalized as grants; month ranges in PubMed dates ("Nov-Dec") keep only the year rather than claim false precision. |
| 2026-11-07 | Slice 3 done. Multi-PI co-investigators are not presumed members; lone-initial person searches dropped (≈5× noise in a live check); preprint servers and megajournals excluded from journal history; seed threshold 0.90 with two rounds. |
| 2026-11-07 | Slice 4 done. `derived/search.db` doubles as the serving index for lists and summaries; detail pages read the lake by key. Person topics ranked by TF-IDF, with model organisms and methods treated as non-topics. Response schemas in OpenAPI are minimal for now (parameters and bodies are documented). |
| 2026-11-07 | Slice 5 done. No build step: ES modules, strict CSP with no inline code, data only ever inserted as text. Icons are an original JS module (no sprite). Grant and registry text is reflowed into paragraphs, and all-capital source names are shown in name case. Studies with phase "not applicable" show no phase. |
| 2026-11-07 | Planned a documentation site (generator, screenshot capture from a fictional demo organization, `gh-pages` publishing) for after wave 2; see the roadmap. Wave 1 docs are Markdown written so that site can use them unchanged. |
| 2026-11-07 | To revisit: Starlette's test client now prefers a successor to `httpx` (warning silenced in pytest config); FastAPI pulls in the inert `opentelemetry-api` package (no exporter, nothing is sent). |
