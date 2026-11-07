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
- Placeholder web page with light, dark, and system themes.
- Development tools: private-terms check with optional git hooks, and a disposable PostgreSQL container script.
