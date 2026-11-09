#!/bin/sh
# Upgrade an installation made by every earlier release to this one, and check nothing is lost:
# on SQLite, and 0.7.0's moved to a disposable PostgreSQL (podman or docker), removed afterwards.
# Arguments go to check_upgrades.py (for example, the releases to check).
#
#   scripts/check-upgrades.sh
set -eu
cd "$(dirname "$0")/.."
URL=$(sh scripts/dev-postgres.sh | tail -n 1)
trap 'sh scripts/dev-postgres.sh stop >/dev/null' EXIT
uv run --extra postgres python scripts/check_upgrades.py --postgres "$URL" "$@"
