# Data model

Tessera's model has two halves:

- **Content** describes *what things are*: a publication's title and MeSH terms, a grant's amounts and dates, a study's phase and status. Content comes from public sources, lives in the **lake**, and is replaceable.
- **Connections** describe *how things relate*: who wrote what, which grant funded which paper, who belongs to which unit. Connections are **assertions** in the **ledger**, each with evidence and a review status, and human decisions about them are permanent.

## Entities

| Entity | Internal ID | External identifiers | Content lives in | Notes |
|---|---|---|---|---|
| **Person** | `per_<uuid7>` | ORCID, NIH profile ID, email, roster local ID | Ledger | Never keyed by name or email; those can change |
| **Unit** | `unt_<uuid7>` | Roster code | Ledger | Department, division, center, or program. Hierarchical |
| **Publication** | `pub:<pmid>` | PMID, DOI, PMCID | Lake | Wave 1 publications come from PubMed, so PMID is the key. A cross-source output registry arrives with non-PubMed outputs |
| **Grant** | `grt:<core project number>` | Core project number, application IDs | Lake | One grant spans many fiscal-year records. Totals must not double-count years, supplements, or subprojects |
| **Study** | `stu:<nct id>` | NCT ID, aliases, org study ID | Lake | Registry metadata only. Never patient data |
| **Concept** | `msh:<descriptor UI>` | MeSH descriptor UI | Lake | Controlled vocabulary for topics (wave 2) |
| **Lab** *(wave 3)* | `lab_<uuid7>` | — | Ledger | First-class group, distinct from a PI and from a department |
| **Opportunity** *(wave 5)* | `opp:<source id>` | Grants.gov opportunity ID | Lake | Public funding opportunity |

## Assertions

An **assertion** is a typed, evidenced, reviewable link between two entities. Every relationship in Tessera, except a few structural ones, is an assertion.

| Field | Meaning |
|---|---|
| `id` | `asr_<uuid7>` |
| `subject_type`, `subject_id` | e.g. `person`, `per_…` |
| `predicate` | Relationship type (table below) |
| `object_type`, `object_id` | e.g. `publication`, `pub:12345678` |
| `qualifiers` | Predicate-specific detail: author position, role, date range |
| `origin` | `source` (stated by a public record), `inferred` (computed by Tessera), or `human` (entered by a person) |
| `method` | Rule or model that produced it, with a version, e.g. `authorship/v1.1` |
| `score` | 0–1 confidence for inferred assertions; null otherwise |
| `evidence` | List of evidence items (below) |
| `status` | `suggested`, `accepted`, `rejected`, or `deferred` |
| `decided_by`, `decided_at`, `decision_note` | Who settled it, when, and why |
| `locked` | True once a human decides. Automation may add evidence to a locked assertion but never changes its status |
| `first_seen_at`, `last_evaluated_at` | Lifecycle timestamps |

Each **evidence item** is a small record: `{feature, value, weight, explanation, source_ref}`. For example: `{feature: "affiliation_match", value: "Example University, Dept. of Medicine", weight: +2.5, explanation: "Affiliation on this paper matches Example University", source_ref: "pubmed:12345678#author3"}`. The UI shows these directly, so explanations are written for humans.

### Predicates

| Predicate | Subject → Object | Origin in wave 1 | Default status |
|---|---|---|---|
| `authored` | person → publication | inferred (evidence engine) | `suggested`. Auto-accept, gated by evaluation, is planned for wave 2 (CUR-05). One per person and paper; the matched author position, author count, and name as printed are qualifiers |
| `investigator_on` | person → grant | source (grant record, keyed by NIH profile ID) | `accepted` |
| `official_of` | person → study | inferred (name and affiliation match) | `suggested` |
| `acknowledges` | publication → grant | source (grant link table, publication grant list) | `accepted` |
| `references` | study → publication | source (registry reference list) | `accepted` |
| `funded_by` | study → grant | source (registry secondary IDs) | `accepted` |
| `member_of` | person → unit | source (roster file) or human | `accepted`. Qualifiers: `title`, and `start` and `end` as precise as the roster gives them (`2015`, `2015-07`, or `2015-07-01`). Current until its end has passed; one membership per person and unit, and a person may belong to several units at once (REC-08) |
| `has_identifier` | person → identifier | source or human | depends on the source |
| `same_as` | person → person | inferred (`same_as/v1`) or human | `suggested`, from the record to fold in to the record to keep. Accepted only by merging; rejected means "different people" and is kept |

The rules every assertion must follow:

1. **Assertions are never deleted by automation.** If a source stops reporting a link, the assertion is marked as no longer reported and the UI shows that. It is not removed.
2. **Human decisions win.** Re-ingesting, re-scoring, or changing a source never overturns `accepted` or `rejected` on a locked assertion.
3. **Every status change is a curation event.** Events are append-only and record actor, time, old and new status, and note. Undo is a new event, not an erased one.
4. **Merges are reversible.** Merging two people accepts their `same_as` assertion (or records one), moves the merged record's links, names, and identifiers to the record kept, and marks the merged record `merged`, pointing at the survivor. Every change goes into the merge's journal with the values it replaced, so unmerging replays the journal backwards and restores the ledger exactly. Where both records hold the same link, a human decision beats a suggestion and, between two decisions, the more recent wins; the other copy stays with the merged record, out of sight.

## Ledger schema (wave 1)

The ledger is relational and portable across SQLite and PostgreSQL. JSON fields are stored as text in SQLite and `jsonb` in PostgreSQL. All timestamps are UTC.

| Table | Purpose | Key columns |
|---|---|---|
| `person` | One row per real person | `id`, `display_name`, `family_name`, `given_name`, `status` (`active`, `former`, `candidate` for people discovered in the data, `dismissed` for candidates a curator judged not to be members, `merged` for a duplicate folded into another record), `merged_into`, `created_at`, `updated_at` |
| `person_name` | Name variants used for matching | `person_id`, `family`, `given`, `initials`, `kind` (`primary`, `variant`, `former`), `source` |
| `person_identifier` | External IDs | `person_id`, `scheme` (`orcid`, `nih_profile`, `email`, `local`), `value`, `status` (`accepted`; `suggested`, when the evidence engine proposes an ORCID iD found on the person's papers; `rejected`, kept so it is never proposed again and counts as evidence against), `source` (`roster`, `reporter`, `pubmed`, `papers`, `curator`) |
| `unit` | Organizational units | `id`, `name`, `kind`, `parent_id`, `code` |
| `assertion` | All typed links (see above) | as above; unique on (`subject_id`, `predicate`, `object_id`, qualifier key) |
| `curation_event` | Append-only decision log | `id`, `assertion_id`, `actor`, `action`, `from_status`, `to_status`, `note`, `at` |
| `person_event` | Append-only log of decisions about people | `id`, `person_id`, `actor`, `action` (`promote`, `dismiss`, `undo`, `identifier`, `merge`, `unmerge`), `from_status`, `to_status`, `note`, `at`, `detail` (for identifier decisions: scheme, value, and the action: add, accept, reject, or remove, with the identifier's statuses; for merges: the merge and the record kept). Migration 0003 added `identifier` and `detail`; 0004 added `merge` and `unmerge`. Automation only ever promotes candidates, and discovery treats dismissed people as known, so a curator's decision is never reversed by a sync |
| `person_merge` | One row per merge | `id`, `merged_id`, `survivor_id`, `actor`, `note`, `at`, `journal` (every change, in order, with the values it replaced), `summary` (links, names, and identifiers moved; conflicting decisions), `undone_at`, `undone_by`. Added by migration 0004; a ledger with merges in effect cannot be downgraded below it |
| `ingest_run` | One row per connector or job run | `id`, `source`, `kind`, `started_at`, `finished_at`, `status`, `params`, `counts`, `error` |
| `source_checkpoint` | Incremental watermarks | `source`, `scope`, `watermark`, `updated_at` |
| `setting` | Runtime settings editable in the app | `key`, `value`, `updated_by`, `updated_at`. Keys: `authorship_model` (the released learned model: multipliers, offset, method name), `authorship_evaluation` (the latest evaluation and the candidate model), `auto_accept` (on or off, threshold, measured precision and support, the model it was measured for) |
| `auto_accept_batch` | Links one automatic run accepted | `id`, `created_at`, `threshold`, `precision`, `method`, `assertion_ids`, `undone_at`, `undone_by`. Migration 0006. Links accepted automatically have `decided_by` `Auto-accept` |

Wave 2 added `account` (username, display name, scrypt password hash, status, linked person), `role_grant` (role, optionally scoped to a unit or a person), and `auth_session` (only a hash of the session token, the CSRF token, and expiry), in migration 0005. Later waves add: `lab`, `lab_membership`, `page`, `page_revision` (wave 3); `snapshot`, `metric_definition` (wave 4); `opportunity_match`, `team` (wave 5).

## Lake tables (wave 1)

Every lake table has these bookkeeping columns: `_run_id`, `_retrieved_at`, `_content_hash`, and `_source_version` where the source provides one. The **current** view of each table keeps the latest row per key, using `_retrieved_at`. Unchanged records (same `_content_hash`) are not rewritten.

| Table | Key | Main columns |
|---|---|---|
| `pubmed_article` | `pmid` | `record_type` (journal article or book), `doi`, `pmcid`, `title`, `abstract`, `journal`, `journal_abbrev`, `issn`, `pub_date`, `pub_year`, `date_precision`, `publication_types[]`, `languages[]`, `keywords[]`, `is_retracted`, `comments_corrections[]` |
| `pubmed_author` | (`pmid`, `position`) | `family`, `given`, `initials`, `collective_name`, `orcid`, `affiliations[]`, `is_first`, `is_last`, `author_count` |
| `pubmed_mesh` | (`pmid`, `descriptor_ui`) | `descriptor`, `major_topic`, `qualifiers[]` |
| `pubmed_grant` | (`pmid`, `seq`) | `grant_id_raw`, `core_project_num` (e.g. `R01CA123456`, when recognizable), `ic_serial` (e.g. `CA123456`, also when the activity code is missing), `agency`, `country` |
| `icite_metrics` | `pmid` | `citation_count`, `relative_citation_ratio`, `nih_percentile`, `field_citation_rate`, `expected_citations_per_year`, `citations_per_year`, `apt`, `is_clinical`, `is_research_article` |
| `reporter_project` | `appl_id` | `project_num`, `core_project_num`, `subproject_id`, `fiscal_year`, `title`, `abstract`, `phr`, `activity_code`, `agency_ic_admin`, `award_amount`, `direct_cost`, `indirect_cost`, `project_start`, `project_end`, `budget_start`, `budget_end`, `is_active`, `org_name`, `org_city`, `org_state`, `org_country`, `org_uei`, `terms[]`, `spending_categories[]` |
| `reporter_investigator` | (`appl_id`, `profile_id`) | `full_name`, `family`, `given`, `middle`, `is_contact_pi`, `title` |
| `reporter_publication_link` | (`pmid`, `core_project_num`) | `appl_id` |
| `ctgov_study` | `nct_id` | `aliases[]`, `org_study_id`, `brief_title`, `official_title`, `acronym`, `overall_status`, `last_known_status`, `why_stopped`, `study_type`, `phases[]`, `conditions[]`, `condition_mesh[]`, `intervention_mesh[]`, `keywords[]`, `lead_sponsor`, `lead_sponsor_class`, `collaborators[]`, `start_date`, `primary_completion_date`, `completion_date` (each with actual/estimated type), `enrollment`, `has_results`, `last_update_posted`, `brief_summary` |
| `ctgov_official` | (`nct_id`, `seq`) | `name`, `affiliation`, `role` |
| `ctgov_site` | (`nct_id`, `seq`) | `facility`, `status`, `city`, `state`, `country`, `latitude`, `longitude` |
| `ctgov_reference` | (`nct_id`, `pmid`) | `type` (`result`, `background`, `derived`), `citation` |
| `ctgov_secondary_id` | (`nct_id`, `seq`) | `id`, `type`, `core_project_num` and `ic_serial` (normalized when NIH) |
| `orcid_record` | `orcid` | `status` (`public`, or why there is none: `not_found`, `deprecated`, `locked`, `deactivated`), `primary_orcid` (the iD that replaced a deprecated one), `given_names`, `family_name`, `credit_name`, `other_names[]`, `last_modified` |
| `orcid_work` | (`orcid`, `put_code`) | `title`, `type`, `pub_year`, `journal`, `doi` (lowercase, no resolver prefix), `pmid`, `pmcid`, `source_name` (who added it), `versions` |
| `orcid_employment` | (`orcid`, `put_code`) | `organization`, `city`, `region`, `country`, `org_id_source` and `org_id` (ROR, Ringgold, GRID), `department`, `role`, `start_year`, `end_year` |

Contact details from registry records (emails, phone numbers) are **not** stored.

### Raw zone layout

`raw/<source>/<run_id>/` holds one gzip file per record-bearing response (`00001-efetch.xml.gz`, `00002-projects.json.gz`, …) and `index.jsonl`, one line per payload with its kind, retrieval time, request summary, and the source's data version. `tessera replay <source> <run>` reads this folder and runs the same parser as ingestion.

### Lake write protocol

1. A run writes Parquet files to `lake/<table>/_staging/<run_id>/`.
2. Each file is fully written and fsynced, then renamed into `lake/<table>/data/`.
3. The table's `_manifest.json` (the list of live files) is rewritten to a temp file and atomically replaced.
4. Readers only open files listed in the manifest. A crash mid-run leaves the previous manifest, and so the previous data, intact.
5. `tessera lake compact` rewrites a table into a small number of key-sorted files and swaps the manifest. Superseded files are deleted after a retention period.

## Derived artifacts (wave 1)

| Artifact | Built from | Purpose |
|---|---|---|
| `derived/search.db` | Lake content + ledger people and links | The **search and serving index**. `doc` holds one row per organization-relevant person, publication, grant, and study with a JSON display summary; `doc_fts` is an FTS5 index (porter + unicode61, diacritics removed) over title, people, terms, and body; `facet` holds (doc, name, value) rows for filters and counts. Publications are included only if a member authored them (accepted, or suggested at or above the review threshold), an author lists the organization, or a grant or study cites them; papers that name searches fetched for other people stay out. Person topics are ranked by TF-IDF across the directory. `candidate` documents describe discovered people (affiliated papers, years, recent titles, grants, affiliations) for the curation queue; they never appear in the directory or in "everything" search. Decisions rewrite only the documents they touch (member lists, person facets, counts, directory membership) in place, using the same rules as a full build, so lists are current on the next request; topics refresh at the next build |

Later: `concept_profile`, `similarity_pair`, `coauthor_edge` (wave 2); `metric_*` tables and frozen `snapshot/<id>/` folders (wave 4).

## Counting rules (to be enforced from the first chart)

- **Publications** are counted once per PMID, whatever the number of matched authors. Multi-person totals use distinct PMIDs.
- **Grants** are counted per core project number. Money is summed per fiscal year **record**, with subprojects excluded from parent totals unless stated. Every funding figure states which amount it is: total cost, direct, or indirect.
- **Proposals are never awards.** Tessera's wave 1 funding data is awards only, because public data contains no applications. That means success rates cannot be computed, and the UI says so.
- **Study status** shown is the registry's *overall* status. It is never presented as local recruitment status.
- Every metric records its cohort, period, and data timestamp.
