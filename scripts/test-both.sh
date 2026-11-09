#!/bin/sh
# Run the whole test suite on SQLite, then again with every ledger on a disposable PostgreSQL
# (podman or docker), which is removed afterwards. Arguments go to pytest on both runs.
#
#   scripts/test-both.sh                 everything
#   scripts/test-both.sh tests/test_api.py -x
set -eu
cd "$(dirname "$0")/.."

echo "== SQLite"
uv run pytest -q "$@"

echo "== PostgreSQL"
URL=$(sh scripts/dev-postgres.sh | tail -n 1)
trap 'sh scripts/dev-postgres.sh stop >/dev/null' EXIT
TESSERA_TEST_LEDGER=postgres TESSERA_TEST_POSTGRES_URL="$URL" uv run --extra postgres pytest -q "$@"
