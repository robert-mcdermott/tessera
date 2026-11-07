#!/bin/sh
# Start (or reuse) a disposable PostgreSQL container for running Tessera's tests
# against PostgreSQL. Works with podman or docker. Development use only.
#
#   scripts/dev-postgres.sh          start it and print the test URL
#   scripts/dev-postgres.sh stop     stop and remove it
#
# Then:  TESSERA_TEST_POSTGRES_URL=<printed url> uv run pytest -m postgres
set -eu

NAME="${TESSERA_PG_CONTAINER:-tessera-pg-test}"
PORT="${TESSERA_PG_PORT:-55434}"
IMAGE="${TESSERA_PG_IMAGE:-docker.io/library/postgres:16}"
PASSWORD="${TESSERA_PG_PASSWORD:-tessera-dev}"

if command -v podman >/dev/null 2>&1; then ENGINE=podman
elif command -v docker >/dev/null 2>&1; then ENGINE=docker
else echo "Neither podman nor docker is installed." >&2; exit 1
fi

if [ "${1:-start}" = "stop" ]; then
  "$ENGINE" rm -f "$NAME" >/dev/null 2>&1 || true
  echo "Removed $NAME"
  exit 0
fi

if ! "$ENGINE" container exists "$NAME" 2>/dev/null && \
   ! "$ENGINE" inspect "$NAME" >/dev/null 2>&1; then
  "$ENGINE" run -d --rm --name "$NAME" \
    -e POSTGRES_USER=tessera -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=tessera_test \
    -p "127.0.0.1:$PORT:5432" "$IMAGE" >/dev/null
fi

printf "Waiting for PostgreSQL on 127.0.0.1:%s " "$PORT"
i=0
until "$ENGINE" exec "$NAME" pg_isready -U tessera -d tessera_test >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && { echo " timed out" >&2; exit 1; }
  printf "."
  sleep 1
done
echo " ready"
echo "postgresql+psycopg://tessera:$PASSWORD@127.0.0.1:$PORT/tessera_test"
