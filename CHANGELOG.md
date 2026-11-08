# Changelog

All notable changes to Tessera are recorded here. Versions follow [semantic versioning](https://semver.org/); each wave of work ships as a minor release (wave 1 → 0.1.0).

## Unreleased

### Changed

- Cassette's stripe is now a thin sunset gradient in both modes, and the hero's diagonal bands are faint pinstripes in the same sunset colors.
- Record-type colors no longer resemble status colors: grants are gold, studies raspberry, and Cassette's publications teal, each kept well apart from the green, amber, and red of accepted, suggested, and rejected (a test checks every palette and mode). Warnings and "suggested" are a warmer amber.
- Charts of grant amounts use a brighter gold fill.

### Fixed

- Keyboard hints on buttons were faded below WCAG AA contrast; they are now full strength.

## 0.1.0 (2026-11-07)

The first release. Tessera builds an organization's research graph from public data, with the evidence for every link, and gives curators fast tools to confirm it. It runs on a laptop or a single server.

**Highlights:** connectors for PubMed, iCite, NIH RePORTER, and ClinicalTrials.gov; a people directory from grant investigators, an optional roster, and discovered authors; explainable authorship suggestions; links between papers, grants, and studies; a fast, accessible web app in three palettes with light and dark versions; a keyboard-driven review queue; a JSON API; and user guides.

### Evaluation

- **Pilot check against ORCID iDs** at a mid-sized research institute (70 directory members, about 2,400 publications over five years; 588 matched papers labeled by the ORCID iD printed on them): the strong band was 98.3% precise at 93.7% recall (target 99%), and suggestions above the review threshold were 97.9% precise at 95.8% recall (target 90%). Seven of the nine strong-band disagreements appear to be people with a second ORCID iD rather than wrong matches. Details are in [spec/evidence-engine.md](spec/evidence-engine.md#pilot-check-against-orcid-ids-v010).
- **Synthetic organization with known answers:** the strong band is 100% precise with at least 99.8% recall, and candidate retrieval recall is 100%.
- **Speed on the pilot:** profile pages in 6 ms and search in 20 ms at the 95th percentile; in the review queue, the next suggestion's evidence appears in 22 ms and a decision is saved in 56 ms.

### Dependencies and licenses

Runtime dependencies: Alembic, DuckDB, FastAPI (with Starlette and Pydantic), HTTPX, PyArrow, SQLAlchemy, and Uvicorn, plus psycopg for the optional `postgres` extra. All are permissively licensed (MIT, BSD, Apache-2.0, PSF), except certifi (MPL-2.0, used unmodified) and psycopg (LGPL-3.0, optional and not bundled), both compatible with distributing Tessera under Apache-2.0. The web app ships no third-party code, fonts, or images.

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
- Review queue page: suggestions in priority order beside an evidence panel that highlights the matched author and their affiliation on the paper; keyboard flow (`j`/`k` to move, `a` accept, `r` reject, `d` defer, `u` undo, `o` open, `p` one person, `?` keys); the next items prefetched; accept all strong matches for a person, undone together in one step.
- Discovered people: people found in the data who are not in the directory, with the evidence for each (affiliated papers and years, recent titles, NIH grants, affiliation), search and filters, and promote or dismiss one at a time or in bulk, all reversible. Dismissed people are not proposed again.
- Decision log page and `GET /api/v1/decisions`: every decision about links and people, newest first.
- API: `GET /api/v1/candidates`, `POST /api/v1/candidates/decisions`, and `POST /api/v1/assertions/decisions` (one decision for many assertions, used for bulk undo).
- "Add a publication" on profiles (by PubMed ID), "Review suggestions" for one person, and promote or dismiss on a discovered person's profile.
- Sidebar counts of suggestions and discovered people waiting, kept current as decisions are made.
- Ledger migration 0002: the `dismissed` person status and the `person_event` log.
- Your organization's logo in the sidebar (`branding.logo_light` and `logo_dark`).
- User guides in `docs/`: getting started, describing your organization, curating, configuration, running Tessera, the API, and a command reference generated from the commands. Tests keep the command reference and the OpenAPI document current and check every link.

### Changed

- Appearance has two choices under a new Appearance panel (and the command palette): light, dark, or system, and a color palette. Three palettes ship, each with day and night versions: **Tessera** (the default; its dark mode is now a neutral graphite instead of blue-navy), **Harbor** (deep navy, teal, bright blue, purple, and gold), and **Cassette** (label paper and rainbow stripes by day, black labels and a sunset at night). The organization sets the default with `branding.theme`; each viewer can choose another. Every palette passes WCAG AA in both modes.
- `branding.accent_color` is now optional and, when set, replaces the palette's accent (lightened after dark, with text on it chosen for contrast).
- Decisions update the search index in place (member lists, person facets and counts, directory membership), so lists and search reflect them on the next request without a rebuild.
- `branding.accent_color` must be a CSS color (hex, keyword, or a color function); anything else is a configuration error.
- Ledger migrations on SQLite run with foreign keys off and verify every reference before committing, so a migration that rebuilds a table keeps the rows that point at it.
