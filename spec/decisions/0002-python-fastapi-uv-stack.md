# ADR-0002: Python, uv, FastAPI, and a small dependency budget

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Tessera must be easy for research organizations to run and extend. Their technical staff most often know Python, and the data work (parsing, matching, analytics) is well served by Python's ecosystem. Every dependency is a future upgrade, a security surface, and a licensing review, so the set must stay small.

## Decision

- **Language:** Python 3.13 (`requires-python >= 3.12`).
- **Environment and packaging:** `uv` for everything: `uv add`, `uv run`, `uv lock`, `uv build`. No direct `pip` usage.
- **Web:** FastAPI served by uvicorn.
- **HTTP client:** `httpx`, for the connectors and as the API test client, so there is one HTTP library across runtime and tests. Retries and rate limiting are implemented in Tessera's shared connector client rather than delegated to a transport plugin, because each source needs its own policy.
- **CLI:** standard-library `argparse`.
- **Configuration:** TOML read with standard-library `tomllib`.
- **Tooling:** `ruff` for linting and formatting, `pytest` for tests.
- **Runtime dependency budget:** `fastapi`, `uvicorn`, `httpx`, `pyarrow`, `duckdb`, `sqlalchemy`, `alembic`. Optional extra `postgres` adds `psycopg`. Any addition requires a new ADR.
- **License:** Apache 2.0. Dependencies must have licenses compatible with redistribution under Apache 2.0.

## Consequences

- Installation is a single `uv sync`.
- Some conveniences (task queues, ORMs, template engines) are deliberately absent and must be justified if needed later.
- Password hashing, signed sessions, and similar needs in later waves use the standard library (`hashlib.scrypt`, `hmac`, `secrets`) where it is sufficient.

## Alternatives

- **`requests`:** mature, but would mean two HTTP libraries, since the API test client needs `httpx`.
- **A full ORM:** more abstraction than Tessera's mostly set-oriented data work needs. SQLAlchemy Core gives portability without it.
- **Click or Typer for the CLI:** pleasant, but `argparse` is sufficient and adds nothing to install.
