# Data sources

Tessera uses **free, public data sources only**. No source needs a paid subscription or a contract. Some accept an optional free API key that raises rate limits.

| Source | Provides | Wave | Key needed |
|---|---|---|---|
| **PubMed** (NCBI E-utilities) | Publications, authors, affiliations, ORCID iDs, MeSH terms, grant acknowledgments | 1 | Optional (raises the rate limit) |
| **iCite** (NIH) | Citation counts, Relative Citation Ratio, clinical-citation indicators | 1 | No |
| **NIH RePORTER** (API v2) | NIH-funded projects, investigators with stable profile IDs, organizations, amounts, dates, and the project-to-publication link table | 1 | No |
| **ClinicalTrials.gov** (API v2) | Study registry records: status, phase, conditions, interventions, sponsors, officials, sites, references | 1 | No |
| **ORCID** (Public API v3.0) | Researcher-confirmed identifiers, employment, and works lists | 2 | No: the public API answers without credentials (checked 2026-11-07) |
| **Grants.gov** (Search API) | Open federal funding opportunities | 5 | No (to be confirmed when the connector is built) |
| **MeSH** (NLM descriptor data) | Hierarchy for rolling concepts up into broader topics | 2 | No. *Candidate: needs approval as an additional source* |

## Rules for every connector

1. **Be a good citizen.** Respect each source's published rate limit with a shared, thread-safe limiter. Back off on 429 and 5xx responses and honor `Retry-After`. Run large backfills off-peak where the source asks for that.
2. **Identify ourselves.** Send a `User-Agent` naming Tessera, its version, and the adopter's configured contact address. For NCBI, also send the `tool` and `email` parameters.
3. **Validate before calling.** Several APIs silently ignore unknown parameters and return everything. Connectors validate queries against known criteria before any request.
4. **Capture raw, then normalize.** Each response body goes to the raw zone before parsing, so a parser fix can be replayed without re-fetching. Parsing is a pure function of the stored payload, so replay and ingest cannot disagree.
5. **Never advance a checkpoint on a partial run.** If any batch failed or a result was truncated, the watermark stays put and the run is recorded as partial.
6. **Fail softly.** One source failing never blocks the others. The last good data stays in service, and the UI shows the staleness.
7. **Store no contact details** (emails, phone numbers) from registry records.
8. **Test offline.** Unit tests use recorded or synthetic fixtures, never the network. Live contract tests are opt-in.

## PubMed (NCBI E-utilities)

- **Endpoints:** `esearch.fcgi` (with the history server) and `efetch.fcgi` returning XML, both POSTed.
- **Rate limit:** about 3 requests/second without an API key, 10 with one. The key comes from the `NCBI_API_KEY` environment variable, never from a committed file.
- **Quirk: the result cap.** A single search can page through at most 10,000 records. The connector splits the date window recursively until each part fits. A single day still over the cap is truncated with a warning and the run is marked partial.
- **Quirk: transient history-server errors.** efetch occasionally returns a spurious 400 for a valid history key. Retry once after a pause.
- **Retrieval modes in Tessera:**
  - *Affiliation sweep:* all articles whose author affiliations match the organization's configured aliases, by date window. Finds new people and most institutional papers.
  - *Person search:* candidate articles for a known person by name variants within their active years. Finds papers where the affiliation is missing or from a previous employer.
  - *By ID:* specific PMIDs, for example those linked from grants or studies.
- **Incremental field:** modification date (`mdat`), not entry date. Entry date misses articles whose MeSH indexing arrives later.
- **Grant normalization:** grant acknowledgments come in many shapes (`R01 CA123456`, `R01CA123456-05`, `CA123456`). Tessera extracts a core project number where the pattern is recognizable and keeps the raw string either way.
- **Terms:** abstracts may be under publisher copyright. Internal views may show them; the public projection (wave 3) shows citations and links rather than full abstracts unless rights are cleared.

## iCite

- **Endpoint:** `GET /api/pubs?pmids=…` in batches of up to 200 PMIDs, JSON.
- **Use:** citation metrics for publications already in the lake. Refreshed on a slower cadence than metadata (monthly), since metrics move slowly.
- **Responsible use:** the Relative Citation Ratio is a field-normalized citation measure, not a quality score. The translation indicator estimates the likelihood of later clinical citation, not patient benefit. The UI says this next to the numbers.

## NIH RePORTER

- **Endpoints:** `POST /v2/projects/search` and `POST /v2/publications/search`, JSON.
- **Rate limit:** no more than 1 request/second. Large backfills run off-peak.
- **Quirk: the offset cap.** A search can page through at most about 15,000 results. The connector splits by fiscal year, then by funding institute, and reports any gap it cannot reach.
- **Quirk: the link cap.** The publication-link search is capped too. Batches of project numbers are halved until each fits, and a failing batch is narrowed to the one bad project.
- **Use in Tessera:**
  - *Organization sweep:* projects at the organization's configured names, which seeds investigators (with stable NIH profile IDs) and their grants.
  - *Investigator follow:* all projects for known profile IDs, including those at previous institutions, which helps authorship matching.
  - *Publication links:* the project-to-PMID table, a strong, source-reported grant ↔ publication link.
- **Incremental runs:** `date_added` only catches new records, while amounts and dates on recent awards keep changing. So every incremental run re-reads the previous, current, and next fiscal years in full (cheap: a few thousand records at most for a large organization). Older years are re-read with `--full`.
- **Contracts:** NIH contract numbers (`75N9…`, `HHSN…`, `N01-…`) are not grants and are not normalized as core project numbers.
- **Counting:** each record is one fiscal year of one project. See the counting rules in [data-model.md](data-model.md).

## ClinicalTrials.gov

- **Endpoints:** `GET /api/v2/studies` with `pageToken` paging, `GET /api/v2/studies/{nct}`, and `GET /api/v2/version` for the data timestamp.
- **Rate limit:** about 50 requests/minute.
- **Use in Tessera:** studies whose sponsors, collaborators, or sites match the organization's configured names; study officials (name, affiliation, role) for person matching; references to PMIDs; NIH grant numbers in secondary IDs.
- **Incremental field:** last update posted date.
- **Safety rule:** a study's *overall* registry status is never presented as the organization's local recruitment status. Views say "Registry status" and link to the official record.
- **Excluded:** results sections and documents are not requested. Central and site contacts (names, emails, phone numbers) are removed from each response *before* it is stored anywhere, including the raw zone; study officials keep only name, affiliation, and role.

## ORCID (wave 2)

- **Use:** when a person's ORCID iD is known, from PubMed author records or entered by the person, read their public works and employment as strong authorship evidence.
- **Principle:** ORCID records are the researcher's own assertions, so they carry high weight but still show as evidence, not silent truth.
- **Scope:** only the accepted ORCID iDs of people in the directory (active or former), one request each for the public record (`/v3.0/{iD}/record`), never the iDs of everyone who appears in PubMed. Records are read again after 30 days.
- **Access:** the public API answers without credentials. Tessera sends at most four requests a second with its contact address in the `User-Agent`, retries politely, and needs no key. If heavier use ever needs one, it will be read from the environment like `NCBI_API_KEY`.
- **Records that cannot be read** are kept as data rather than failures: an iD that does not exist, a deprecated iD (with the iD that replaced it, which is evidence that two iDs belong to one person), and locked or deactivated records.
- **Works** are kept once per ORCID work group, using the researcher's preferred version, with its own DOI, PubMed ID, and PMC ID. Employment keeps the organization with its ROR, Ringgold, or GRID identifier, department, role, and years.

## Grants.gov (wave 5)

- **Use:** open funding opportunities (title, agency, dates, eligibility, description) for opportunity-to-team matching.
- **Principle:** similarity is not eligibility. Matches show topical fit and eligibility as separate signals.

## Organization profile: how Tessera knows "who we are"

Without HR data, Tessera recognizes the organization through a configured **organization profile** in `tessera.toml`:

```toml
[organization]
name = "Example University"
short_name = "EXU"
aliases = ["Example University", "Example Univ", "Example University School of Medicine"]
affiliation_patterns = ["example univ*", "exu medical center"]   # matched against PubMed affiliations
reporter_org_names = ["EXAMPLE UNIVERSITY"]                       # as they appear in grant records
ctgov_org_names = ["Example University", "Example University Medical Center"]
email_domains = ["example.edu"]
exclusions = ["Example University of Technology"]                 # look-alikes that contain an alias
```

An optional **roster file** (CSV: name, ORCID, email, unit, title) can be imported from the admin screen. It is a file an administrator chooses to provide, not a live integration, and Tessera works without it.
