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

SQLite is the default ledger, and every test also runs on PostgreSQL. To run the whole suite on both, with a disposable PostgreSQL in podman or docker that is removed afterwards:

```bash
scripts/test-both.sh                          # arguments go to pytest on both runs
```

Or by hand: with `TESSERA_TEST_LEDGER=postgres`, every installation a test creates gets its own empty PostgreSQL database instead of `ledger.db` (see `tests/pg_ledgers.py`):

```bash
scripts/dev-postgres.sh                       # prints a connection URL
TESSERA_TEST_LEDGER=postgres TESSERA_TEST_POSTGRES_URL=<url> uv run --extra postgres pytest
scripts/dev-postgres.sh stop
```

Without `TESSERA_TEST_LEDGER`, `TESSERA_TEST_POSTGRES_URL` runs only the tests about PostgreSQL itself (`-m postgres`). A test that is about SQLite alone says so and is skipped on PostgreSQL; anything else that differs between the two is fixed in the code, not skipped.

### The API contract and upgrades

`tests/test_api_contract.py` checks every change against [`tests/api_contract.json`](tests/api_contract.json), which records what each stable API route accepts and answers with (see [Compatibility](docs/compatibility.md)). It fails on anything that would break a client: a route, parameter, or field taken away, a type changed, something newly required. When you add a stable route or field, record it:

```bash
UPDATE_CONTRACT=1 uv run pytest tests/test_api_contract.py
```

Recording only adds. Taking something out of the contract is an edit by hand, for a major version or a route past its sunset. A new route that is still settling belongs in `EXPERIMENTAL_PATHS` (`src/tessera/api/stability.py`).

Before a release, `scripts/check-upgrades.sh` installs every earlier release from its tag, makes an installation with it, upgrades it with this one, and checks that every ledger row, lake file, and raw response comes through (on SQLite, and for 0.7.0 on a disposable PostgreSQL). A migration that changes rows on purpose says so to the check (`intended` and `moved` in `scripts/check_upgrades.py`), so that the change is confirmed rather than excused.

### Web app tests

The web app has no build step. `tests/test_web.py` checks its safety rules (no inline code, no resources from other origins, no HTML parsing of data), that every module import resolves (including worker scripts), that every module is loaded by something, and the size budgets: one for what the app loads at start and one for each part loaded on first use, such as the graph views. When Node.js is installed it also syntax-checks every module and runs the JavaScript unit tests in `tests/js` (`node --test tests/js/*.test.mjs`).

The documentation site is built from the guides with `node docs/site/build.mjs` (into `site/`, which git ignores); the build fails on any broken link or anchor. Write links between guides as GitHub reads them (`curation.md#bulk-decisions`); the build rewrites them for the site.

### Documentation

User guides live in [`docs/`](docs/README.md) as plain Markdown that reads well on GitHub; a future product site is built from the same files. Write links between guides as relative links, with anchors where useful. Tests check that every relative link and anchor resolves, and that the generated [command reference](docs/cli.md) and [OpenAPI document](docs/openapi.json) are current. After changing a command or an endpoint, regenerate them:

```bash
UPDATE_DOCS=1 uv run pytest tests/test_docs.py
```

### Screenshots and the accessibility audit

The guides' screenshots are taken from the real app serving the demo organization, in light and dark, by `node docs/capture/capture.mjs` (Node.js 22 and Chrome; its options are described at the top of the file). The same tool audits every scene for WCAG 2.2 AA: accessible names from Chrome's accessibility tree, landmarks and headings, the contrast of rendered text, target sizes, and a real Tab walk for visible, unhidden focus and keyboard traps (`docs/capture/audit.mjs`). Run it after changing the web app; it exits non-zero on any finding:

```bash
node docs/capture/capture.mjs --audit-only        # audit every scene, write no screenshots
node docs/capture/capture.mjs --audit             # take the screenshots and audit them
```

What a machine cannot judge, such as whether a name is meaningful or how a screen reader announces a change, needs a pass by hand with the keyboard and a screen reader: [spec/screen-reader-check.md](spec/screen-reader-check.md) is a script for one.

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
