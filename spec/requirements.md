# Requirements

The capability catalog. IDs are stable: code, tests, and changelog entries can cite them.

**Priority:** **M**ust (required for the capability to ship), **S**hould (high value, next in line), **C**ould (optional expansion).
**Wave:** the release that delivers it (see [roadmap.md](roadmap.md)). `—` means not yet scheduled.

## Records and provenance (REC)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| REC-01 | M | Stable internal IDs for people and units | Changing a person's name or email never creates a new person | 1 |
| REC-02 | M | External identifier mapping | ORCID, NIH profile ID, email, and local IDs map to one person; conflicting mappings are flagged | 1 |
| REC-03 | M | Provenance on every record and link | Source, retrieval time, method, and status are visible for every assertion | 1 |
| REC-04 | M | Source vs inferred vs human links are distinguishable | The UI and API label each assertion's origin | 1 |
| REC-05 | M | Human decisions survive re-ingestion | Re-running sync never changes a locked assertion's status | 1 |
| REC-06 | M | Replayable ingestion | Raw payloads are kept; `tessera replay` rebuilds the lake without network access | 1 |
| REC-07 | M | Reversible person merges | Merge and unmerge restore all links exactly | 2 |
| REC-08 | M | Dated memberships | Unit and lab memberships carry start and end dates; concurrent memberships are supported | 2 |
| REC-09 | S | Corrections and retractions visible | Retracted papers are marked everywhere and excluded from metrics by default | 1 |
| REC-10 | S | Broader output types | Preprints, datasets, and software outputs are supported alongside articles | — |
| REC-11 | C | Linked-data export | Selected entities export as JSON-LD | — |

## Curation (CUR)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| CUR-01 | M | Explainable suggestions | Each suggestion lists its evidence items in plain language with weights | 1 |
| CUR-02 | M | Accept, reject, defer, undo | Each action records actor, time, previous state, and note | 1 |
| CUR-03 | M | Keyboard-driven review queue | A curator can process a suggestion with one key | 1 |
| CUR-04 | M | Manual additions | A missing paper can be added by PMID and is recorded as a human assertion | 1 |
| CUR-05 | M | Safe auto-accept | Off by default; enabling it requires a threshold whose evaluated precision is shown; reversible in bulk | 2 |
| CUR-06 | M | Scoped curation roles | Curators can be scoped to people or units; researchers can review their own suggestions | 2 |
| CUR-07 | S | Prioritized queue | Conflicts and ambiguous names surface first; strong matches can be cleared in bulk per person | 1 |
| CUR-08 | S | Digest notifications | Optional periodic summaries of pending suggestions (in-app first; email when configured) | 3 |
| CUR-09 | S | Contribution detail | Equal-contribution and explicit role annotations | — |
| CUR-10 | C | Learned weights | Scoring weights refit from local decisions, versioned and evaluated | 2 |

## Discovery (DIS)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| DIS-01 | M | Unified search | One query searches people, publications, grants, and studies, with facets and counts | 1 |
| DIS-02 | M | People directory | Filterable, sortable directory with card and table views and URL state | 1 |
| DIS-03 | M | Connected detail pages | Every entity page links to all its connected entities | 1 |
| DIS-04 | M | Concept profiles | Each person has an explainable concept profile from accepted work | 2 |
| DIS-05 | M | Expert finder | "Who works on X" returns ranked people with matching concepts shown | 2 |
| DIS-06 | M | Similar people with reasons | Each profile lists similar people with shared concepts | 2 |
| DIS-07 | S | Collaboration graph | Person focus, collaboration network, and concept landscape views, each with a list fallback | 2 |
| DIS-08 | S | Missed connections | High-similarity pairs without shared work are listed with explanations | 2 |
| DIS-09 | S | Unit matrix | Unit-by-unit similarity vs collaboration heatmap | 2 |
| DIS-10 | C | Saved searches and alerts | Users save a search and see new matches | 4 |

## Labs and showcases (LAB)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| LAB-01 | M | Labs as first-class entities | A lab has a stable URL, mission, leaders, contact, and owner, separate from any PI profile | 3 |
| LAB-02 | M | Dated roster with alumni | Members have roles and dates; alumni remain visible as alumni | 3 |
| LAB-03 | M | Explicit lab attribution | A PI's authorship alone does not make a paper a lab output; lab attribution is its own assertion | 3 |
| LAB-04 | M | Modular page blocks | Editors arrange approved blocks without code | 3 |
| LAB-05 | M | Editorial workflow | Draft, preview, publish, history, and rollback | 3 |
| LAB-06 | S | Weekly approval inbox | New papers, grants, roster changes, and expiring openings arrive as one weekly review | 3 |
| LAB-07 | S | Research themes and projects | Themes link people, funding, outputs, and studies | 3 |
| LAB-08 | S | Openings with expiry | Job and training openings expire automatically | 3 |
| LAB-09 | C | Embeddable widgets | Lab content embeds in an existing external site | 4 |

## Intelligence and reporting (INT)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| INT-01 | M | Transparent metrics | Every metric shows definition, cohort, period, source, and data timestamp | 4 |
| INT-02 | M | Reproducible funding totals | Fiscal-year records, supplements, and subprojects are never double-counted; the amount type is labeled | 4 |
| INT-03 | M | Reporting snapshots | A report can be regenerated exactly from its snapshot | 4 |
| INT-04 | M | Evidence-carrying exports | CSV, Parquet, and JSON exports include filters, definitions, and provenance | 4 |
| INT-05 | S | Funding portfolio | Sponsor mix, activity codes, timelines, and grants ending soon (labeled as "end dates", not "funding gaps") | 4 |
| INT-06 | S | Topic landscape and trends | Concept trends over time with small-number warnings | 4 |
| INT-07 | S | Study portfolio | Studies by condition, intervention, phase, sponsor class, and registry status | 4 |
| INT-08 | S | Grant-to-impact threads | Grant → publications → studies → clinical citations, with typed, sourced links | 5 |
| INT-09 | S | Opportunity navigator | Public opportunities matched to people and teams; fit and eligibility shown separately | 5 |
| INT-10 | S | Team builder | Assemble people for an opportunity, see covered and missing concepts | 5 |
| INT-11 | C | No league tables | No default ranking of people by any metric (a constraint on every view) | all |
| INT-12 | S | Opportunities an organization tracks | Foundation, internal, and limited-submission opportunities, imported or entered, appear beside public ones with their source | 5 |
| INT-13 | C | Patents in impact threads | Patents NIH reports for a grant extend its thread (a new source: RePORTER's bulk downloads) | — |

## Researcher profiles (PRF)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| PRF-01 | M | Every profile visible inside | Every person's profile is visible to every signed-in account; whether it is public is a separate choice | 6 |
| PRF-02 | M | Sections the researcher keeps | Overview, research interests, positions, education and training, honors and awards, memberships and service, teaching and mentoring, talks, links, and featured publications, kept by the researcher or someone acting for them, with every change recorded | 6 |
| PRF-03 | M | Public by choice | The researcher opts their profile in to or out of the public site and chooses which sections and items it shows; internal views always show everything | 6 |
| PRF-04 | M | CVs and biosketches | CVs and NIH-format biosketches built from the profile, with chosen sections, items, order, personal statement, and contributions to science; several saved versions; printable and downloadable | 6 |
| PRF-05 | S | Someone to act for them | A researcher can name an account that keeps their profile for them | 6 |
| PRF-06 | C | Imports without lock-in | Entries record their source, so ORCID's public record or an HR feed can add to them later without duplicates; no import is required | — |

## Clinical studies (CLN)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| CLN-01 | M | Study entity | NCT ID and organization study IDs coexist on one study | 1 |
| CLN-02 | M | Registry status is labeled as such | No view implies local recruitment from registry status | 1 |
| CLN-03 | M | No patient data | No patient-level data is stored or displayed, ever | all |
| CLN-04 | S | Investigator links with evidence | Person–study links are suggested with evidence and reviewed | 1 |

## Platform (PLT)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| PLT-01 | M | Runs on one machine | Laptop or single VM; one data directory; no external services besides public APIs | 1 |
| PLT-02 | M | Source health and freshness | Every source shows last run, status, counts, and staleness | 1 |
| PLT-03 | M | Idempotent, resumable ingestion | Re-running a sync never duplicates data; interrupted runs resume | 1 |
| PLT-04 | M | Configurable organization profile | Org names, aliases, patterns, and domains are configuration, not code | 1 |
| PLT-05 | M | Re-brandable | Product name, organization name, logo, default palette, and accent color are configuration | 1 |
| PLT-06 | M | Local accounts and roles | Admin, curator, researcher, and viewer roles enforced in API, search, and exports | 2 |
| PLT-07 | M | Public / internal separation | Public views use an allowlisted projection, never raw records | 3 |
| PLT-08 | M | Backup, restore, and exit export | Documented, tested backup and restore; a full export of the ledger and lake | 4 |
| PLT-09 | M | PostgreSQL ledger option | Full test suite passes against PostgreSQL | 7 |
| PLT-10 | S | Standards-based single sign-on | Optional OpenID Connect sign-in with any standards-based provider (such as Microsoft Entra ID) beside local accounts, which remain the default; the provider's groups or app roles optionally map to Tessera roles and group scopes at each sign-in | 7 |
| PLT-11 | S | Configurable vocabulary | Unit kinds, role names, and labels are configurable | 3 |
| PLT-12 | S | Operational visibility | Job metrics, run history, and failure alerts in the admin area | 4 |
| PLT-13 | S | Brand kit | An organization's own palette (core colors by day and night), footer links, favicon, and social preview image are configuration; `tessera doctor` fails any configured color pair below WCAG AA | 3 |

## User experience (UX)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| UX-01 | M | Light, dark, and system themes, with selectable palettes | Every palette passes AA contrast in both modes; choices persist per viewer; the organization sets the default | 1 |
| UX-02 | M | URL-addressable state | Search, filters, tabs, and sort survive reload and Back | 1 |
| UX-03 | M | Keyboard accessible | All features usable by keyboard; visible focus | 1 |
| UX-04 | M | Responsive | Usable from 360 px to wide desktop | 1 |
| UX-05 | S | Command palette | ⌘K / Ctrl-K finds entities, pages, and actions | 1 |
| UX-06 | S | WCAG 2.2 AA audit | An audit is passed before v1.0 | 7 |

## AI (optional, off by default) (AI)

| ID | Pri | Requirement | Acceptance | Wave |
|---|---|---|---|---|
| AI-01 | M | Off by default; core never depends on it | All features work with AI disabled | 5 |
| AI-02 | M | Pluggable provider | Any OpenAI-compatible or Anthropic-compatible endpoint (official, self-hosted, or local) and Amazon Bedrock, configured by the adopter; credentials come from the environment | 5 |
| AI-03 | M | Grounded answers with citations | Answers cite Tessera records; unsupported questions are declined | 5 |
| AI-04 | M | AI outputs are suggestions | Summaries and classifications are assertions with method and model recorded, reviewable like any other | 5 |
| AI-05 | M | Data minimization | No contact details or unreviewed suggestions are sent to a model | 5 |
| AI-06 | S | Plain-language summaries | Optional lay summaries for profiles and lab pages, with approval before display | 5 |
| AI-07 | M | Invisible when off | With AI disabled, the app, the API document, and the public site show no menu item, setting, route, or mention of it | 5 |
| AI-08 | M | Same access as the person asking | The assistant reads only what the signed-in person may read, through the same access policy | 5 |
