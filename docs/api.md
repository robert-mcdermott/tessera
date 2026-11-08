# The API

Everything the web app shows comes from a JSON API under `/api/v1`, and the same API is open to scripts and other tools. A running Tessera documents it at `/api/docs`; the OpenAPI document is at `/api/openapi.json`, and a copy for this version is in [openapi.json](openapi.json).

Without accounts (single-operator mode), the API answers only on this machine and needs no sign-in. With accounts on, a script signs in like the web app:

```bash
curl -s -c jar -H "Content-Type: application/json" -X POST http://127.0.0.1:8000/api/v1/session \
  -d '{"username": "ada", "password": "…"}'           # returns data.csrf_token
curl -s -b jar "http://127.0.0.1:8000/api/v1/people?q=circadian"
curl -s -b jar -H "X-CSRF-Token: <csrf_token>" -H "Content-Type: application/json" \
  -X POST "http://127.0.0.1:8000/api/v1/assertions/asr_…/decision" -d '{"action": "accept"}'
```

Every change needs the session's CSRF token in `X-CSRF-Token`, and each route needs a role: reading needs any account, deciding needs a curator for that person (or the person themselves, as a researcher), and managing accounts needs an admin. A missing session is a `401`, a missing role or scope a `403`.

## Conventions

- **Envelopes.** A successful response is `{"data": …, "meta": {…}}`. Lists put paging details and facet counts in `meta`.
- **Paging.** Lists take `limit` (up to 200) and either `offset` or the opaque `cursor` from the previous page's `meta.next_cursor`, which is `null` on the last page.
- **Sparse fields.** `fields=id,name` returns only those keys of each list item.
- **Errors** are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details (`application/problem+json`) with a `title`, a `status`, and a plain-language `detail`.
- **Search highlights** in `highlights` mark matches with the private-use characters U+E000 and U+E001, never HTML. Escape the text, then replace those characters with your own markup.
- **Identifiers.** People have IDs such as `per_01…`. Records use their public identifiers: a PubMed ID, a grant's core project number (for example `R01CA123456`), or an NCT number.

## What is available

| Area | Endpoints |
|---|---|
| System | `GET /health`, `GET /meta` |
| Search and overview | `GET /search` (everything, or one kind with facets), `GET /stats`, `GET /sources`, `GET /runs` |
| People | `GET /people`, `GET /people/{id}`, and the person's `/publications`, `/grants`, `/studies`, and `/history`; `POST /people/{id}/publications` adds a paper by PubMed ID; `POST /people/{id}/identifiers` adds, confirms, rejects, or removes an identifier |
| Records | `GET /publications`, `/grants`, `/studies`, each with search and facets, and one record by its identifier with its connections and provenance |
| Opportunities | `GET /opportunities` (search and facets: `status`, `source`, `kind`, `closing`, `agency`, `activity`, `category`; sorted by closing date, soonest first), `GET /opportunities/{id}` (Grants.gov's numeric ID, or `opl_…` for the organization's own); curators for everyone and admins: `POST /opportunities` (add one the organization tracks), `POST /opportunities/{id}` (change it, or restore it with `archived: false`), `DELETE /opportunities/{id}` (archive it), `POST /opportunities/import` (`rows`, or `csv` text with a header; `source`, `complete`, `dry_run`). The list also filters by `eligible` (meets, check, does_not_meet), `fits` (a person's ID, or `me`), and `group`; `GET /opportunities/{id}/fit` (who fits and why, with `concept` and `drop` to adjust the reading, and the eligibility checks), `POST /opportunities/{id}/interest` (`watching`, `dismissed`, or null, for yourself), `GET /me/opportunities` (those that fit your work, and those you watch), `GET /people/{id}/opportunities`, `GET /groups/{group}/opportunities` |
| Teams | `GET /teams` (yours, those you are on, and those shared with you), `POST /teams` (`name`, `purpose`, `opportunity`, `concepts`; from an opportunity, its topics come with it), `GET /teams/{id}` (members, coverage, missing topics with suggestions, pairings, groups), `POST /teams/{id}` (change `name`, `purpose`, `notes`, `concepts`; the owner: `shared_with`, person IDs), `DELETE /teams/{id}`, `POST /teams/{id}/members` (`person_id`, `role`, `note`), `DELETE /teams/{id}/members/{person_id}`, `GET /teams/{id}/brief` (`format`: json, markdown, or csv) |
| Impact | `GET /grants/{core}/thread` (what followed a grant: the papers acknowledging it, the registered studies that reference them or name the grant, and the clinical articles citing the papers, each with how it is linked), `GET /publications/{pmid}/thread` (the grants a paper acknowledges, and the studies and clinical articles after it), `GET /people/{id}/impact` (what followed a person's grants and their confirmed papers) |
| Curation | `GET /assertions/{id}` (a link with its evidence and history), `POST /assertions/{id}/decision`, `POST /assertions/decisions` (one decision for many), `GET /review` (the queue), `POST /review/bulk`, `GET /decisions` (the log) |
| Discovered people | `GET /candidates`, `POST /candidates/decisions` (promote, dismiss, or undo) |
| Evidence model | `GET /evidence` (active model, latest evaluation, auto-accept, batches); admins: `POST /evidence/evaluate`, `/evidence/release`, `/evidence/revert`, `/evidence/auto-accept`, `/evidence/auto-accept/batches/{id}/undo` |
| Sessions and accounts | `GET /session` (who is signed in), `POST /session` (sign in), `DELETE /session` (sign out), `POST /session/password`; admins: `GET /accounts`, `POST /accounts`, `POST /accounts/{id}` |
| Groups | `GET /groups` (kinds, parents, and member counts; `q`, `kind`, `status`, `parent`), `GET /groups/{group}` (by ID, address, or code, with sub-groups), `GET /groups/{group}/members` (`include`: current, alumni, all, or removed), `GET /groups/{group}/history`, `GET /groups/{group}/overview` (the group's page: leaders, people, alumni, topics, areas, grants, studies, publications by year, and the groups it writes with), `GET /groups/{group}/publications` (`status`: accepted, suggested, or rejected, with evidence), `GET /groups/{group}/page` (its blocks: `version` published, draft, or a number) and `/page/revisions`; and, for the group's managers and curators, `POST /groups/{group}/publications/decisions`, `POST /groups/{group}/page/draft`, `DELETE /groups/{group}/page/draft`, `POST /groups/{group}/page/publish`, `POST /groups/{group}/page/rollback`, `POST /groups/{group}/openings` and `/openings/{id}`, `POST /groups/{group}/themes` and `/themes/{id}`, `GET /groups/{group}/inbox`, and `POST /groups/{group}/inbox/reviewed`; `GET /groups/{group}/openings` and `GET /groups/{group}/themes` for everyone; `GET /inbox` (your digest of pending work); `POST /groups/{group}/public` (the group's editors) and `POST /people/{id}/public` (the person, or a curator) opt in to the public site; admins: `POST /groups`, `POST /groups/{group}` (change, nest, archive, restore), `POST /groups/{group}/merge`, `POST /groups/{group}/members`, `POST` and `DELETE /groups/{group}/members/{membership}`, `POST /groups/import?source=` (rows from another system; `complete`, `dry_run`) |
| Insight | `GET /insight/figures/{metric}` (a metric over a cohort and a period: `cohort`, `start`, `end`, the metric's options, and `records=true` for the records counted; the response carries the definition, cohort, period, sources with their data timestamps, notes, and small numbers), `GET /insight/metrics` (every definition and version), `GET /insight/cohort?cohort=` (who a cohort is), `GET /insight/years` (the years with data and the default period), `GET /insight/cohorts` (saved cohorts), `GET /insight/figures/{metric}/export?format=` (CSV or Parquet as a zip, or JSON, with records, definitions, filters, and sources), `GET /insight/reports` and `/insight/reports/{id}`, `GET /insight/snapshots`, `/insight/snapshots/{id}` (the manifest and frozen figures; `records=true`), `/insight/snapshots/{id}/verify`, and `/insight/snapshots/{id}/export?format=` (all figures, or `figure=n`); managers, reviewers, and admins: `POST /insight/cohorts`, `POST` and `DELETE /insight/cohorts/{id}`, `POST /insight/reports`, `POST` and `DELETE /insight/reports/{id}`, `POST /insight/reports/{id}/snapshots` |
| Operations | Admins: `GET /operations` (every source and job over 30 days with failures, durations, and the last error; recent runs; sizes; the last backup; and what needs attention) |
| Saved searches | Each person's own: `GET /saved-searches` (with how many matches are new), `POST /saved-searches` (`name`, `kind`, `q`, `filters`), `GET /saved-searches/{id}` (with the new matches), `POST /saved-searches/{id}` (rename), `POST /saved-searches/{id}/seen`, `DELETE /saved-searches/{id}` |
| Duplicates and merges | `GET /duplicates` (possible duplicate pairs with evidence), `POST /merges` (merge two records), `POST /merges/{id}/undo` |
| Graph views | `GET /graph/network` (people linked by collaboration and, where they have none, by similarity; `unit`, `max_people`, `min_similarity`, `similar`), `GET /graph/focus/{id}` (a person, their top concepts, and who shares them, with their collaborators), `GET /graph/landscape` (concepts linked by the people who hold them together). Each returns `{"nodes": [...], "edges": [...]}`; the network's `meta` also names the collaboration groups and the people who bridge them |
| Missed connections and units | `GET /graph/missed` (alike, current, and never worked together; `min_similarity`, `unit`, `across`, paging), `GET /graph/units` (unit-by-unit interest and collaboration densities), `GET /graph/units/pairs?a=&b=` (the pairs behind one cell) |
| Discovery | `GET /experts` (people ranked by matching concepts, from words `q` or descriptors `concept`, with the concepts the query became in `meta.concepts`), `GET /concepts?q=` (descriptors by name), `GET /people/{id}/similar` (similar people with shared concepts, and whether they have worked together), `GET /people/{id}/concepts` (what drives a profile, with the person's research areas in `meta.areas`) |

All paths are under `/api/v1`.

## Examples

Find people who work on a topic, with the concepts that match:

```bash
curl -s "http://127.0.0.1:8000/api/v1/experts?q=circadian+rhythms&limit=10"
```

Or start from a MeSH descriptor (here, Neoplasms) and find who shares it:

```bash
curl -s "http://127.0.0.1:8000/api/v1/experts?concept=D009369"
```

The organization's active grants, largest first:

```bash
curl -s "http://127.0.0.1:8000/api/v1/grants?status=active&sort=amount&limit=10"
```

Why Tessera links a person to a paper, and how to accept it:

```bash
curl -s "http://127.0.0.1:8000/api/v1/assertions/asr_…"
curl -s -X POST "http://127.0.0.1:8000/api/v1/assertions/asr_…/decision" \
  -H "Content-Type: application/json" -d '{"action": "accept", "note": "Checked the author list"}'
```

Decisions made through the API are recorded under the signed-in person's name (or, without accounts, the `[server] operator` name), exactly like decisions in the web app.
