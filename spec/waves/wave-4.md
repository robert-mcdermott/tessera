# Wave 4: Insight (v0.4.0)

**Goal:** trustworthy portfolio intelligence and reproducible reporting. Leaders see what the organization, a division, a program, or a lab is funded for, publishes on, and runs as studies; every figure says exactly what it counts; reports can be frozen and regenerated; and the installation can be backed up, restored, and left.

**Branch:** `wave-4` · **Target version:** `0.4.0` · **Status:** ☑ released as 0.4.0 on 2026-11-08

## Exit criteria

The wave is done when, on a fresh checkout with the demo organization and the pilot:

1. Every figure in **Insight** carries its metric's definition and version, its cohort, its period, its sources, and their data timestamps, one click away (INT-01). No view ranks people by any metric (INT-11).
2. The funding portfolio shows a cohort's awards by fiscal year, institute, activity code, and mechanism, counted per core project and summed per fiscal-year record, with subprojects excluded and the amount type (total, direct, or indirect) labeled, so nothing is counted twice (INT-02). Grants whose projects end soon are listed as **end dates**, never as funding gaps (INT-05).
3. Topics show what a cohort publishes on, by year, with trends that are only claimed when there are enough papers to support them and small numbers marked (INT-06). The study portfolio shows studies by condition, intervention, phase, sponsor class, and registry status, labeled as registry status (INT-07, CLN-02).
4. A cohort is the organization, a group with or without the groups beneath it, a hand-picked set of people, or a people search, and can be saved. People save searches and see new matches (DIS-10). Reports put figures over a cohort and period; a report's snapshot regenerates it exactly (INT-03); exports in CSV, Parquet, and JSON carry their filters, definitions, and provenance (INT-04).
5. `tessera backup` and `tessera restore` round-trip an installation, between SQLite and PostgreSQL too; `tessera export` writes a full exit export that needs no Tessera to read (PLT-08). Admin → Operations shows runs, failures, durations, record counts, and sizes, and failures can send an alert (PLT-12).
6. A group's public content embeds in an existing website (LAB-09).
7. The test suite passes offline; lint and the private-terms check are clean; the documentation site is refreshed.

Requirements: INT-01 to INT-07, INT-11, DIS-10, LAB-09, PLT-08, PLT-12.

## Decisions from the maintainer

- **Single sign-on** (2026-11-08): local accounts stay the default and may be all a small organization needs; OpenID Connect sign-in is optional beside them, with any standards-based provider (such as Microsoft Entra ID) and an optional mapping from the provider's groups to Tessera roles. It stays in wave 6 (PLT-10), recorded in the roadmap.
- **AI assistant** (2026-11-08): eventually, and completely optional: off by default, with no hint of it in the app unless the configuration enables it; providers are any OpenAI-compatible or Anthropic-compatible endpoint (official or custom) and Amazon Bedrock. It stays in wave 5 (AI-01 to AI-08, ADR-0008 amended).

## Principles for every figure

- **A figure is a metric over a cohort and a period.** Metrics are defined once, in code, with an identifier and a version (`funding.awarded/v1`); a change to what a metric counts is a new version, never a silent edit.
- **The definition travels with the number.** The app shows it beside the chart, exports carry it, and snapshots freeze it.
- **The records behind a number are always available**: every figure can list what it counted and why each record is in the cohort (the link, its status, its source).
- **Small numbers are marked, not hidden.** Counts below `[insight] small_number` (5 by default) are flagged, and trends are not claimed from them.
- **Decided and likely are kept apart.** Papers and studies count when a member's link is confirmed or a strong suggestion not yet decided (as on profiles), and every figure says how many of each; *confirmed only* is one click away.
- **No league tables.** Figures break down by year, funder, activity, topic, phase, or group, never by person. Tables of groups sort by name until someone sorts them.

---

## Slice 1: Metrics, cohorts, and the figure ☑

**Goal:** the foundation every other slice uses (INT-01, INT-11).

- [x] A metric registry (`tessera.insight.metrics`): identifier, version, name, plain-language definition, counting rule, unit, sources, and caveats; `GET /api/v1/insight/metrics` and an **Insight → Definitions** page
- [x] Cohorts: the organization, a group (with or without the groups beneath it, counting each member's work while they were a member where the dates are known), picked people, or a people search; saved for everyone to reuse (ledger migration 0012)
- [x] Periods in calendar or fiscal years; the figure envelope: rows, columns and units, notes, small-number marks, the records counted, and the sources with their data timestamps
- [x] Figures are pure functions of their inputs (tables gathered from the lake and the ledger), so a snapshot can recompute them later from frozen inputs
- [x] **Insight** in the app: a cohort and period bar kept in the address, the figure card (chart, table, and **About this figure**), and the first figure: publications by year, confirmed and likely

**Acceptance (met):** the organization's publications by year equal the distinct PMIDs that count for its members; a program's figure counts a member's paper only from the year they joined when the start date is known; a year with three papers is marked as a small number; the figure's About panel names `publications.count/v1`, the cohort with its size, the period, and PubMed's last successful fetch.

## Slice 2: Funding portfolio ☑

**Goal:** money counted the way a sponsored-research office would count it (INT-02, INT-05).

- [x] Fiscal-year records from RePORTER: one per application, subprojects excluded from money, supplements counted once each; a grant counted once per cohort however many members are on it
- [x] The organization's portfolio is what was awarded to the organization; a cohort's is its members' grants awarded to the organization, or anywhere when asked
- [x] Awards by fiscal year (new and continuing), by institute, by activity code, and by mechanism; active grants; grants by group (overlapping, and said so); total, direct, or indirect amounts, always labeled
- [x] Grants with project end dates in the next 6, 12, or 24 months, labeled as end dates
- [x] The demo gains a center grant with subprojects, a supplement, training and career awards, and a second institute, so every rule shows

**Acceptance (met):** on the demo, the by-year, by-institute, and by-activity totals agree to the dollar; a P01's subproject records add nothing to its total; a supplement in the same fiscal year adds once; a grant with two member investigators in one lab counts once for that lab; the about panel says which amount is shown.

## Slice 3: Topics and studies ☑

**Goal:** what a cohort works on, and how its studies stand (INT-06, INT-07).

- [x] Topics: a cohort's papers by MeSH major topic, as headings or rolled up to research areas, with the share of the cohort's papers and counts by year
- [x] Trends: a topic is *rising* or *falling* only when both windows (the last three years against the three before) have enough papers; otherwise it says there are too few to tell
- [x] Studies by registry status, phase, study type, sponsor class, intervention type, condition (rolled up the MeSH hierarchy), and start year; registry status is labeled as the registry's, never as local recruitment
- [x] Publications' field-normalized citation (iCite's relative citation ratio) as a distribution, with recent papers marked as provisional

**Acceptance (met):** a heading with four papers in a year is marked; a topic with 12 papers over six years shows no trend; the demo's immunotherapy program shows its studies by phase and condition; every study figure says "registry status as reported to ClinicalTrials.gov".

## Slice 4: Saved searches, reports, snapshots, and exports ☑

**Goal:** questions people ask again, and answers they can stand behind (DIS-10, INT-03, INT-04).

- [x] Saved searches: save any search with its filters; the list shows new matches since you last looked (migration 0013)
- [x] Reports: a named set of figures over a cohort and period, shared with everyone who can read Insight
- [x] Snapshots: freeze a report (its resolved cohort, its period, every metric's definition and version, the exact input rows, the sources and their data timestamps, and the outputs) in `snapshots/<id>/` in the data home; `tessera report verify` recomputes the figures from the frozen inputs and confirms they are identical
- [x] Exports of any figure or snapshot: CSV (a zip with the table, the records counted, and the definitions), Parquet (with the same in the file's metadata), and JSON

**Acceptance (met):** a snapshot taken, then followed by a sync that adds papers, still shows and exports its original numbers, and verifies; a live report shows the new ones; every export names its metric versions, cohort, period, filters, and sources; a saved search shows two new matches after two matching grants arrive.

## Slice 5: Backup, restore, exit export, and operations ☑

**Goal:** an installation that can be recovered, moved, left, and watched (PLT-08, PLT-12).

- [x] `tessera backup` writes one archive: the ledger as portable rows (any backend), the lake, snapshots, and the configuration, with checksums; `tessera backup --verify` checks an archive; `tessera restore` loads it into an empty data home, SQLite or PostgreSQL
- [x] `tessera export` writes a full exit export: every ledger table and every lake table as Parquet and CSV, with a data dictionary, readable without Tessera
- [x] Admin → Operations: runs by source and job with durations, records, and failures; freshness; ledger, lake, and raw sizes; the last backup
- [x] Alerts: `[alerts]` sends one email through `[mail]` when a sync has failures or a source goes stale; `tessera doctor` warns when there is no recent backup

**Acceptance (met):** a backup of the demo restored into a new data home gives identical API answers; a SQLite backup restores into PostgreSQL; the exit export opens in DuckDB and in a spreadsheet; a failing source shows in Operations with its error and sends one alert.

## Slice 6: Embeddable group content ☑

**Goal:** a group's public content on its own website (LAB-09).

- [x] `tessera publish` writes, for every public group, small pages made to be framed (publications, people, openings, news) and a JSON feed, from the same allowlisted projection
- [x] The page editor shows the code to embed each, once the site's address is configured
- [x] Embed pages pass the same checks as the rest of the site, carry no scripts, follow the reader's light or dark preference, and link back to the group's full page

**Acceptance (met):** an embed of the demo program's publications shows its confirmed papers inside another page; the feed validates as JSON Feed 1.1; a group that is not public has no embeds.

## Slice 7: Release prep ☑

- [x] Guides for Insight, reports and exports, saved searches, embeds, and operations (backup, restore, exit, alerts); every screenshot retaken from the demo, with new ones of Insight, a snapshot, a saved search, Operations, and an embed
- [x] Evaluation, dependency and license review, private-terms check over the whole repository
- [x] `CHANGELOG.md` for 0.4.0; version bump
- [x] PR, merge, tag `v0.4.0`, release, and the documentation site published, on the maintainer's go-ahead

**Acceptance (met):** the ORCID works-list check on the pilot reproduces 0.3.0's result exactly (193 listed papers, 95.3% found, 97.3% of them strong); every Insight figure answers in 160 ms or less on the pilot's data; the pilot's ledger takes migrations 0012 and 0013 and backs up in under two seconds; the full suite passes on SQLite and PostgreSQL 16; no dependencies were added; the private-terms check and a scan of every change since 0.3.0 for pilot names are clean.

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-08 | Released as 0.4.0 on the maintainer's go-ahead. |
| 2026-11-08 | Slice 7 prepared. The demo now saves a cohort, freezes a program's annual review as a snapshot, keeps a saved search with two new matches, and takes a backup, so every new screen has something to show. On the pilot (migrated to 0013 after a backup), the ORCID works-list check reproduces 0.3.0 exactly; every Insight figure answers within 160 ms (topics are the slowest, reading MeSH headings for about a thousand papers); the pilot has no groups yet, and the group figures say so. The README's status line had been left at 0.2.0 through the last release; it now describes 0.4.0. |
| 2026-11-08 | Slice 6 done. Embeds are static pages from the public site rather than a script that renders content, so they need nothing running, carry no code into someone else's page, and show only what the allowlisted projection already shows. A first version followed only the reader's preference and showed a dark box in a light lab website; embeds now also come held to light or dark. They open links in the surrounding page, are kept out of the sitemap and search indexes, and always exist for a public group so that pasted code keeps working. The JSON feed covers sites that render content themselves. |
| 2026-11-08 | Slice 5 done. Backups hold the ledger as portable rows rather than a database file, so one format serves SQLite and PostgreSQL and also moves an installation between them; restore reflects the schema at the backup's revision, loads the rows (references within a table are set once every row is there), resets PostgreSQL's integer sequences, and upgrades. A demo backup restored into a new data home gives identical ledger rows and identical API answers once the index is rebuilt; on PostgreSQL 16, a SQLite backup restored with identical rows. The comparison found that the search index and grant pages took a subproject's record as a grant's latest when several share a fiscal year; fixed. The demo now rebuilds its index after its last direct changes. Alerts are emailed once per set of issues, so a stale source does not send a message every night. |
| 2026-11-08 | Slice 4 done. A snapshot keeps the inputs, not the whole lake: only the rows its cohort's figures read, so it is small (tens of kilobytes for a program) and cannot drift when the lake is compacted. Verification recomputes from those inputs with the code's metrics and fails, naming the metric, when a metric's version has moved on, rather than silently using the new definition. Deleting a report keeps its snapshots, because someone may have cited them. CSV exports are zips so that the definitions travel with the numbers; Parquet files also carry them in their own metadata. Saved searches remember up to 5,000 matches each and are visible only to their owner; what a search finds when it is saved counts as seen. |
| 2026-11-08 | Slice 3 done. Topics count major MeSH headings only: every paper carries check tags such as Humans and Mice, which say nothing about its subject. Trend windows are halves of the period's complete years, at most three years each, so that a five-year window (the default first sync) can still show trends; the year under way is never in a window. A study counts for the period when it was under way during it (by its start and completion dates), not only when it started, because a portfolio is what is running. Field-normalized citation keeps the last two years apart and says that a ratio describes papers, not people. Correction to slice 2's note: the demo's new grants had joined the lists that papers draw their acknowledgments from, which shifted the rest of the demo; they no longer do, and the demo has its 717 papers again. |
| 2026-11-08 | Slice 2 done. Award types come from the first digit of the project number (1 new, 2 renewal, 3 supplement; the rest continuing), which RePORTER does not state separately. The organization's portfolio is what was awarded to it, matched by the awardee's name with the same rules as everywhere else, so grants of people not yet in the directory still count; a cohort's portfolio needs a member on the grant. Grants by group are listed by name, and say that their amounts overlap, so the figure cannot be read as a ranking. Fiscal-year figures default to the fiscal year under way (October onward) and say it is incomplete. The demo's mechanisms now use RePORTER's names, labeled in words. One demo test assumed a person's research areas sum to one; they are shares of the whole profile with eight kept, so the test now checks that. |
| 2026-11-08 | Slice 1 done. Metric definitions live in code with a version rather than in the ledger: a definition is part of what the code computes, and a snapshot records the definition it used. Figures are computed from tables gathered once for the cohort (the members with their years, links, and lake rows), so a snapshot can keep those tables and compute the figures again. Group cohorts count a member's work by year, from the year they joined to the year they left; most memberships have no dates yet, so About says how many do. The first sync's window (`sync.window_years`) makes earlier years incomplete; the default period starts at the first complete year, and figures that reach back further say so. |
| 2026-11-08 | Wave planned, after folding the maintainer's guidance on single sign-on and the AI assistant into the roadmap (waves 6 and 5). The 0.3.0 release date was corrected to 2026-11-08 in the changelog, roadmap, and wave 3 file. |
