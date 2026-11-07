# Contributing to Tessera

Thanks for helping. This guide covers the development setup and the conventions the project follows. The *why* behind the design lives in [spec/](spec/); start with [spec/README.md](spec/README.md).

## Setup

Tessera uses [uv](https://docs.astral.sh/uv/) for everything Python.

```bash
uv sync                 # create .venv with runtime and dev dependencies
uv run tessera init     # writes ./tessera.toml (gitignored) and a data home in ./var
uv run tessera doctor   # checks the installation
uv run tessera serve    # http://127.0.0.1:8000
```

Add dependencies with `uv add` (runtime) or `uv add --dev` (development). A new runtime dependency needs an ADR; see [process.md](spec/process.md).

## Tests and checks

```bash
uv run pytest                 # offline test suite (never touches the network)
uv run ruff check .           # lint
uv run ruff format .          # format
python3 scripts/check_terms.py  # private-terms check (see below)
```

### PostgreSQL

SQLite is the default ledger. To run the ledger tests against PostgreSQL, start a disposable container with podman or docker:

```bash
scripts/dev-postgres.sh                       # prints a connection URL
TESSERA_TEST_POSTGRES_URL=<url> uv run --extra postgres pytest -m postgres
scripts/dev-postgres.sh stop
```

### Live API tests

Tests that call public APIs are marked `network` and are skipped by default. Run them deliberately with `uv run pytest -m network`.

## Private-terms check

The repository is public. Some words must never appear in it, and that list is itself private, so it lives outside version control in `.private/terms.txt` (one term per line; `#` comments; prefix `=` for a case-sensitive match). `scripts/check_terms.py` scans the working tree, staged changes (`--staged`), or a commit message (`--message FILE`) against it.

To run it automatically on every commit, install the git hooks once:

```bash
python3 scripts/check_terms.py --install-hooks
```

## Conventions

- **Waves and slices.** Work is planned in [spec/roadmap.md](spec/roadmap.md) and [spec/waves/](spec/waves/). Update the wave file's checklist as slices land.
- **Definition of done.** See [spec/process.md](spec/process.md#definition-of-done-every-slice).
- **Commits.** A short imperative summary line, a blank line, then what and why in plain prose.
- **Generic content only.** Examples use *Example University* and `example.edu`. No real organization's configuration, no personal data in fixtures, no secrets.
- **Changelog.** Add a line under *Unreleased* in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
