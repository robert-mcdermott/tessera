# Changelog

All notable changes to Tessera are recorded here. Versions follow [semantic versioning](https://semver.org/); each wave of work ships as a minor release (wave 1 → 0.1.0, wave 2 → 0.2.0).

## Unreleased

### Added

- Groups: divisions, departments, programs, centers, shared resources, labs, and any other kind you configure (`[vocabulary]` names the kinds, the membership roles, and what the app calls a group). A group can sit under several others, has an address that survives renames, and can be archived or merged into another. Memberships carry a role as well as a title and dates; ending one keeps the person as an alumnus, and removing one is for mistakes. **Admin → Groups** curates all of it, with each group's history. `tessera groups import` (and `POST /api/v1/groups/import`) reads groups and memberships from CSV or JSON with the source's own identifiers, so importing the same source again updates instead of duplicating; `--complete` archives what the source no longer lists and `--dry-run` changes nothing. The roster import goes through the same path and can give a group's kind and a person's role. A curator limited to a group reaches the groups beneath it through any parent. Ledger migration 0007.
- Group pages: **Groups** lists every division, program, lab, and shared resource, and each has a page with what it does, its leaders and contact, its people and alumni, the publications attributed to it, its members' grants and studies, its topics and broader areas, and the groups it writes most with. Profiles list a person's groups with their roles and link to them.
- Lab attribution: whether a paper is a group's own is a link of its own (`attributed_to`), suggested at each sync for labs, programs, centers, shared resources, and teams (`[vocabulary] attributable_kinds`) with its evidence (member authors, a leader as last author, an affiliation naming the group, a member's grant, the group's topics), and always decided by a person on the group's page. A leader's authorship alone is weak evidence. `tessera groups attribute` refreshes the suggestions. Groups gain a contact address and a website (migration 0008).
- Managers: a role limited to one group (`manager=group:<address>`, or `+below` for the groups beneath it too) for the person who keeps the group's page and decides which papers are its own. Admin → Accounts offers it with a group picker.
- Each group's page is made of blocks from an approved set (about, people, publications with featured papers, grants, studies, topics, writes most with, themes and projects, openings, contact, news), arranged in **Edit page** without code, saved as a draft, previewed, and published as numbered versions that can be viewed and restored. Migration 0009.
- The demo organization has labs, programs that span divisions, and a shared resource, imported from its registry, with alumni and decided attributions.
- MeSH, the National Library of Medicine's vocabulary, as a source: `tessera sync` reads the annual descriptor file once a year (`[sources.mesh]`). Expert search understands synonyms ("cancer" is Neoplasms, "AML" is acute myeloid leukemia), shows which synonym a concept came from, and lets a broad heading include the narrower ones beneath it; concept search finds headings by synonym; profiles show research areas rolled up the MeSH hierarchy; and the concept landscape is colored by MeSH category. NLM is credited, with the MeSH year, wherever these appear. New lake table `mesh_descriptor`; `derived/concept_vocabulary.parquet` and `derived/concept_area.parquet`.

- `tessera demo [PATH]` builds a complete installation for an invented organization, the Corvane Institute for Biomedical Research: about 100 people in 22 labs, 700 publications, grants, clinical studies, ORCID records, and a curator's decisions, so every screen has something to show. Identifiers are chosen so they can never be mistaken for real ones. It is also what the documentation's screenshots are taken from.
- Screenshots for the documentation are taken from the real app, serving the demo organization, in light and dark (`node docs/capture/capture.mjs`), and the diagrams are drawn as SVG (`node docs/capture/diagrams.mjs`).
- A product home page for the documentation site, figures in the guides, a social preview card, and a screenshot at the top of the README.
- A documentation site, built from the guides with `node docs/site/build.mjs` (Node.js 22, no packages): a menu in reading order, search, light and dark themes from Tessera's own design tokens, an API reference generated from the OpenAPI document, and a check that fails the build on any broken link or anchor.

### Changed

- The collaboration graph's communities are now called clusters, so they are not confused with the organization's groups.
- Units are now groups. The API's person `units` entries add the membership's role, the group's kind and address; `GET /people?unit=` and the unit matrix list only active groups.
- In the person focus view, the person in focus stays at the centre, and topics are drawn larger than the people around them, so people gather around the topics they share.

### Fixed

- With reduced motion turned on, the graphs drew every node and label in black: a tiny transition on every element made the colors read back wrong. Reduced motion now turns transitions off.
- The collaboration graph's legend lists former members only when the graph shows one.

## 0.2.0 (2026-11-08)

Connections. Tessera now helps people find who works on what and who should meet, with the reasons on screen, and lets several people curate with their own accounts and roles. ORCID strengthens the evidence, duplicate records merge reversibly, and the evidence model can learn from local decisions.

**Highlights:** ORCID records and iDs as evidence; possible duplicates and reversible merges; dated unit memberships; local accounts with admin, curator (for everyone, a unit, or a person), researcher, and viewer roles; learned weights evaluated on held-out people, and auto-accept only where the measured precision is at least 99%; the expert finder; concept profiles that show what drives them; similar people; collaboration, person focus, and concept landscape graphs with list views; missed connections; groups and the people who bridge them; and the unit matrix.

### Evaluation

- **Pilot check against people's own ORCID works lists:** Tessera reads an ORCID record only once the iD is confirmed, and none was, so the records behind the 49 suggested iDs are independent of every score. Of 193 papers that 39 people list themselves (within the pilot's two-year window), 95.3% were found as candidates (target 95%); of those, 97.3% are in the strong band and all are at or above the review threshold. Works lists measure recall, not precision: people list only some of their papers. Details are in [spec/evidence-engine.md](spec/evidence-engine.md#pilot-check-against-orcid-works-lists-v020).
- **Learned weights and auto-accept** need curator decisions, which the pilot does not have yet. On generated decisions, fitting recovers the weights that produced them, and a learned model is offered only when it predicts better on people left out of the fit. On the synthetic organization with simulated decisions, auto-accept at 0.99 accepted 477 suggestions, all correct, and undoing the batch restored every one.
- **Discovery on the synthetic organization:** every person's top three concepts come from their own field, searching a field's heading returns that field's people first, at least 90% of similar pairs and of collaborating pairs share a field, and collaboration counts match an independent recount exactly.
- **Speed on the pilot:** the expert finder answers in 31 ms at the 95th percentile for a query in words (13 ms for a chosen concept); similar people, a profile's concepts, and every graph view in under 10 ms. Graphs of about 150 nodes, and a test graph of 300 nodes and 600 links, lay out with no main-thread task over 50 ms.

### Dependencies and licenses

No dependency was added or changed: the runtime dependencies and their licenses are as in 0.1.0 (MIT, BSD, Apache-2.0, and PSF, with certifi under MPL-2.0 used unmodified, and the optional psycopg under LGPL-3.0, not bundled). The web app still ships no third-party code, fonts, or images. The force layout, community detection, and betweenness are original implementations of published methods.

### Upgrading

- Run `uv run tessera db upgrade` to apply ledger migrations 0003 to 0006, then `uv run tessera sync` (or `tessera build search` and `tessera build discovery`) to build the new discovery data.
- **Breaking:** listening on any address other than this machine now requires `server.accounts = true`. See [Accounts and sign-in](docs/operations.md#accounts-and-sign-in).

### Added

- Missed connections (DIS-08): pairs of current members whose interests are alike but who have never published, held a grant, or run a study together, with the topics they share and anyone who has worked with both. Filters for unit, pairs across units, and how alike. Profiles' **Similar people** now say whether each pair has worked together. `GET /api/v1/graph/missed`.
- Groups and bridges: the collaboration network is divided into groups by modularity, each named by its members' most distinctive topics and given its own colour; people who connect groups are ringed and listed by name with the groups they work with. `derived/person_group.parquet`.
- The unit matrix (DIS-09): every pair of units compared by interest (shade) and collaboration (inner square), each per possible pair of members; each cell opens the people behind it, the matrix works from the keyboard, and a sortable list has the same information. `GET /api/v1/graph/units`, `/graph/units/pairs`.
- Categorical colours (`--series-0..5`) in every palette, tested to stay distinct from one another and visible against cards in light and dark.
- Connections (DIS-07): three graph views under Explore, beside **Missed connections** and **Units**. **Collaboration** shows people linked by shared papers, grants, and studies (weighted so that one large consortium paper does not outweigh a long partnership) and, dashed, by similar interests where they have no shared work; **Person focus** shows someone's top topics and the people who share them, with a trail to step back; **Concept landscape** shows research areas as topics linked by the people who hold them together. Hover to see someone's connections, select for details, double-click to focus, drag to pin, search to fly to a name; every view has a sortable **List** view with the same information, and its state is in the URL. The layout runs in a Web Worker and the views load on first use. `GET /api/v1/graph/network`, `/graph/focus/{id}`, `/graph/landscape`; `derived/collaboration_edge.parquet` and `derived/concept_edge.parquet`.
- Find experts (DIS-05): describe a topic in your own words and Tessera ranks people by the topics of their papers, with the concepts that match on every result. Words are read through the organization's papers that they find, so "cancer" reaches the people whose papers are indexed under "Neoplasms"; the concepts a search became are shown as **Understood as**, where you can drop one or add a heading by name. `GET /api/v1/experts`, `GET /api/v1/concepts`.
- Concept profiles (DIS-04): every directory member's topics as weighted MeSH headings from their papers, counting major topics, first and last authorship, and recent work more, and headings many people share less. Strong suggestions count half until confirmed (`[discovery] likely_weight`); author keywords stand in, marked, for papers not yet indexed; studies add their conditions and interventions. **How this is built** on a profile's Topics card shows each concept's weight, papers, and confirmed papers. Built by `tessera sync` or `tessera build discovery` (which also builds the graphs) into `derived/concept_profile.parquet`; `GET /api/v1/people/{id}/concepts`.
- Similar people (DIS-06): each profile lists the people whose topics overlap most, with the shared concepts, precomputed into `derived/similarity_pair.parquet`. `GET /api/v1/people/{id}/similar`.
- A `[discovery]` section for the recency half-life, the weight of unconfirmed suggestions, and the similarity floor; a [Finding experts](docs/discovery.md) guide.
- Learned weights (`authorship/v2`, CUR-10): Tessera fits one multiplier per kind of evidence, and an offset, to curators' accept and reject decisions, pulled toward the hand-set weights so a few decisions barely move it. It is evaluated on people left out of the fit and offered only if it predicts better there (at least 200 decisions, 20 rejections, 5 people). Admins release or revert it under **Admin → Evidence** or with `tessera evidence`; explanations keep both weights.
- Safe auto-accept (CUR-05): off by default, and only at a threshold where the latest evaluation measured at least 99% precision over 50 or more decisions. Each sync accepts qualifying suggestions without conflicting evidence as one batch, marked **Auto-confirmed**, never used for learning, and undone in one step. A new model, or undoing a batch, switches it off. Ledger migration 0006.
- Local accounts and roles (PLT-06, CUR-06): set `[server] accounts = true` to require sign-in. Roles add up: viewers read, researchers also review the suggestions about their own profile (**My suggestions**), curators decide for everyone or only for one unit or person, and admins also manage accounts (**Admin → Accounts**, or `tessera users add | list | disable | enable | reset-password | roles`). Decisions record the signed-in person's name. Passwords are hashed with the standard library's scrypt; sessions are server-side, in `HttpOnly`, `SameSite=Strict` cookies, with a CSRF token on every change and throttled sign-in. Every API route has an access policy, and a test fails for any route without one. Ledger migration 0005; ADR-0009.
- Possible duplicates: two records of one person (typically a roster row and a grant record with no shared identifier) are proposed as a pair, scored by `same_as/v1` on shared identifiers, a replaced ORCID iD, author positions both claim, shared grants, and names; never on a name alone. A new **Possible duplicates** page under Curate shows both records side by side with the evidence. `GET /api/v1/duplicates`.
- Reversible merges: merging moves a record's links, names, and identifiers to the record kept and journals every change, so **Undo merge** restores both exactly. Where both records decided the same link differently, the more recent decision holds and the merge says so. Merge from the duplicates page or with **Merge…** on a profile; merges appear in the decision log. `POST /api/v1/merges`, `POST /api/v1/merges/{id}/undo`; ledger migration 0004 (a `merged` status, `person.merged_into`, and `person_merge`).
- Dated unit memberships: roster `start` and `end` columns (a year, month, or day), several units at once, titles and dates on profiles, and only current units in the directory and its filters. People whose memberships have all ended become former members; importing an updated roster updates dates.
- ORCID iDs suggested from people's own papers: an iD printed at a person's author position on at least two of their likely papers is suggested on their profile, with how many papers carry it and what the public ORCID record says. Curators confirm it, mark it as not theirs, or add identifiers by hand; every decision is logged. An iD recorded for two people is flagged on both profiles. `POST /api/v1/people/{id}/identifiers`; ledger migration 0003.
- ORCID evidence in authorship scoring (`authorship/v1.1`): papers listed on a person's public ORCID record (+6.0), iDs a curator rejected (−8.0, like a conflict), and the same iD on the person's other papers (+2.0 or +3.0) or a clearly different one (−2.0, weak, since some people have two iDs). Deprecated iDs count as the iD that replaced them; an iD suggested for the person is never a conflict. On the pilot, the strong band grew from 1,652 to 1,666 suggestions, and the near misses on a short, common name left it.
- Papers listed on members' ORCID records are fetched from PubMed when the lake lacks them.
- ORCID connector: the public records of directory members who have an ORCID iD (works with their PubMed IDs and DOIs, and employment), read without credentials, refreshed every 30 days, and part of `tessera sync`. Deprecated, locked, and missing records are kept as data. New lake tables `orcid_record`, `orcid_work`, and `orcid_employment`; a `[sources.orcid]` setting.

### Changed

- `authorship/v1.2`: a full-name match on a first name of at most two letters, which many people share, counts for less (the near misses of the v0.1.0 pilot check).
- **Breaking:** listening on any address other than this machine now requires `server.accounts = true` (it used to be a warning). Without accounts, Tessera also answers only to requests addressed to `localhost` or `127.0.0.1`, and in both modes refuses changes that a browser sends from another site.
- Cassette's stripe is now a thin sunset gradient in both modes, and the hero's diagonal bands are faint pinstripes in the same sunset colors.
- Record-type colors no longer resemble status colors: grants are gold, studies raspberry, and Cassette's publications teal, each kept well apart from the green, amber, and red of accepted, suggested, and rejected (a test checks every palette and mode). Warnings and "suggested" are a warmer amber.
- Charts of grant amounts use a brighter gold fill.

### Fixed

- On SQLite, a migration that failed part way could leave some of its schema changes behind, because Python's driver did not put them in the transaction. Schema changes are now transactional and roll back whole (a test checks it).
- A person listed on several roster rows without any identifier became several people; within one file, such rows with the same full name are now one person.
- Keyboard hints on buttons were faded below WCAG AA contrast; they are now full strength.
- Resolving again rewrote hundreds of unchanged suggestions because tied topics were listed in a random order in their explanations; ties are now broken by name, and a second resolve changes nothing.

## 0.1.0 (2026-11-07)

The first release. Tessera builds an organization's research graph from public data, with the evidence for every link, and gives curators fast tools to confirm it. It runs on a laptop or a single server.

**Highlights:** connectors for PubMed, iCite, NIH RePORTER, and ClinicalTrials.gov; a people directory from grant investigators, an optional roster, and discovered authors; explainable authorship suggestions; links between papers, grants, and studies; a fast, accessible web app in three palettes with light and dark versions; a keyboard-driven review queue; a JSON API; and user guides.

### Evaluation

- **Pilot check against ORCID iDs** at a mid-sized research institute (70 directory members, about 2,400 publications over five years; 588 matched papers labeled by the ORCID iD printed on them): the strong band was 98.3% precise at 93.7% recall (target 99%), and suggestions above the review threshold were 97.9% precise at 95.8% recall (target 90%). Seven of the nine strong-band disagreements appear to be people with a second ORCID iD rather than wrong matches. Details are in [spec/evidence-engine.md](spec/evidence-engine.md#pilot-check-against-orcid-ids-v010).
- **Synthetic organization with known answers:** the strong band is 100% precise with at least 99.8% recall, and candidate retrieval recall is 100%.
- **Speed on the pilot:** profile pages in 6 ms and search in 20 ms at the 95th percentile; in the review queue, the next suggestion's evidence appears in 22 ms and a decision is saved in 56 ms.

### Dependencies and licenses

Runtime dependencies: Alembic, DuckDB, FastAPI (with Starlette and Pydantic), HTTPX, PyArrow, SQLAlchemy, and Uvicorn, plus psycopg for the optional `postgres` extra. All are permissively licensed (MIT, BSD, Apache-2.0, PSF), except certifi (MPL-2.0, used unmodified) and psycopg (LGPL-3.0, optional and not bundled), both compatible with distributing Tessera under Apache-2.0. The web app ships no third-party code, fonts, or images.

### Added

- Product specification in `spec/`: vision, requirements, architecture, data model, data sources, evidence engine, discovery, UX, roadmap, process, glossary, and ADRs 0001–0008.
- Project skeleton: `uv` package, Apache 2.0 license, contributor guide.
- Configuration from `tessera.toml` with `TESSERA_*` environment overrides and validation that reports every problem at once.
- Data home layout (`raw/`, `lake/`, `ledger.db`, `derived/`, `logs/`).
- Ledger schema for people, names, identifiers, units, assertions, curation events, ingest runs, checkpoints, and settings, with an Alembic baseline migration tested on SQLite and PostgreSQL.
- Parquet lake with crash-safe writes, per-table manifests, current-version views in DuckDB, unchanged-record skipping, compaction, and garbage collection. DuckDB never downloads extensions.
- API skeleton: `/api/v1/health`, `/api/v1/meta`, `/api/openapi.json`, RFC 9457 problem responses, strict security headers, and confined static file serving with client-side route fallback.
- CLI: `tessera init`, `doctor` (with `--online`), `db upgrade|current`, `lake status|compact|gc`, `serve`.
- Development tools: private-terms check with optional git hooks, and a disposable PostgreSQL container script.
- Connectors for PubMed, iCite, NIH RePORTER, and ClinicalTrials.gov: a shared HTTP client with per-source rate limits, retries with backoff and `Retry-After`, and contact-identifying `User-Agent`; fetch and parse kept separate so raw payloads can be replayed offline.
- PubMed: organization affiliation sweep, query, ID, and "linked" modes; automatic date-window splitting past the 10,000-record limit; parsing of authors (with ORCID iDs and affiliations), MeSH headings, grants, retractions, and book chapters.
- NIH RePORTER: organization, investigator, ID, and validated-criteria modes; splitting past the 15,000-record limit; the grant-to-publication link table with batch halving and failure narrowing.
- ClinicalTrials.gov: organization search across sponsors, collaborators, sites, and official affiliations; incremental updates by last-update date; contact details removed before storage.
- iCite: citation metrics for publications that lack them or whose metrics are over 30 days old.
- NIH grant-number normalization to core project numbers (and institute + serial when the activity code is missing).
- `tessera ingest` (one source or `all`), `tessera replay`, and `tessera runs`; run records and incremental checkpoints in the ledger; checkpoints advance only after complete runs; one data job at a time per data home.
- `/api/v1/meta` reports each source's last successful fetch and whether it is stale.
- Opt-in live contract tests for every source (`pytest -m network`).
- Evidence engine: name normalization and compatibility (initials, particles, hyphens, accents); organization matching with wildcard patterns, email domains, and exclusions for look-alike names.
- People: bootstrap from NIH grant investigators (contact PIs active; co-investigators held as candidates until affiliated papers confirm them), roster CSV import with unit memberships, and discovery of affiliated authors for review.
- Authorship suggestions from the explainable `authorship/v1` model: ORCID, email, affiliation, grant links, name agreement, name ambiguity, coauthors, topics, journals, and career window, with leave-one-out seeding and one person per author position.
- Source-reported links (grant-publication, study-publication, study-grant, person-grant) and suggested study officials, all with evidence; human decisions are never overridden and links no longer reported are marked, not deleted.
- Decisions with undo, recorded as curation events.
- PubMed person search (`ingest pubmed --mode people`) with batch splitting and skipping of names too common to search.
- `tessera resolve`, `tessera sync`, `tessera people import|list`, and `tessera eval authorship` (precision, recall, calibration, and slices on local decisions).
- Configuration: `organization.exclusions` and an `[evidence]` section for thresholds.
- Search and serving index (`derived/search.db`, SQLite FTS5): organization-relevant people, publications, grants, and studies with summaries, facets, and highlights; rebuilt by `tessera build search` and at the end of `sync`. User input is sanitized so it can never act as query syntax.
- API `/api/v1`: people (directory, profile, publications, grants, studies, history, manual addition), publications, grants, and studies (lists with facets; details with connected records, metrics with definitions, counting rules, and provenance), assertions and decisions, review queue with bulk decisions, unified search, stats, sources, and runs. Envelopes, cursor paging, sparse field sets, and problem responses throughout.
- Self-hosted API reference at `/api/docs`.
- Person topics ranked by distinctiveness (TF-IDF); common model-organism and method headings no longer count as topics.
- Web app (no build step, no frameworks): design tokens in `oklch` with light, dark, and system themes; brand accent from configuration via `/theme.css`; original mark, favicon, and icons.
- App shell with sidebar, top bar, URL-driven router, skip link, focus management, and a mobile menu; command palette (⌘K / Ctrl-K or `/`) searching everything.
- Pages: Home (stats, recent publications, publications by year, largest active grants, source freshness), Search, People directory (cards or table), person profile (publications by status, grants, studies, decision history, topics, activity), publication, grant, and study details with connections and provenance, and Admin → Sources.
- Status marks on every suggested or confirmed link, opening an evidence drawer that explains the score feature by feature and records accept, reject, defer, and undo decisions with an undo toast.
- Accessibility: keyboard operation throughout, focus trapping in dialogs, live-region announcements, data tables behind every chart, reduced-motion support, and AA contrast in both themes.
- Web tests: no inline code, no resources from other origins, no HTML parsing of data, every module import resolves, an app-shell size budget, and JavaScript unit tests (run when Node.js is installed).
- Review queue page: suggestions in priority order beside an evidence panel that highlights the matched author and their affiliation on the paper; keyboard flow (`j`/`k` to move, `a` accept, `r` reject, `d` defer, `u` undo, `o` open, `p` one person, `?` keys); the next items prefetched; accept all strong matches for a person, undone together in one step.
- Discovered people: people found in the data who are not in the directory, with the evidence for each (affiliated papers and years, recent titles, NIH grants, affiliation), search and filters, and promote or dismiss one at a time or in bulk, all reversible. Dismissed people are not proposed again.
- Decision log page and `GET /api/v1/decisions`: every decision about links and people, newest first.
- API: `GET /api/v1/candidates`, `POST /api/v1/candidates/decisions`, and `POST /api/v1/assertions/decisions` (one decision for many assertions, used for bulk undo).
- "Add a publication" on profiles (by PubMed ID), "Review suggestions" for one person, and promote or dismiss on a discovered person's profile.
- Sidebar counts of suggestions and discovered people waiting, kept current as decisions are made.
- Ledger migration 0002: the `dismissed` person status and the `person_event` log.
- Your organization's logo in the sidebar (`branding.logo_light` and `logo_dark`).
- User guides in `docs/`: getting started, describing your organization, curating, configuration, running Tessera, the API, and a command reference generated from the commands. Tests keep the command reference and the OpenAPI document current and check every link.

### Changed

- Appearance has two choices under a new Appearance panel (and the command palette): light, dark, or system, and a color palette. Three palettes ship, each with day and night versions: **Tessera** (the default; its dark mode is now a neutral graphite instead of blue-navy), **Harbor** (deep navy, teal, bright blue, purple, and gold), and **Cassette** (label paper and rainbow stripes by day, black labels and a sunset at night). The organization sets the default with `branding.theme`; each viewer can choose another. Every palette passes WCAG AA in both modes.
- `branding.accent_color` is now optional and, when set, replaces the palette's accent (lightened after dark, with text on it chosen for contrast).
- Decisions update the search index in place (member lists, person facets and counts, directory membership), so lists and search reflect them on the next request without a rebuild.
- `branding.accent_color` must be a CSS color (hex, keyword, or a color function); anything else is a configuration error.
- Ledger migrations on SQLite run with foreign keys off and verify every reference before committing, so a migration that rebuilds a table keeps the rows that point at it.
