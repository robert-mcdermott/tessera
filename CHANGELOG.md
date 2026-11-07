# Changelog

All notable changes to Tessera are recorded here. Versions follow [semantic versioning](https://semver.org/); each wave of work ships as a minor release (wave 1 → 0.1.0).

## Unreleased

### Added

- Product specification in `spec/`: vision, requirements, architecture, data model, data sources, evidence engine, discovery, UX, roadmap, process, glossary, and ADRs 0001–0008.
- Project skeleton: `uv` package, Apache 2.0 license, contributor guide.
- Configuration from `tessera.toml` with `TESSERA_*` environment overrides and validation that reports every problem at once.
- Data home layout (`raw/`, `lake/`, `ledger.db`, `derived/`, `logs/`).
- Ledger schema for people, names, identifiers, units, assertions, curation events, ingest runs, checkpoints, and settings, with an Alembic baseline migration tested on SQLite and PostgreSQL.
- Parquet lake with crash-safe writes, per-table manifests, current-version views in DuckDB, unchanged-record skipping, compaction, and garbage collection. DuckDB never downloads extensions.
- API skeleton: `/api/v1/health`, `/api/v1/meta`, `/api/openapi.json`, RFC 9457 problem responses, strict security headers, and confined static file serving with client-side route fallback.
- CLI: `tessera init`, `doctor` (with `--online`), `db upgrade|current`, `lake status|compact|gc`, `serve`.
- Development tools: private-terms check with optional git hooks, and a disposable PostgreSQL container script.
- Connectors for PubMed, iCite, NIH RePORTER, and ClinicalTrials.gov: a shared HTTP client with per-source rate limits, retries with backoff and `Retry-After`, and contact-identifying `User-Agent`; fetch and parse kept separate so raw payloads can be replayed offline.
- PubMed: organization affiliation sweep, query, ID, and "linked" modes; automatic date-window splitting past the 10,000-record limit; parsing of authors (with ORCID iDs and affiliations), MeSH headings, grants, retractions, and book chapters.
- NIH RePORTER: organization, investigator, ID, and validated-criteria modes; splitting past the 15,000-record limit; the grant-to-publication link table with batch halving and failure narrowing.
- ClinicalTrials.gov: organization search across sponsors, collaborators, sites, and official affiliations; incremental updates by last-update date; contact details removed before storage.
- iCite: citation metrics for publications that lack them or whose metrics are over 30 days old.
- NIH grant-number normalization to core project numbers (and institute + serial when the activity code is missing).
- `tessera ingest` (one source or `all`), `tessera replay`, and `tessera runs`; run records and incremental checkpoints in the ledger; checkpoints advance only after complete runs; one data job at a time per data home.
- `/api/v1/meta` reports each source's last successful fetch and whether it is stale.
- Opt-in live contract tests for every source (`pytest -m network`).
- Evidence engine: name normalization and compatibility (initials, particles, hyphens, accents); organization matching with wildcard patterns, email domains, and exclusions for look-alike names.
- People: bootstrap from NIH grant investigators (contact PIs active; co-investigators held as candidates until affiliated papers confirm them), roster CSV import with unit memberships, and discovery of affiliated authors for review.
- Authorship suggestions from the explainable `authorship/v1` model: ORCID, email, affiliation, grant links, name agreement, name ambiguity, coauthors, topics, journals, and career window, with leave-one-out seeding and one person per author position.
- Source-reported links (grant-publication, study-publication, study-grant, person-grant) and suggested study officials, all with evidence; human decisions are never overridden and links no longer reported are marked, not deleted.
- Decisions with undo, recorded as curation events.
- PubMed person search (`ingest pubmed --mode people`) with batch splitting and skipping of names too common to search.
- `tessera resolve`, `tessera sync`, `tessera people import|list`, and `tessera eval authorship` (precision, recall, calibration, and slices on local decisions).
- Configuration: `organization.exclusions` and an `[evidence]` section for thresholds.
- Search and serving index (`derived/search.db`, SQLite FTS5): organization-relevant people, publications, grants, and studies with summaries, facets, and highlights; rebuilt by `tessera build search` and at the end of `sync`. User input is sanitized so it can never act as query syntax.
- API `/api/v1`: people (directory, profile, publications, grants, studies, history, manual addition), publications, grants, and studies (lists with facets; details with connected records, metrics with definitions, counting rules, and provenance), assertions and decisions, review queue with bulk decisions, unified search, stats, sources, and runs. Envelopes, cursor paging, sparse field sets, and problem responses throughout.
- Self-hosted API reference at `/api/docs`.
- Person topics ranked by distinctiveness (TF-IDF); common model-organism and method headings no longer count as topics.
- Web app (no build step, no frameworks): design tokens in `oklch` with light, dark, and system themes; brand accent from configuration via `/theme.css`; original mark, favicon, and icons.
- App shell with sidebar, top bar, URL-driven router, skip link, focus management, and a mobile menu; command palette (⌘K / Ctrl-K or `/`) searching everything.
- Pages: Home (stats, recent publications, publications by year, largest active grants, source freshness), Search, People directory (cards or table), person profile (publications by status, grants, studies, decision history, topics, activity), publication, grant, and study details with connections and provenance, and Admin → Sources.
- Status marks on every suggested or confirmed link, opening an evidence drawer that explains the score feature by feature and records accept, reject, defer, and undo decisions with an undo toast.
- Accessibility: keyboard operation throughout, focus trapping in dialogs, live-region announcements, data tables behind every chart, reduced-motion support, and AA contrast in both themes.
- Web tests: no inline code, no resources from other origins, no HTML parsing of data, every module import resolves, an app-shell size budget, and JavaScript unit tests (run when Node.js is installed).
