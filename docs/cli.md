# Command reference

Every command, with its options. Run any command with `--help` for the same text. Global
options go before the command: `uv run tessera --config site.toml sync`.

This page is generated from the commands themselves.

## Global options

```text
usage: tessera [-h] [--version] [-c PATH] [-v | -q] COMMAND ...
```

| Argument | Meaning |
|---|---|
| `--version` | show program's version number and exit |
| `-c, --config PATH` | configuration file (default: $TESSERA_CONFIG or ./tessera.toml) |
| `-v, --verbose` | more output |
| `-q, --quiet` | only warnings and errors |

## `tessera init`

Create a configuration file and data home.

```text
usage: tessera init [-h] [--force]
```

| Argument | Meaning |
|---|---|
| `--force` | overwrite an existing config file |

## `tessera demo`

Build a fictional organization to explore (and to take screenshots of).

```text
usage: tessera demo [-h] [--force] [--seed SEED] [path]
```

| Argument | Meaning |
|---|---|
| `path` | where to build it (default: ./demo) |
| `--force` | replace a demo already there |
| `--seed SEED` | another seed, other people |

## `tessera doctor`

Check that this installation is ready.

```text
usage: tessera doctor [-h] [--online]
```

| Argument | Meaning |
|---|---|
| `--online` | also test public API reachability |

## `tessera db`

Ledger database maintenance.

```text
usage: tessera db [-h] ACTION ...
```

### `tessera db upgrade`

Apply pending migrations.

```text
usage: tessera db upgrade [-h]
```

### `tessera db current`

Show the schema revision.

```text
usage: tessera db current [-h]
```

## `tessera lake`

Lake (Parquet) maintenance.

```text
usage: tessera lake [-h] ACTION ...
```

### `tessera lake status`

Files and rows per table.

```text
usage: tessera lake status [-h]
```

### `tessera lake compact`

Rewrite tables to current rows only.

```text
usage: tessera lake compact [-h] [datasets ...]
```

| Argument | Meaning |
|---|---|
| `datasets` | datasets to compact (default: all) |

### `tessera lake gc`

Delete superseded files past retention.

```text
usage: tessera lake gc [-h] [--retention-days DAYS]
```

| Argument | Meaning |
|---|---|
| `--retention-days DAYS` | keep superseded files younger than this (default: 7) |

## `tessera ingest`

Fetch public data into the lake.

Fetch from a public source. SOURCE is pubmed, icite, reporter, ctgov, orcid, or 'all' (every enabled source in a sensible order). Without --mode, an organization sweep is run (or an ID fetch if --ids is given); iCite and ORCID read what the lake and the directory need.

```text
usage: tessera ingest [-h] [--mode MODE] [--ids ID [ID ...]] [--ids-file PATH] [--query QUERY]
                      [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--full] [--max-results N]
                      source
```

| Argument | Meaning |
|---|---|
| `source` | pubmed \| icite \| reporter \| ctgov \| orcid \| all |
| `--mode MODE` | org \| query \| ids \| linked or people (pubmed) \| pi (reporter) \| refresh (icite, orcid) |
| `--ids ID` | IDs to fetch |
| `--ids-file PATH` | file with one ID per line |
| `--query QUERY` | source query (PubMed syntax, Essie, or RePORTER JSON) |
| `--since YYYY-MM-DD` | start of the window to fetch |
| `--until YYYY-MM-DD` | end of the window to fetch |
| `--full` | ignore the saved checkpoint |
| `--max-results N` | stop after N records (for quick checks) |

## `tessera replay`

Rebuild lake records from a run's raw payloads.

```text
usage: tessera replay [-h] source run_id
```

| Argument | Meaning |
|---|---|
| `source` | pubmed \| icite \| reporter \| ctgov \| orcid |
| `run_id` | the run to replay (see `tessera runs`) |

## `tessera resolve`

Update people, links, and authorship suggestions from the lake.

```text
usage: tessera resolve [-h] [--people-only]
```

| Argument | Meaning |
|---|---|
| `--people-only` | stop after people |

## `tessera build`

Rebuild derived artifacts.

```text
usage: tessera build [-h] WHAT ...
```

### `tessera build search`

The search and serving index.

```text
usage: tessera build search [-h]
```

### `tessera build discovery`

Concept profiles, similar people, and the collaboration graph.

```text
usage: tessera build discovery [-h]
```

## `tessera sync`

Fetch every source and resolve, in the right order (the usual command).

```text
usage: tessera sync [-h] [--no-fetch] [--full]
```

| Argument | Meaning |
|---|---|
| `--no-fetch` | only resolve the lake |
| `--full` | ignore saved checkpoints |

## `tessera people`

The people directory.

```text
usage: tessera people [-h] ACTION ...
```

### `tessera people import`

Import a roster CSV file.

```text
usage: tessera people import [-h] path
```

| Argument | Meaning |
|---|---|
| `path` | CSV file with a header row |

### `tessera people public`

Give people a profile on the public site (or take it away).

```text
usage: tessera people public [-h] [--off] person [person ...]
```

| Argument | Meaning |
|---|---|
| `person` | IDs (per_…) |
| `--off` | take the profiles away |

### `tessera people list`

List people.

```text
usage: tessera people list [-h] [--status STATUS] [--limit LIMIT]
```

| Argument | Meaning |
|---|---|
| `--status STATUS` | active \| former \| candidate \| all |
| `--limit LIMIT` | at most this many (default: 50) |

## `tessera groups`

Groups: divisions, programs, labs, and other kinds.

```text
usage: tessera groups [-h] ACTION ...
```

### `tessera groups import`

Import groups or memberships from a CSV or JSON file.

Import groups (a CSV file with a 'name' column) or memberships (a CSV file with a 'group' column), or both (a JSON file with 'groups' and 'memberships'). Rows carry the source's own identifiers, so importing the same source again updates what it made earlier. See docs/groups.md for the columns.

```text
usage: tessera groups import [-h] --source SOURCE [--complete] [--dry-run] path
```

| Argument | Meaning |
|---|---|
| `path` | CSV or JSON file |
| `--source SOURCE` | a short name for where the file comes from, like hr |
| `--complete` | the file is the source's whole list: archive its missing groups and end its missing current memberships today |
| `--dry-run` | report what would change; change nothing |

### `tessera groups attribute`

Suggest which papers are each group's own (also run by sync).

```text
usage: tessera groups attribute [-h]
```

### `tessera groups public`

Give groups a page on the public site (or take it away).

```text
usage: tessera groups public [-h] [--off] groups [groups ...]
```

| Argument | Meaning |
|---|---|
| `groups` | addresses, codes, or IDs |
| `--off` | take the pages away |

### `tessera groups list`

List groups.

```text
usage: tessera groups list [-h] [--kind KIND] [--all]
```

| Argument | Meaning |
|---|---|
| `--kind KIND` | only groups of this kind |
| `--all` | include archived groups |

## `tessera digest`

Pending work for each account: groups' inboxes and review queues.

```text
usage: tessera digest [-h] ACTION ...
```

### `tessera digest show`

Print the digests.

```text
usage: tessera digest show [-h] [--account ACCOUNT]
```

| Argument | Meaning |
|---|---|
| `--account ACCOUNT` | only this account (username) |

### `tessera digest send`

Email each account with pending work its digest ([mail]); run it weekly.

```text
usage: tessera digest send [-h] [--account ACCOUNT] [--dry-run]
```

| Argument | Meaning |
|---|---|
| `--account ACCOUNT` | only this account (username) |
| `--dry-run` | print the messages instead of sending them |

## `tessera publish`

Write the public site: pages for the people and groups that opted in.

Write a static public site from an allowlisted projection: a page for every person and group that opted in, with citations and links rather than abstracts. The site is built and checked (structure, accessibility, links, and colors) before it replaces the published one. Run it daily, after sync.

```text
usage: tessera publish [-h] [--out OUT] [--check]
```

| Argument | Meaning |
|---|---|
| `--out OUT` | where to write it ([publish] out) |
| `--check` | build and check, but write nothing |

## `tessera report`

Insight reports: list them, freeze snapshots, verify and export them.

Reports are named sets of Insight figures over a cohort and a period. A snapshot freezes one with the exact inputs its figures were computed from, so it can be verified and exported long after the data has moved on.

```text
usage: tessera report [-h] ACTION ...
```

### `tessera report list`

Reports and their snapshots.

```text
usage: tessera report list [-h]
```

### `tessera report snapshot`

Freeze a report now.

```text
usage: tessera report snapshot [-h] [--note NOTE] report
```

| Argument | Meaning |
|---|---|
| `report` | the report's name or ID |
| `--note NOTE` | why it was taken |

### `tessera report verify`

Compute a snapshot again from its frozen inputs and compare.

```text
usage: tessera report verify [-h] snapshot
```

| Argument | Meaning |
|---|---|
| `snapshot` | the snapshot's ID |

### `tessera report export`

Write a snapshot's figures, records, and definitions to a file.

```text
usage: tessera report export [-h] [--format {csv,parquet,json}] [--out OUT] snapshot
```

| Argument | Meaning |
|---|---|
| `snapshot` | the snapshot's ID |
| `--format FORMAT` | (default: csv) |
| `--out OUT` | the file to write (default: its name) |

## `tessera users`

Local accounts and their roles.

```text
usage: tessera users [-h] ACTION ...
```

### `tessera users add`

Create an account (prompts for a password).

```text
usage: tessera users add [-h] [--name NAME] --role ROLE [--email EMAIL] [--person ID]
                         [--password-stdin]
                         username
```

| Argument | Meaning |
|---|---|
| `username` | lowercase letters, digits, dots, dashes, underscores |
| `--name NAME` | the name shown and recorded on decisions |
| `--role ROLE` | admin, curator, manager, researcher, or viewer; a curator for some people only is curator=group:<name, address, or code> or curator=person:<ID>; a manager is manager=group:<…>, or manager=group:<…>+below to manage the groups below too (repeatable) |
| `--email EMAIL` | contact address |
| `--person ID` | the person a researcher account belongs to |
| `--password-stdin` | read the password from standard input |

### `tessera users list`

List accounts and roles.

```text
usage: tessera users list [-h]
```

### `tessera users disable`

Sign an account out and block it.

```text
usage: tessera users disable [-h] username
```

| Argument | Meaning |
|---|---|
| `username` |  |

### `tessera users enable`

Unblock.

```text
usage: tessera users enable [-h] username
```

| Argument | Meaning |
|---|---|
| `username` |  |

### `tessera users reset-password`

Set a new password (signs the account out everywhere).

```text
usage: tessera users reset-password [-h] [--password-stdin] username
```

| Argument | Meaning |
|---|---|
| `username` |  |
| `--password-stdin` | read the password from standard input |

### `tessera users roles`

Replace an account's roles.

```text
usage: tessera users roles [-h] --role ROLE username
```

| Argument | Meaning |
|---|---|
| `username` |  |
| `--role ROLE` | admin, curator, manager, researcher, or viewer; a curator for some people only is curator=group:<name, address, or code> or curator=person:<ID>; a manager is manager=group:<…>, or manager=group:<…>+below to manage the groups below too (repeatable) |

## `tessera evidence`

The authorship model: learned weights and auto-accept.

```text
usage: tessera evidence [-h] ACTION ...
```

### `tessera evidence status`

The active model, the latest evaluation, and auto-accept.

```text
usage: tessera evidence status [-h]
```

### `tessera evidence evaluate`

Fit learned weights to decisions and compare on held-out people.

```text
usage: tessera evidence evaluate [-h]
```

### `tessera evidence release`

Make the evaluated learned model active (if it was better).

```text
usage: tessera evidence release [-h]
```

### `tessera evidence revert`

Go back to the hand-set weights.

```text
usage: tessera evidence revert [-h]
```

### `tessera evidence auto-accept`

Accept strong suggestions automatically, where precision allows.

```text
usage: tessera evidence auto-accept [-h] (--threshold THRESHOLD | --off | --undo BATCH)
```

| Argument | Meaning |
|---|---|
| `--threshold THRESHOLD` | switch on at this score (one the evaluation measured) |
| `--off` | switch off |
| `--undo BATCH` | undo one automatic batch |

## `tessera eval`

Evaluate matching quality.

```text
usage: tessera eval [-h] WHAT ...
```

### `tessera eval authorship`

Precision, recall, and calibration of authorship suggestions.

```text
usage: tessera eval authorship [-h] [--gold CSV]
```

| Argument | Meaning |
|---|---|
| `--gold CSV` | adjudicated pairs: person,pmid,label |

## `tessera runs`

Recent ingest runs.

```text
usage: tessera runs [-h] [--source SOURCE] [--limit LIMIT]
```

| Argument | Meaning |
|---|---|
| `--source SOURCE` | only this source |
| `--limit LIMIT` | at most this many (default: 20) |

## `tessera serve`

Run the web app and API.

```text
usage: tessera serve [-h] [--host HOST] [--port PORT] [--reload]
```

| Argument | Meaning |
|---|---|
| `--host HOST` | override server.host |
| `--port PORT` | override server.port |
| `--reload` | restart on code changes (development) |
