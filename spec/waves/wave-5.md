# Wave 5: Impact (v0.5.0)

**Goal:** connect discovery to outcomes and opportunities. Follow a grant forward to the papers, studies, and clinical citations that came after it; see which open funding opportunities fit the organization's people and groups, and whether the organization can apply; assemble a team for one; and, where an organization chooses to turn it on, ask questions answered from Tessera's own records.

**Branch:** `wave-5` · **Target version:** `0.5.0` · **Status:** ◐ slices 1 to 5 done

## Exit criteria

The wave is done when, on a fresh checkout with the demo organization and the pilot:

1. A grant's **impact thread** shows the papers that acknowledge it, the studies that reference those papers or are funded by the grant, and the clinical articles that cite the papers (practice guidelines and trials marked), with every link typed, sourced, and dated, and the whole labeled as association, never causation (INT-08). The same thread is reachable from a paper, a person, and a group, and **Insight → Impact** counts it for any cohort.
2. Open and forecast federal opportunities from Grants.gov, and opportunities an organization tracks itself, are listed with **topical fit** and **eligibility** as separate signals, each with its reasons. A person sees the opportunities that fit their work; a group's inbox and digest list new ones that fit it (INT-09, INT-12).
3. A **team** can be assembled for an opportunity, showing which needed concepts its members cover and which are missing, with people who cover the gaps and why, and exported as a brief (INT-10). No view ranks people by a metric (INT-11).
4. With `[ai]` absent or disabled, nothing in the app, the API document, or the public site mentions AI (AI-01, AI-07). Enabled, the assistant works with any OpenAI-compatible or Anthropic-compatible endpoint (official, self-hosted, or local) and with Amazon Bedrock, with credentials from the environment (AI-02). Answers cite Tessera records that the person asking may read and decline what the records do not support (AI-03, AI-08). Nothing sent to a model includes contact details or unreviewed suggestions (AI-05), and every exchange is logged with its model, prompt version, inputs, and output.
5. Plain-language summaries of profiles and group pages are drafted on request, then edited and approved by a person before anyone sees them, recorded with their model and prompt version (AI-04, AI-06).
6. The test suite passes offline; lint and the private-terms check are clean; the documentation site is refreshed.

Requirements: INT-08 to INT-12, AI-01 to AI-08.

## Decisions from the maintainer

- **Model for development** (2026-11-08): build and test the assistant against a model served by Ollama, starting with `gemma4:31b-cloud` through Ollama's OpenAI-compatible endpoint, and say so if it is not capable enough, so that a larger model can be tried. The feature itself must work with any OpenAI-compatible or Anthropic-compatible endpoint (official, local, or remote) and with Amazon Bedrock. The model only ever sees the invented demo organization during development and for screenshots.
- **Roadmap** (2026-11-08): researcher profiles become wave 6 (Profiles, v0.6.0): positions, education, honors, service, and other activities kept by the researcher, every profile visible inside, public by the researcher's choice, and CVs and biosketches, kept simple and with no external integration required. Adoption moves to wave 7. Wave 5's summaries and opportunities for a person will appear on those profiles.
- **Defaults taken while the maintainer was asked** (2026-11-08; either can change): patents stay out of wave 5 and are noted for a later wave (INT-13), because NIH reports them only in RePORTER's bulk downloads, a new source; and when an organization turns AI off, approved summaries are kept but no longer shown, so nothing about AI remains visible, and they return if it is turned back on.

## Principles for this wave

- **Association, not causation.** A thread shows what followed a grant, by typed links with their sources. It never claims a grant caused a citation, and says so where it is shown.
- **Fit is not eligibility.** Topical fit and eligibility are computed separately, shown separately, and never combined into one number. Eligibility that depends on the person (career stage, citizenship) or on text only a person can read is "check the notice", never a guess.
- **Confirmed work only, by default.** Threads, fit, coverage, and everything a model sees use confirmed links and accepted assertions; likely links are a click away where the app shows them and never reach a model.
- **No league tables.** Suggestions of people are ordered by how well they answer a question (a topic, a gap in a team), with reasons, and never show a metric of a person.
- **Nothing in the core depends on AI.** Threads, opportunities, and teams use no language model. The assistant uses them as read-only tools.
- **Invisible when off; the person's own access when on.** AI code paths are dormant and unadvertised until the configuration enables them; when enabled, the assistant can read only what the person asking can read, through the same access policy as every route.
- **Machines suggest, people decide.** A model's summary is a draft until a person approves it, and the record keeps who approved what and which model and prompt produced it.

---

## Slice 1: Grant-to-impact threads ☑

**Goal:** follow funding forward, with every step typed and sourced (INT-08).

- [x] iCite's clinical citations (`cited_by_clin`, the clinical articles that cite a paper) kept in a new lake table, filled as each paper's metrics are refreshed (monthly), or at once with `tessera ingest icite --full`
- [x] The citing clinical articles' own records from PubMed (title, journal, year, publication types, with practice guidelines, clinical trials, and meta-analyses marked) in a separate dataset, never among the organization's candidate papers, so authorship matching never sees them
- [x] Threads (`tessera.impact`): forward from a grant (acknowledging papers → studies that reference them or are funded by the grant → clinical articles citing the papers), and around a paper, a study, a person's grants, or a group's; every edge typed (`acknowledges`, `references`, `funded_by`, cited by a clinical article) with its source and status; years on every node, so the time from award to clinical citation shows
- [x] The thread view on grant pages, and from papers, people, and groups: columns from the grant to clinical citations, with counts, expandable lists, and the paths that reach a practice guideline picked out
- [x] **Insight → Impact**: `impact.clinical_citations/v1` (a cohort's papers cited by clinical articles, by year, with guidelines apart) and `impact.studies/v1` (its papers referenced by registered studies), with iCite's caution that a clinical citation is not patient benefit
- [x] The demo gains clinical citations and practice guidelines, with invented identifiers that cannot be mistaken for real ones

**Acceptance (met):** on the demo, the immunotherapy program project (P01) reaches a practice guideline through two papers; a paper cited by three clinical articles shows them with their years; a rejected acknowledgment never appears in a thread; a citing article never becomes an authorship candidate; a grant's thread answers in 300 ms or less on the pilot's data.

## Slice 2: Funding opportunities ☑

**Goal:** the opportunities themselves, from Grants.gov and from the organization (INT-09, INT-12).

- [x] A Grants.gov connector over its public search and opportunity APIs (no key needed; checked 2026-11-08): forecast and posted opportunities, details fetched for new and changed ones, closed and archived ones kept with their status, raw responses captured like every source; `[sources.grants_gov]` can limit agencies and funding categories (all by default)
- [x] An opportunity in the lake: number, title, agency, status, posting, closing, and archive dates, award floor and ceiling, expected awards and total funding, cost sharing, eligible applicant types, instruments, categories, assistance listings, the synopsis as text, eligibility notes, and its link; for NIH notices, the activity code and clinical-trial requirement read from the title, and the NIH Guide link
- [x] Opportunities an organization tracks itself (foundation calls, internal pilot funds, limited submissions): `tessera opportunities import` from CSV or JSON, kept in the ledger with their source and editable in **Admin → Opportunities** (ledger migration 0014)
- [x] Opportunities in search, the command palette, and saved searches (new matches)
- [x] Source health and Operations show the new source; `data-sources.md` records its terms, pace, and quirks

**Acceptance (met):** a sync against recorded Grants.gov responses stores forecast and posted opportunities and, on a second run, fetches only those that changed; an NIH notice yields its activity code and clinical-trial requirement; an imported foundation opportunity appears beside federal ones, labeled with its source; the first live sync's duration is recorded here.

## Slice 3: The opportunity navigator ☑

**Goal:** which opportunities fit whom, and whether the organization can apply, kept apart (INT-09).

- [x] Fit: an opportunity's title and synopsis read as concepts the way an expert search reads a question (headings, synonyms, and the organization's papers), with the concepts it was read as shown and adjustable; the people and groups whose confirmed work matches, with the matching concepts and papers as reasons, in bands (strong, some) rather than scores; computed after each sync
- [x] Eligibility, separately: the organization's applicant types (`[organization] applicant_types`, in Grants.gov's codes, checked by `tessera doctor`) against the opportunity's, foreign components, cost sharing, the clinical-trial requirement, the mechanism, and the deadline; each "meets", "does not meet", or "check the notice", with the text it came from; career and training awards say that eligibility depends on the person
- [x] **Opportunities** in the menu: open and forecast opportunities with filters (agency, closing within, mechanism, eligibility, fits a group, fits me), fit and eligibility in separate columns; an opportunity's page with its details, the eligibility checklist, who fits and why, and **Start a team**
- [x] For people and groups: opportunities that fit on the home page and the profile; a group's inbox and weekly digest list new opportunities that fit it; anyone can watch or dismiss an opportunity for themselves (migration 0015)
- [x] `GET /api/v1/opportunities`, an opportunity, its fit, and a person's or group's opportunities

**Acceptance (met):** on the demo, a cell-therapy opportunity lists the immunotherapy program and its people with the concepts that match; an opportunity open only to small businesses shows "does not meet" for applicant type while its fit is still shown; dismissing an opportunity hides it for that person only; a lab's inbox lists a new fitting opportunity once.

## Slice 4: Team builder ☑

**Goal:** assemble people for an opportunity and see what is covered and what is missing (INT-10).

- [x] A team: name, purpose, an opportunity or a set of concepts it needs, members with roles (lead, co-investigator, collaborator, core or resource), and notes; owned by its creator, shared with named people, visible to admins (ledger migration 0016)
- [x] Coverage: the needed concepts against each member's confirmed work; covered and missing concepts; for each gap, people who cover it, with reasons; who on the team has worked together before and which pairings are new; the groups and divisions the team spans
- [x] The team brief: a printable page and Markdown, with JSON and CSV, giving the purpose, the opportunity, each member's role with their relevant confirmed papers, grants, and studies, and the coverage, carrying its sources and data dates as exports do
- [x] Suggestions are ordered by the gap they fill, with their reasons, and never show a metric of a person

**Acceptance (met):** building a team for the demo's cell-therapy opportunity shows two missing concepts and suggests people who cover them; adding one updates the coverage; the brief lists only confirmed papers; a viewer the team is not shared with cannot open it.

## Slice 5: The AI layer: providers, configuration, invisibility, and the log ☑

**Goal:** an optional, provider-neutral foundation that leaves no trace when off (AI-01, AI-02, AI-05, AI-07).

- [x] `[ai]`, off unless enabled: `provider` is `openai` (any endpoint that speaks the Chat Completions API: the official service, gateways, Ollama, vLLM, and the like), `anthropic` (any endpoint that speaks the Messages API: the official service, gateways, Ollama), or `bedrock` (Amazon Bedrock's Converse API); `base_url`, `model`, `api_key_env` (the name of the environment variable that holds the key), `region`, timeouts, output limits, extra headers, a daily limit per person, the roles that may use it, and how long the log is kept. A key written in the configuration file is refused
- [x] Clients on the HTTP library Tessera already uses, with no SDKs: tool calls and streaming for all three providers, Bedrock's event stream decoded with the standard library; Bedrock authenticated with an Amazon Bedrock API key or with access keys from the environment, signed with Signature Version 4 from the standard library and checked against AWS's published test vectors; one shape for messages, tool calls, usage, and errors across providers
- [x] Invisible when off: routes, API document entries, menu items, settings, and front-end modules exist only when enabled; the app loads optional modules that the server names, so the app's own code never names them; `tessera ai` is hidden from the command help when off. Tests check that, with AI off, the API document, the app shell, every script served, and the public site never mention it
- [x] Data minimization: one module builds everything a model sees from the same read projections the API serves, without email addresses, phone numbers, postal addresses, or unreviewed suggestions
- [x] The log: every exchange (who asked, when, provider, model, prompt version, the messages, each tool call with the records it returned, the answer, tokens, time, and outcome) in the ledger (migration 0017); **Admin → AI** shows the log and usage
- [x] `tessera ai check` makes one round trip to the configured provider; `tessera doctor` validates the section
- [x] ADR-0010: AI providers spoken over plain HTTP, without SDKs

**Acceptance (met):** with `[ai]` absent, the API document has no AI paths or schemas and a crawl of the app and the public site finds no mention; enabled against Ollama, a tool-call round trip works through both its OpenAI-compatible and its Anthropic-compatible endpoints; Bedrock requests match recorded fixtures and the signing test vectors; a key in the configuration file stops startup with a clear message.

## Slice 6: Grounded questions and answers ☐

**Goal:** answers from Tessera's records, cited, within the asker's access (AI-03, AI-08).

- [ ] **Ask**, in the header and the command palette, only when enabled: a side panel with streamed answers, the records cited shown as cards, follow-up questions, and a stop button
- [ ] Read-only tools over Tessera's own functions, run with the asker's access: search people, papers, grants, studies, and opportunities; open a record; experts for a topic; a person's or group's work; an Insight figure for a cohort; an impact thread; an opportunity's fit. No tool changes anything
- [ ] Grounding: the prompt (`assistant/v1`, versioned in code) has the model cite records by identifier; the server keeps only citations of records that a tool returned in this exchange and that the asker may read, and shows the rest as removed; an answer with nothing to cite becomes a decline that says what was searched; record text is passed to the model as data, never as instructions
- [ ] Access: the same policy as every route. Tests with a limited account confirm that draft pages, other people's saved searches and teams, and unreviewed suggestions never reach the model
- [ ] Evaluation: about 30 questions on the demo (answerable, unanswerable, and limited by access) with the records each should cite, run on `gemma4:31b-cloud`; recorded here: answers whose citations are all valid before filtering, answers citing an expected record, correct declines, and leaks, which must be none. If the model falls short, the maintainer is told before the slice closes

**Acceptance:** "Who works on CAR-T cell therapy?" cites people and papers that exist in the demo; a question the records cannot answer (a salary) is declined; a viewer limited to one group learns nothing from another group's draft page; every exchange appears in the log.

## Slice 7: Plain-language summaries ☐

**Goal:** reviewable lay summaries for profiles and group pages (AI-04, AI-06).

- [ ] A draft summary of a profile (requested by the person, a curator, or an admin) or of a group page (by its managers or curators), from confirmed records only; edit, approve, reject, or draft again; stored with its model, prompt version, a digest of its inputs, and who approved it (migration 0018); every change a curation event
- [ ] Approved summaries on profiles and as a group page block, labeled as drafted with AI assistance and approved by whom; on the public site only for people and groups that are public
- [ ] When a person's or group's records have changed a good deal, the inbox suggests a new draft; an approved summary is never replaced without approval
- [ ] With AI off, approved summaries are kept but not shown anywhere

**Acceptance:** a drafted summary is visible to no one but its reviewers until approved; an edited approval keeps both the draft and the edit; turning AI off hides approved summaries everywhere, the public site included, and turning it back on shows them again.

## Slice 8: Release prep ☐

- [ ] Guides for impact threads, opportunities, and the team builder; a guide to the assistant: providers and their settings (OpenAI-compatible, Anthropic-compatible, Amazon Bedrock), what is sent and what is not, the log, and turning it on; screenshots retaken from the demo, with the AI screens taken with AI enabled against the demo
- [ ] Evaluation, dependency and license review (Grants.gov's terms included), private-terms check over the whole repository
- [ ] `CHANGELOG.md` for 0.5.0; version bump
- [ ] PR, merge, tag `v0.5.0`, release, and the documentation site published, on the maintainer's go-ahead

**Acceptance:** the ORCID works-list check on the pilot reproduces 0.4.0's result; a grant's thread answers in 300 ms or less on the pilot's data; the pilot's first Grants.gov sync is timed and recorded; the full suite passes on SQLite and PostgreSQL 16; no dependencies were added; the AI-off crawl is clean; the private-terms check and a scan of every change since 0.4.0 for pilot names are clean.

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-08 | Slice 5 done. The providers speak the three protocols directly (ADR-0010). `gemma4:31b-cloud` through Ollama made a tool call and answered from the tool's result through both its OpenAI-compatible and its Anthropic-compatible endpoints, streaming text in 23 pieces, in under a second each. Bedrock is tested against recorded event streams and AWS's published Signature Version 4 vectors (get-vanilla, post-vanilla, and the query-order case all match); a live Bedrock call waits for the maintainer's go-ahead. Invisibility is enforced in three places: the routes and their access policies are installed only when enabled (the static policy table never names them, and a test checks both ways), the web app loads only the front-end modules `/api/v1/meta` names, and a test reads every file the app serves and the public site's code for any mention. `argparse` prints `==SUPPRESS==` for a hidden subcommand, so `tessera ai` is removed from the help's list directly. The exit export leaves the empty log out while the assistant is off. |
| 2026-11-08 | Slice 4 done. A member covers a topic when it, or a narrower heading, carries at least 0.15 of their concept profile (profiles are unit length; a typical topic carries about 0.23), so coverage says what someone's confirmed work is about. Suggestions for a gap reuse the expert finder with that one topic and leave out the members. Teams are visible to their members as well as their owner and the people it is shared with, because a team is usually discussed with the people on it; sharing and deleting stay with the owner and admins. The brief lists confirmed papers only, since its reader cannot see the evidence, and says when a member has none yet. |
| 2026-11-08 | Slice 3 done. An opportunity is read the way an expert search reads a question, but its words find papers when any of them match (ranked by how many, and how rare), not all: the first reading, needing every word, found no papers for titles such as "Circuit Mechanisms of Chronic Pain" and read several opportunities as nothing. Plural words are now read as singular ("germinal centers" names Germinal Center), in expert search too. On the demo, people in an opportunity's field score 0.7 to 0.98 and everyone else under 0.3, so the bands are strong from 0.6 and some from 0.35. Eligibility separates checks that decide (applicant type, deadline, internal deadline, limited submission, career stage, cost sharing) from notes that do not (the clinical-trial rule, a forecast's estimated dates); otherwise every NIH notice would have said "check the notice". A group's inbox counts the opportunities Tessera first held since the last review (fetched, or added), not the sponsor's posting date, which can be weeks old by the first fetch. On the pilot, 1,459 open opportunities are read in 100 seconds at each sync (194 before the reading stopped building search highlights it never shows); 254 of them fit at least one person, with 31 strong and 462 some fits. |
| 2026-11-08 | Slice 2 done. The pilot's first live Grants.gov sync read all 1,460 forecast and posted opportunities in 12 minutes 15 seconds with no failures (Grants.gov answered more slowly toward the end); a second sync, with nothing changed, took 1.9 seconds. A sync reads an opportunity again when the search's listing of it changes, two weeks after its last read, or when it is no longer listed (it closed); forecasts keep their status while listed, because their dates are estimates. Curation and administration pages now load when first opened: the app's first load had reached 101 KB of its 100 KB budget, and is now 79 KB. |
| 2026-11-08 | Slice 1 done. Threads add no ledger tables: they follow the `acknowledges`, `references`, and `funded_by` links Tessera already keeps (accepted and still reported) and iCite's clinical citations, so a curator's rejection removes a step at once. Citing articles get their own lake table, never `pubmed_article`, so they cannot become anyone's authorship candidates. iCite is asked for named fields, so `cited_by_clin` had to be added; existing rows fill as their monthly refresh comes round, or at once with the new `tessera ingest icite --full`. On the pilot, a full iCite read of 6,054 papers took 9 seconds and PubMed returned all 1,165 citing articles in 15; the 88 grants' threads answer in 17 ms at the median and 54 ms at most (the largest is a grant acknowledged by 1,256 papers), every grant's counts together in 70 ms, and the two impact figures in 43 and 22 ms. A first thread view dimmed what was not linked to a choice, but the linked papers sat out of sight in long columns; a choice now narrows each column to what is linked, with counts such as 1 of 34. The thread component loads only with the pages that show one, keeping the app shell under its 100 KB budget. The demo's clinical citations come from their own random stream (more often for papers with a higher translation score), so the rest of the demo is unchanged; the program project (P01) reaches a guideline through two papers. |
| 2026-11-08 | Planned. Every source wave 5 needs is public and keyless: Grants.gov's search and opportunity APIs answered without a key (about 1,460 forecast and posted opportunities, 377 of them NIH's), and iCite's publication API lists the clinical articles that cite each paper. Grant-to-paper, study-to-paper, and study-to-grant links already exist as assertions, so threads need only clinical citations added. Fit reuses the expert finder's reading of a question, so an opportunity is understood the same way a search is, with the same reasons shown. The AI providers are spoken over plain HTTP: `gemma4:31b-cloud` through Ollama made a correct tool call on the first try, and Ollama also answers on an Anthropic-compatible endpoint, so both adapters can be tested locally; Bedrock is tested against recorded fixtures and AWS's signing test vectors until a live check is approved. Threads, opportunities, and teams come first, because the assistant uses them as tools. |
