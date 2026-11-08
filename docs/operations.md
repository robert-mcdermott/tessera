# Running Tessera

Tessera is one Python application and one folder of data. This guide covers what is in that folder, how to keep data fresh, backups, upgrades, PostgreSQL, and what to check when something goes wrong.

## The data home

Everything Tessera stores lives in the data home (`[data] home`, `./var` by default):

```text
var/
  raw/        the public sources' responses, exactly as received, one folder per run
  lake/       normalized records in Parquet files, one folder per table
  ledger.db   people, links, decisions, and run history (SQLite; absent with PostgreSQL)
  derived/    the search index and concept profiles, rebuilt from the lake and ledger
  logs/       logs, one JSON object per line
```

These parts are not equally precious:

- **The ledger is the only part that holds human work**: people, decisions, notes. Back it up.
- **The lake and raw downloads** come from public sources and can be fetched again. Keeping `raw/` lets `tessera replay` rebuild the lake without the network.
- **`derived/`** is always rebuildable with `uv run tessera build search` and `uv run tessera build discovery`.

Only one data job (a sync, ingest, resolve, or build) runs at a time per data home; a second one stops at once and says another job is running. The web app keeps working during jobs.

## Keeping data fresh

Run `uv run tessera sync` on a schedule. After the first sync it fetches only what changed, so a daily run usually takes a few minutes. Each source's expected freshness appears on the **Sources** page, which warns when a source is overdue or its last run failed.

A daily cron entry (Linux or macOS):

```cron
30 2 * * * cd /srv/tessera && /usr/local/bin/uv run tessera --quiet sync
```

A systemd timer works the same way: run `uv run tessera --quiet sync` from the folder that holds `tessera.toml`, as the user who owns the data home.

`uv run tessera runs` lists recent runs with their status and counts. A run that ends *partial* fetched some but not all of its batches; the next sync retries from the last complete checkpoint, so nothing is skipped.

## Serving the app

```bash
uv run tessera serve
```

Tessera listens on `127.0.0.1:8000` by default (`[server] host` and `port`).

## Accounts and sign-in

Out of the box Tessera runs in *single-operator mode*: no sign-in, answering only on this machine, with every decision recorded under `[server] operator`. That suits one curator on a laptop. For several people, or for a server others reach, turn on accounts:

1. Create the first admin (you are asked for a password of at least 12 characters):

   ```bash
   uv run tessera users add ada --name "Ada Okafor" --role admin
   ```

2. Set `accounts = true` under `[server]`, and `host` to the address to listen on (for example `0.0.0.0`).
3. Put Tessera behind HTTPS, typically a reverse proxy such as nginx or Caddy that forwards to it, so that passwords and session cookies travel encrypted. Have the proxy pass the original host (`Host`, or `X-Forwarded-Host`) and `X-Forwarded-Proto: https`: Tessera refuses changes whose origin does not match the host, and marks its cookie `Secure` over HTTPS. With the proxy on the same machine, run Tessera on `127.0.0.1` and let the proxy face the network. `tessera doctor` reminds you when accounts are on and Tessera listens beyond this machine.
4. Restart `tessera serve`. Everyone now signs in; admins add the others under **Admin → Accounts**, or from the command line.

Roles add up:

| Role | May |
|---|---|
| Viewer | Read everything: people, records, evidence, search |
| Researcher | Also review the suggestions about their own profile (the account is linked to their person) |
| Curator | Decide for everyone, or, when the role is limited to a unit or a person, only for them; curators for everyone also handle discovered people and duplicates |
| Admin | Everything, including accounts |

```bash
uv run tessera users add cora --name "Cora Lindqvist" --role "curator=unit:Department of Medicine"
uv run tessera users add ada.okafor --role researcher --person per_01…
uv run tessera users list
uv run tessera users reset-password cora     # signs her out everywhere
uv run tessera users disable cora            # also signs her out
uv run tessera users roles cora --role curator --role viewer
```

Sessions end after `session_idle_hours` without use (12 by default) and after `session_max_days` in any case (7). After five failed attempts for one username or address, sign-in is refused for 15 minutes. Tessera will not start with accounts on and no active admin, nor listen beyond this machine with accounts off. See [ADR-0009](../spec/decisions/0009-local-accounts-and-sessions.md) for the design.

## Backups

With SQLite, back up `ledger.db` with SQLite's own backup command, which is safe while Tessera runs:

```bash
sqlite3 var/ledger.db ".backup 'backups/ledger-$(date +%F).db'"
```

Copying the whole data home while no job is running also works. With PostgreSQL, use `pg_dump` as for any database.

To restore, stop Tessera, put the ledger back, and run `uv run tessera build search` and `uv run tessera build discovery` to rebuild the index and discovery data from it.

## Upgrading

```bash
git pull
uv sync
uv run tessera db upgrade      # apply ledger migrations, if any
uv run tessera build search    # rebuild the index with the new version
uv run tessera build discovery # and concept profiles and the graph
```

`tessera doctor` and the web app's health check (`/api/v1/health`) report a ledger that needs `db upgrade`. Migrations keep every row: they are tested on SQLite and PostgreSQL with a populated ledger, upgrading and downgrading. Back up the ledger before upgrading anyway. The [changelog](../CHANGELOG.md) notes anything that needs more than these steps.

## PostgreSQL

SQLite suits one organization on one machine well. To keep the ledger in PostgreSQL instead:

```bash
uv sync --extra postgres
```

Then set `[ledger] url = "postgresql+psycopg://tessera@dbhost/tessera"` (or `TESSERA_LEDGER_URL`) and run `uv run tessera db upgrade` to create the schema. Put the password in the standard `PGPASSWORD` variable or a `.pgpass` file rather than in the URL. The lake and search index stay in the data home either way.

## Lake maintenance

The lake keeps earlier versions of records when sources update them. Occasionally:

```bash
uv run tessera lake status     # files and rows per table
uv run tessera lake compact    # rewrite each table to current rows only
uv run tessera lake gc         # delete superseded files older than 7 days
```

## When something goes wrong

- **`tessera doctor`** checks the configuration, the data home, the ledger schema, and the database engines Tessera relies on (SQLite full-text search and DuckDB); `tessera doctor --online` also checks that each public source can be reached.
- **`tessera runs`** and the **Sources** page show failed or partial runs with the source's error.
- **Logs** are in `<data home>/logs`. Add `-v` to any command for more detail.
- **A source changed or was down:** run `uv run tessera sync` again; completed work is not repeated.
- **Search looks out of date:** `uv run tessera build search` rebuilds it from the lake and ledger in seconds.
