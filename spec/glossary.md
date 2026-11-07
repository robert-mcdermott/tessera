# Glossary

| Term | Meaning |
|---|---|
| **Assertion** | A typed, evidenced, reviewable link between two entities, such as "person X authored publication Y". The unit of trust in Tessera |
| **Band** | A score range for suggestions: *strong*, *possible*, or *unlikely* |
| **Candidate (person)** | A person discovered from public data, not yet promoted into the directory |
| **Concept** | A research topic, represented by a MeSH descriptor |
| **Concept profile** | A person's weighted set of concepts, built from their accepted work |
| **Core project number** | The stable identifier for an NIH grant across years, such as `R01CA123456` (activity code, institute code, serial number) |
| **Curation event** | An append-only record of a decision on an assertion: who, when, from what status to what status, and why |
| **Data home** | The single directory holding an installation's raw zone, lake, ledger, derived artifacts, and logs |
| **Derived artifact** | Data computed from the lake and ledger that can always be rebuilt: the search index, concept profiles, similarity pairs, and metrics |
| **Evidence item** | One reason behind an assertion: a feature, its value, its weight, a plain-language explanation, and the source it came from |
| **Lake** | The tier of normalized source records in Parquet files, queried with DuckDB |
| **Ledger** | The relational database (SQLite or PostgreSQL) holding people, assertions, decisions, and settings. The only tier that cannot be rebuilt |
| **Locked** | An assertion a human has decided. Automation can add evidence to it but never change its status |
| **Missed connection** | Two people with strongly overlapping concepts and no shared papers, grants, or studies |
| **Organization profile** | Configuration that tells Tessera how the organization appears in public data: names, aliases, affiliation patterns, and email domains |
| **Origin** | Where an assertion came from: `source` (a public record states it), `inferred` (Tessera computed it), or `human` (a person entered it) |
| **Raw zone** | Verbatim API payloads, kept so the lake can be rebuilt without re-fetching |
| **Registry status** | The overall status of a study in the public registry. It is not the organization's local recruitment status |
| **Roster file** | An optional CSV of people that an administrator imports. A file, not a live integration |
| **Single-operator mode** | Wave 1 operating mode with no sign-in. The server binds to localhost and curation is attributed to a configured operator name |
| **Slice** | A coherent chunk of work within a wave |
| **Tessera** | A single tile in a mosaic, and this product |
| **Wave** | A release cycle. Wave *n* ships as version `0.n.0` |
