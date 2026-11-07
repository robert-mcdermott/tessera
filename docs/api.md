# The API

Everything the web app shows comes from a JSON API under `/api/v1`, and the same API is open to scripts and other tools. A running Tessera documents it at `/api/docs`; the OpenAPI document is at `/api/openapi.json`, and a copy for this version is in [openapi.json](openapi.json).

Like the web app, the API listens on this machine only until sign-in arrives.

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
| People | `GET /people`, `GET /people/{id}`, and the person's `/publications`, `/grants`, `/studies`, and `/history`; `POST /people/{id}/publications` adds a paper by PubMed ID |
| Records | `GET /publications`, `/grants`, `/studies`, each with search and facets, and one record by its identifier with its connections and provenance |
| Curation | `GET /assertions/{id}` (a link with its evidence and history), `POST /assertions/{id}/decision`, `POST /assertions/decisions` (one decision for many), `GET /review` (the queue), `POST /review/bulk`, `GET /decisions` (the log) |
| Discovered people | `GET /candidates`, `POST /candidates/decisions` (promote, dismiss, or undo) |

All paths are under `/api/v1`.

## Examples

Find people who work on a topic:

```bash
curl -s "http://127.0.0.1:8000/api/v1/people?q=circadian&fields=id,name,concepts"
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

Decisions made through the API are recorded under the `[server] operator` name, exactly like decisions in the web app.
