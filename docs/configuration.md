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
| `applicant_types` | list | `[]` | Who the organization is when it applies for funding, in Grants.gov's applicant type codes, so that [Opportunities](opportunities.md) can say whether it may apply. Leave empty to have every opportunity's applicant types checked by hand |

`name` and at least one of `aliases`, `affiliation_patterns`, or `reporter_org_names` are required, so Tessera can recognize the organization.

Grants.gov's applicant type codes, for `applicant_types`:

| Code | Applicant |
|---|---|
| `00`, `01`, `02`, `04` | State, county, city or township, and special district governments |
| `05` | Independent school districts |
| `06` | Public and state-controlled institutions of higher education |
| `07`, `11` | Federally recognized Native American tribal governments; other Native American tribal organizations |
| `08` | Public and Indian housing authorities |
| `12` | Nonprofits with 501(c)(3) status, other than institutions of higher education |
| `13` | Nonprofits without 501(c)(3) status, other than institutions of higher education |
| `20` | Private institutions of higher education |
| `21` | Individuals |
| `22`, `23` | For-profit organizations other than small businesses; small businesses |
| `25`, `99` | Others named in the eligibility notes; unrestricted |

A private university is usually `["20"]`; an independent nonprofit research institute, `["12"]`.

## `[branding]`

Admins can set all of this in the app, with a live preview and contrast checks, under **Admin → Branding**; see [Look and words](branding.md), which also has guidance for logos.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `product_name` | text | `"Tessera"` | The product's name in the sidebar and page titles |
| `theme` | text | `"tessera"` | The default color palette: `tessera`, `harbor`, or `cassette` |
| `accent_color` | color | (none) | Replaces the palette's accent: a hex value, a color name, or `rgb()`, `hsl()`, or `oklch()` |
| `logo_light` | path | (none) | Your logo, shown in the sidebar in place of the Tessera mark and name (SVG, PNG, JPEG, or WebP) |
| `logo_dark` | path | (none) | The logo for dark mode; the light one is used if absent |
| `show_powered_by` | true/false | `true` | The small "Powered by Tessera" line in the sidebar |
| `favicon` | path | (none) | The icon in browser tabs, for the app and the public site (SVG, PNG, or ICO) |
| `social_image` | path | (none) | The picture shown when someone shares a link to the public site (PNG or JPEG, 1200 by 630 pixels) |
| `footer_links` | list | (none) | Links for the foot of the app's sidebar and of every public page, such as privacy and accessibility statements |

**Palettes.** Each palette has a light and a dark version, and every viewer can switch palettes and modes for themselves under **Appearance** in the top bar; `theme` sets what everyone sees first.

| Palette | By day | After dark |
|---|---|---|
| `tessera` | Calm cool neutrals with an indigo accent | Neutral graphite with a soft indigo accent |
| `harbor` | White and light grey, dark navy text, teal actions, a navy-to-gold rule | Deep navy, bright blue actions, gold and purple accents |
| `cassette` | Cream label paper, a red-orange accent, a thin sunset stripe | Black labels, an orange accent, the same sunset stripe |

A configured `accent_color` is used as given by day and lightened for dark mode. It also colors links and labels, so choose one that reads on white at a contrast of at least 4.5:1, such as a deep blue or teal rather than a yellow. Write it as a hex value so that text on accent-colored buttons can be chosen for contrast; with other forms that text is white. Tessera and Harbor suit public and leadership views best; Cassette is playful by design. Logos look best at about 190 by 30 pixels.

**Your own colors.** `[branding.palette.day]` and `[branding.palette.night]` set the organization's colors, by name, as hex values. Any you leave out keep Tessera's. They apply over the default (`tessera`) theme in the app, and to the whole public site.

| Color | Used for |
|---|---|
| `bg`, `surface` | The page, and cards and panels on it |
| `text`, `muted` | Text, and secondary text |
| `border` | Lines between things |
| `accent`, `on_accent` | Links, buttons, and highlights, and text on them |
| `stripe` | The thin band at the top of the app and under the public site's header |
| `person`, `publication`, `grant`, `study` | Each kind of record, as text and icons |

```toml
[branding]
favicon = "brand/favicon.svg"
social_image = "brand/social.png"
footer_links = [
  { label = "Privacy", url = "https://www.example.edu/privacy" },
  { label = "Accessibility", url = "https://www.example.edu/accessibility" },
]

[branding.palette.day]
accent = "#0b6e4f"
stripe = "#1d3557"

[branding.palette.night]
accent = "#7fd6b4"
```

Every pair of colors that carries text must meet WCAG 2.2 level AA: 4.5:1 for text, links, and record colors on the page and on cards, and for text on the accent; 3:1 for the stripe against the page. `tessera doctor` checks each pair by day and by night and fails any below it, and `tessera publish` will not publish a site whose colors fail.

## `[data]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `home` | path | `"var"` | The data home: raw downloads, the Parquet lake, the ledger (with SQLite), the search index, and logs. See [Running Tessera](operations.md#the-data-home) |

## `[server]`

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `host` | text | `"127.0.0.1"` | Address to listen on. Anything other than this machine needs `accounts = true` |
| `port` | number | `8000` | Port to listen on |
| `operator` | text | `"Operator"` | Without accounts, the name recorded on every curation decision |
| `accounts` | true/false | `false` | Require sign-in with local accounts and roles; see [Accounts and sign-in](operations.md#accounts-and-sign-in) |
| `session_idle_hours` | number | `12` | A session ends this long after it was last used (1 to 720) |
| `session_max_days` | number | `7` | A session never lasts longer than this from sign-in (1 to 90) |

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

Each source has its own table, `[sources.pubmed]`, `[sources.icite]`, `[sources.reporter]`, `[sources.ctgov]`, `[sources.orcid]`, `[sources.mesh]` (NLM's MeSH vocabulary, one 17 MB download a year), and `[sources.grants_gov]` (federal funding opportunities):

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | true/false | `true` | Whether `sync` and `ingest all` use the source |
| `api_key_env` | text | `"NCBI_API_KEY"` for PubMed, else `""` | The environment variable holding an optional API key |

An NCBI API key (created under *API Key Management* in your NCBI account settings) raises PubMed's request limit from three to ten per second, which shortens the first sync of a large organization considerably.

`[sources.grants_gov]` can narrow the opportunities Tessera keeps. Both lists are empty by default, which keeps every forecast and posted federal opportunity (about 1,500):

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `categories` | list of text | `[]` | Grants.gov funding categories, such as `"HL"` (health) and `"ST"` (science and technology) |
| `agencies` | list of text | `[]` | Agency codes, or their beginnings: `"HHS-NIH"` keeps every NIH institute's, `"NSF"` the National Science Foundation's |

The first sync reads every opportunity's details, about one a second; later syncs read only those that are new or changed, or were last read two weeks ago.

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

## `[discovery]`

Concept profiles and similar people. See [Finding experts](discovery.md).

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `half_life_years` | 0.5 to 50 | `5.0` | A paper's weight in a profile halves every this many years |
| `likely_weight` | 0 to 1 | `0.5` | How much a strong suggestion not yet confirmed counts, relative to a confirmed paper. `0` builds profiles from confirmed papers only |
| `similarity_floor` | 0 to below 1 | `0.10` | Pairs of people less similar than this are not listed as similar |
| `similar_per_person` | 1 to 200 | `25` | At most this many similar people are kept for each person |

Changes take effect at the next `tessera sync` or `tessera build discovery`.

## `[vocabulary]`

What the app calls groups, the kinds of group it offers, and the roles people hold in them (see [Groups](groups.md)). Groups and memberships store the keys, so you can rename a label at any time; keep its key.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `preset` | text | (none) | Start from the words of a kind of organization: `academic_medical_center`, `university`, or `research_institute`; settings given beside it replace the preset's. See [Look and words](branding.md#the-words) |
| `group`, `groups` | text | `Group`, `Groups` | What the app calls one group and several |
| `leader_roles` | list of role keys | `["lead", "co_lead"]` | Roles listed as a group's leaders |
| `default_role` | role key | `member` | The role of a membership that names none |
| `attributable_kinds` | list of kind keys | `["program", "center", "core", "lab", "team"]` | Kinds of group whose members' papers are suggested as the group's own; see [Which papers are the group's](groups.md#which-papers-are-the-groups) |

`[vocabulary.kinds]` lists the kinds of group, as `key = "Label"`, in the order the app offers them. The defaults are `division` (Division), `department` (Department), `program` (Program), `center` (Center), `institute` (Institute), `core` (Shared resource), `lab` (Lab), and `team` (Team). Roster rows that name no kind are departments, or the first kind when you have no `department`.

`[vocabulary.roles]` lists the membership roles the same way. The defaults are `lead` (Lead), `co_lead` (Co-lead), `member` (Member), `staff` (Staff), `trainee` (Trainee), and `affiliate` (Affiliate).

Keys are lowercase letters, digits, and underscores. Replacing a table replaces the defaults entirely, and `leader_roles` and `default_role` must name roles in it.

## `[mail]`

A mail server for the weekly digest of pending work (`tessera digest send`). Without a `host`, nothing is sent and digests stay in the app's **Inbox**.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `host` | text | empty | The SMTP server |
| `port` | number | `587` | Its port |
| `username` | text | empty | The user to sign in as, if the server needs one |
| `password_env` | text | empty | The name of the environment variable that holds the password; the password itself never goes in the file |
| `sender` | address | empty | The From address, such as `Tessera <tessera@example.edu>`; required with a `host` |
| `starttls` | true or false | `true` | Upgrade the connection with STARTTLS |
| `ssl` | true or false | `false` | Connect with TLS from the start (usually port 465) instead of STARTTLS |
| `base_url` | address | empty | The app's address, such as `https://tessera.example.edu`, for links in messages |

```toml
[mail]
host = "smtp.example.edu"
username = "tessera"
password_env = "TESSERA_SMTP_PASSWORD"
sender = "Tessera <tessera@example.edu>"
base_url = "https://tessera.example.edu"
```

## `[publish]`

The static public site (`tessera publish`); see [The public site](public-site.md).

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `out` | path | `<data home>/public` | Where the site is written |
| `base_url` | address | empty | Where it is served, such as `https://research.example.edu`; needed for the sitemap and canonical links |
| `title` | text | the organization's name | The site's title |
| `intro` | text | empty | A sentence or two for the home page (at most 1,000 characters) |

## `[alerts]`

Who hears when something goes wrong; see [Running Tessera](operations.md#alerts). Alerts are emailed through `[mail]`.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `email` | list of addresses | empty | Who is emailed when a sync has failures, a source goes stale, or backups lapse. Empty: problems show in Admin → Operations only |
| `backup_days` | number | `7` | Warn when the last `tessera backup` is older than this many days (0 to 365; 0 never warns) |

## `[ai]`

The optional assistant; see [The assistant](assistant.md). Without this section, or with `enabled = false`, nothing of it exists in the app, the API, or the public site. The example configuration ends with this section commented out, ready to turn on.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | true/false | `false` | Turns the assistant on |
| `provider` | text | (required) | The protocol: `openai` (Chat Completions), `anthropic` (Messages), or `bedrock` (Amazon Bedrock's Converse API) |
| `model` | text | (required) | The model's name, or Bedrock's model or inference profile ID |
| `base_url` | text | the official service | The endpoint's root, such as `http://127.0.0.1:11434/v1` (`openai`) or `http://127.0.0.1:11434` (`anthropic`) |
| `api_key_env` | text | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `AWS_BEARER_TOKEN_BEDROCK` | The environment variable holding the key; with no key set, none is sent (fine for local servers) |
| `region` | text | `""` | Bedrock: the AWS region (required for Bedrock) |
| `headers` | list | `[]` | Extra `"Name: value"` headers for a gateway; never a key |
| `max_tokens` | number | `1500` | The longest answer, in tokens |
| `temperature` | number | `0.2` | 0 to 2; lower is steadier |
| `timeout_seconds` | number | `120` | How long to wait for the provider |
| `daily_limit` | number | `100` | Questions per person per day; 0 for no limit |
| `roles` | list | `[]` | The roles that may use it (`admin`, `curator`, `manager`, `researcher`, `viewer`); empty for everyone who may read. Admins always may |
| `log_days` | number | `365` | How long exchanges stay in the log |

A setting named like a key (`api_key`, `secret`, `token`, `password`) is refused, and so is an `Authorization` header: keys belong in the environment.

## `[auth.oidc]`

Single sign-on with an OpenID Connect provider, beside local accounts; it needs `[server] accounts = true`. See [Single sign-on](operations.md#single-sign-on) for setting it up with Microsoft Entra ID, Okta, Google, or Keycloak.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `issuer` | text | `""` | The provider's issuer address; setting it turns single sign-on on. `https://` (or `http://` on this machine, for testing) |
| `client_id` | text | (required) | The application's client ID at the provider |
| `client_secret_env` | text | `TESSERA_OIDC_CLIENT_SECRET` | The environment variable holding the client secret; a `client_secret` in the file is refused |
| `label` | text | `Sign in with single sign-on` | The button on the sign-in page |
| `redirect_url` | text | `""` | The callback address registered with the provider; empty: this site's `/api/v1/auth/sso/callback`, from the request (and a reverse proxy's `X-Forwarded-Host` and `X-Forwarded-Proto`) |
| `scopes` | list | `["openid", "profile", "email"]` | Must include `openid` |
| `username_claim` | text | `preferred_username` | Names a new account (the part before any `@`) |
| `name_claim` | text | `name` | The account's display name, kept up to date at each sign-in |
| `email_claim` | text | `email` | Matches existing accounts and directory people |
| `roles_claim` | text | `""` | The claim holding app roles or groups to map (`roles` for Entra ID, `groups` for Okta); empty: no mapping |
| `default_role` | text | `viewer` | The role of someone with no mapped role; `""` refuses them |
| `researchers` | true/false | `true` | An account linked to a person also gets the researcher role |
| `create_accounts` | true/false | `true` | Create an account at first sign-in; `false` admits only accounts an admin added |
| `link_people` | true/false | `true` | Link an account to the directory person with the same email address |
| `trust_email` | true/false | `false` | Use the email claim even when the provider does not mark it verified (Entra ID) |

`[auth.oidc.roles]` maps each value of the `roles_claim` to a role, written as for `tessera users add --role`: `"Tessera.Curator" = "curator"`, `"Lab managers" = "manager=group:okafor-lab"`. Mapped roles are replaced at every sign-in; roles given in the app are kept.

## `[insight]`

Portfolio figures; see [Insight](insight.md).

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `small_number` | number | `5` | Counts above zero and below this are marked as small numbers, and trends are not claimed from them (1 to 100) |

