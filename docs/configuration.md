# Configuration

Tessera reads one file, `tessera.toml`. `uv run tessera init` writes a commented starting point; the same file is in the repository as [`tessera.example.toml`](../tessera.example.toml). Every setting has a sensible default except the organization profile.

Tessera checks the whole file when it starts and reports every problem at once, with the setting's name, so a typo never fails silently. Unknown sections and settings are errors too.

## Where the file is

Commands look for the configuration in this order:

1. `--config PATH` (or `-c PATH`) on the command line;
2. the `TESSERA_CONFIG` environment variable;
3. `tessera.toml` in the current folder.

Relative paths inside the file (the data home, logo files) are relative to the file's own folder.

## Environment variables

A simple setting can be overridden by an environment variable named `TESSERA_<SECTION>_<SETTING>`, which is handy for containers and services:

```bash
TESSERA_SERVER_PORT=9000 uv run tessera serve
TESSERA_DATA_HOME=/srv/tessera/data uv run tessera sync
TESSERA_LEDGER_URL="postgresql+psycopg://tessera@db/tessera" uv run tessera serve
```

Secrets never go in the file. The only secret Tessera uses, an optional NCBI API key, is read from the environment variable named by `[sources.pubmed] api_key_env`.

## `[organization]`

How the organization appears in public records. See [Describing your organization](organization-profile.md) for how to choose values.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `name` | text | (required) | Full name, shown in the app and used as an alias |
| `short_name` | text | `""` | Short form for the sidebar |
| `aliases` | list | `[]` | Names as written in affiliations, sponsor and site names |
| `affiliation_patterns` | list | `[]` | Affiliation patterns; `*` matches any run of characters in a word |
| `reporter_org_names` | list | `[]` | Organization names exactly as NIH RePORTER writes them |
| `ctgov_org_names` | list | `[]` | Sponsor, collaborator, and site names in ClinicalTrials.gov (falls back to `aliases`) |
| `email_domains` | list | `[]` | Domains whose addresses count as evidence of membership |
| `exclusions` | list | `[]` | Look-alike organizations that contain one of the aliases |

`name` and at least one of `aliases`, `affiliation_patterns`, or `reporter_org_names` are required, so Tessera can recognize the organization.

## `[branding]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `product_name` | text | `"Tessera"` | The product's name in the sidebar and page titles |
| `theme` | text | `"tessera"` | The default color palette: `tessera`, `harbor`, or `cassette` |
| `accent_color` | color | (none) | Replaces the palette's accent: a hex value, a color name, or `rgb()`, `hsl()`, or `oklch()` |
| `logo_light` | path | (none) | Your logo, shown in the sidebar in place of the Tessera mark and name (SVG, PNG, JPEG, or WebP) |
| `logo_dark` | path | (none) | The logo for dark mode; the light one is used if absent |
| `show_powered_by` | true/false | `true` | The small "Powered by Tessera" line in the sidebar |

**Palettes.** Each palette has a light and a dark version, and every viewer can switch palettes and modes for themselves under **Appearance** in the top bar; `theme` sets what everyone sees first.

| Palette | By day | After dark |
|---|---|---|
| `tessera` | Calm cool neutrals with an indigo accent | Neutral graphite with a soft indigo accent |
| `harbor` | White and light grey, dark navy text, teal actions, a navy-to-gold rule | Deep navy, bright blue actions, gold and purple accents |
| `cassette` | Cream label paper, a red-orange accent, rainbow stripes | Black labels, an orange accent, sunset stripes |

A configured `accent_color` is used as given by day and lightened for dark mode; text on it is chosen for contrast. Logos look best at about 190 by 30 pixels.

## `[data]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `home` | path | `"var"` | The data home: raw downloads, the Parquet lake, the ledger (with SQLite), the search index, and logs. See [Running Tessera](operations.md#the-data-home) |

## `[server]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `host` | text | `"127.0.0.1"` | Address to listen on. Tessera has no sign-in yet, so keep it on this machine; `tessera doctor` warns about anything else |
| `port` | number | `8000` | Port to listen on |
| `operator` | text | `"Operator"` | The name recorded on every curation decision |

## `[ledger]`

The ledger holds people, links, and decisions.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `url` | text | `""` | Empty means SQLite at `<data home>/ledger.db`. For PostgreSQL: `postgresql+psycopg://user@host/dbname` |

PostgreSQL support needs the `postgres` extra: `uv sync --extra postgres`. See [Running Tessera](operations.md#postgresql).

## `[sources]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `contact_email` | text | `""` | Sent with requests to the public APIs, as their usage policies ask. Set it to an address that can be reached |

Each source has its own table, `[sources.pubmed]`, `[sources.icite]`, `[sources.reporter]`, and `[sources.ctgov]`:

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | true/false | `true` | Whether `sync` and `ingest all` use the source |
| `api_key_env` | text | `"NCBI_API_KEY"` for PubMed, else `""` | The environment variable holding an optional API key |

An NCBI API key (created under *API Key Management* in your NCBI account settings) raises PubMed's request limit from three to ten per second, which shortens the first sync of a large organization considerably.

## `[sync]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `window_years` | number | `5` | How many years of history the first sync collects. Later syncs fetch only what changed |

## `[evidence]`

Thresholds for authorship suggestions. See [Curating](curation.md#how-suggestions-are-scored).

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `strong_threshold` | 0 to 1 | `0.95` | Suggestions at or above this likelihood are *strong* |
| `review_threshold` | 0 to 1 | `0.60` | Suggestions below this are *unlikely* and hidden by default |
| `store_threshold` | 0 to 1 | `0.05` | Candidates below this are not recorded at all |
| `discover_min_papers` | number | `2` | Affiliated papers needed before a person is proposed under Discovered people, or a grant co-investigator is promoted |

Changing a threshold takes effect at the next `tessera resolve` or `sync`; decisions already made are not affected.
