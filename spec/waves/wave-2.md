# Wave 2: Connections (v0.2.0)

**Goal:** turn the graph into discovery: who works on what, who should meet, and why. Make the evidence stronger with ORCID, and let more than one person use Tessera, each with the right permissions.

**Branch:** `wave-2` · **Target version:** `0.2.0` · **Status:** ◐ slices 1–4 done; slice 5 next

## Exit criteria

The wave is done when, on a fresh checkout with the pilot organization:

1. `tessera sync` also reads the public ORCID records of directory members, and ORCID evidence shows in the evidence drawer.
2. Two directory records for the same person can be merged and unmerged, restoring every link exactly; unit memberships carry dates.
3. People sign in with local accounts. An admin, a curator scoped to one unit, a researcher, and a viewer each see and can do exactly what their role allows, in the API and the web app.
4. `authorship/v2` weights are fitted to local decisions and evaluated. Auto-accept stays off until an admin enables it, and the admin can enable it only at a threshold where the latest evaluation shows at least 99% precision.
5. "Who works on X?" returns ranked people with the concepts that match. Each profile lists similar people with shared concepts, and the collaboration graph shows the person focus, collaboration network, and concept landscape views, each with a list fallback.
6. Missed connections and the unit matrix explain why each pair or pair of units is shown.
7. The test suite passes offline; lint and the private-terms check are clean.

Requirements delivered: REC-02 (ORCID), REC-07, REC-08, CUR-05, CUR-06, CUR-10, DIS-04–09, PLT-06.

## Decisions needed from the maintainer

- **MeSH descriptor data.** The MeSH hierarchy lets specific concepts roll up into broader areas. It is a candidate source that needs approval ([data-sources.md](../data-sources.md)). Without it, concept profiles are flat and slice 5 still works.

---

## Slice 1: ORCID ☑

**Goal:** researcher-confirmed evidence. See [data-sources.md](../data-sources.md#orcid-wave-2) and [evidence-engine.md](../evidence-engine.md).

- [x] ORCID connector: public records by iD (works with their PubMed IDs and DOIs, and employment), through the shared HTTP client with a rate limit, raw capture, and replay; parser to lake tables `orcid_record`, `orcid_work`, `orcid_employment`; deprecated, locked, and missing records kept as data. The public API needs no credentials
- [x] Which iDs are read: the confirmed iDs of directory members, refreshed every 30 days, never the iDs of everyone who appears in PubMed; papers their records list are fetched from PubMed when missing
- [x] Evidence (`authorship/v1.1`): the paper is on the person's ORCID record (+6.0); a curator-rejected iD (−8.0); the same iD on the person's other likely papers (+2.0 or +3.0) or a clearly different one (−2.0, weak, for people with two iDs); deprecated iDs count as their replacements; an iD suggested for the person is never a conflict
- [x] Identifier claims: iDs on at least two of a person's likely papers (judged without ORCID evidence) are suggested, never confirmed automatically; curators confirm, reject, restore, add, or remove them on the profile, with a note; every decision is logged (migration 0003); an iD on two people is flagged on both (REC-02)
- [x] Evaluation without circularity: the ORCID-based check from v0.1.0 cannot judge ORCID features, so this slice was evaluated on the synthetic organization (strong band still 100% precise across eight worlds; false matches above the review threshold in the hardest world fell from 8 to 3) and by reviewing every pilot suggestion that changed band

**Acceptance (met):** ingest is idempotent and replayable like the other sources; evidence appears in the drawer with its source; the synthetic organization's strong band keeps 100% precision and recall rose slightly. On the pilot, 49 iDs were suggested for 48 of 70 members, and confirming one made 25 papers decisive matches. Resolving twice changes nothing (a test checks it).

## Slice 2: People model ☑

**Goal:** one record per real person, and memberships with dates.

- [x] `same_as` proposals (`same_as/v1`): a shared identifier, an ORCID iD replaced by the other record's, author positions both records claim, shared grants, and names, never a name alone; a **Possible duplicates** page to merge, keep apart, or defer
- [x] Merge moves every link, name, and identifier to the record kept and journals each change; unmerge replays the journal backwards and restores the originals exactly (REC-07); migration 0004; a test round-trips a populated ledger with conflicting decisions
- [x] Dated unit memberships, concurrent memberships, and a roster import that carries start and end dates (REC-08); former status when every membership has ended; current units only in the directory

**Acceptance (met):** merge then unmerge leaves the ledger identical, including links both records decided differently; a merge survives a resolve and still unmerges cleanly; on the synthetic organization every roster-and-grant duplicate is proposed at 95% or higher and no pair of different people is; memberships show on profiles with their dates. On the pilot, which has no roster, no duplicates are proposed; a duplicate created from a one-row roster was proposed at 99%, merged, and undone through the web app.

## Slice 3: Accounts and roles ☑

**Goal:** more than one person can use Tessera, each with the right permissions.

- [x] Ledger tables `account`, `role_grant`, and `auth_session` (migration 0005); passwords hashed with the standard library's scrypt; server-side sessions in `HttpOnly`, `SameSite=Strict` cookies with a CSRF token on every change and throttled sign-in; a sign-in page, a user menu with sign-out and password change, and an Accounts page for admins (ADR-0009)
- [x] Roles admin, curator, researcher, and viewer, enforced in every API route through one policy table (PLT-06; search is read access, and discovered people stay with curators for everyone); curators can be scoped to units (and the units under them) or people; researchers review their own suggestions (CUR-06); the web app shows each person only what they may use
- [x] Every decision is attributed to the signed-in user; `tessera users add | list | disable | enable | reset-password | roles`
- [x] Single-operator mode remains for a server that listens on this machine only, and answers only to requests addressed to it; listening on any other address requires accounts; changes sent from another site are refused in both modes

**Acceptance (met):** a test signs in as each role and checks every route against the policy table, and fails if any route lacks a policy; a curator scoped to a unit decides only for its people and sees only them in the queue; a researcher reviews only their own suggestions; every API route but health, metadata, and signing in answers 401 without a session; CSRF, other-site, DNS-rebinding, throttling, expiry, sign-out, and last-admin protections are tested. Checked in the web app on the pilot data as a researcher and an admin.

## Slice 4: Evidence v2 ☑

**Goal:** weights learned from local decisions, and auto-accept that is safe.

- [x] Fit `authorship/v2` weights to local decisions (one multiplier per feature and an offset, logistic regression in pure Python, pulled toward the hand-set weights) with an evaluation on held-out people; a first name of at most two letters counts against a full-name match (`authorship/v1.2`, a lesson from the v0.1.0 pilot check)
- [x] Evaluation in the CLI (`tessera evidence status | evaluate`) and the admin area (Admin → Evidence): precision, recall, log loss, calibration, and precision at each candidate threshold
- [x] Auto-accept (CUR-05): off by default; an admin can enable it only at a threshold whose latest evaluated precision is at least 99% over at least 50 decisions; suggestions with conflicting evidence are never accepted; auto-accepted links are marked, never training data, and undone together (migration 0006)

**Acceptance (met):** a learned model is offered only when it beats the hand-set one on held-out people (tested both ways, and fitting recovers known weights from generated decisions); enabling auto-accept below 99% or on too few decisions is refused; on the synthetic organization with simulated decisions, auto-accept at 0.99 accepted 477 suggestions, all correct, and undoing the batch restored every one. The pilot has no curator decisions yet, so neither has been used there.

## Slice 5: Concepts and expert finder ☐

**Goal:** who works on what, with reasons. See [discovery.md](../discovery.md).

- [ ] Concept profiles from accepted work (DIS-04): MeSH weighted by major topic, author position, recency, and IDF; provisional concepts from keywords and title terms until MeSH indexing arrives; `derived/concept_profile.parquet`
- [ ] Expert finder (DIS-05): a query becomes a small profile; people are ranked by similarity with the matching concepts shown
- [ ] Similar people on every profile with shared concepts (DIS-06); pairs precomputed to `derived/similarity_pair.parquet`
- [ ] Owners can see what drives their own profile

**Acceptance:** on the synthetic organization, people built around a topic are found by it; every result shows its concepts.

## Slice 6: Collaboration graph ☐

**Goal:** see the shape of the research community.

- [ ] `derived/coauthor_edge.parquet` from accepted authorships, grants, and studies
- [ ] Person focus, collaboration network, and concept landscape views (DIS-07): Canvas 2D drawing, force layout in a Web Worker, hover to dim, search and fly-to, focus and breadcrumbs, state in the URL
- [ ] A list view with the same nodes and relationships for every graph, keyboard navigable; reduced motion shows the settled layout

**Acceptance:** about 150 nodes lay out without blocking the page; every view has a working list fallback.

## Slice 7: Missed connections and the unit matrix ☐

**Goal:** who should meet, and which units share interests but rarely work together.

- [ ] Missed connections (DIS-08): high-similarity pairs without shared papers, grants, or studies, with the shared concepts as the reason
- [ ] Bridges: people who connect otherwise separate groups
- [ ] Unit matrix (DIS-09): unit-by-unit similarity versus collaboration, sorted by density, each cell opening its list

**Acceptance:** every pair and cell explains itself; copy never suggests a ranking of people.

## Slice 8: Release prep ☐

- [ ] Guides updated for discovery, accounts, ORCID, merges, and auto-accept; command reference and OpenAPI regenerated
- [ ] Evaluation on pilot decisions, summarized without identifying data
- [ ] Dependency and license review; private-terms check over the whole repository
- [ ] `CHANGELOG.md` for 0.2.0; version bump
- [ ] PR, merge, tag `v0.2.0`, and release on the maintainer's go-ahead

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-07 | Wave planned. Before slice 1, a design review of the palettes led to three changes: record-type colors were moved clear of the status colors (a test now enforces at least 28° of hue between them in every palette and mode), Cassette's stripe became a thin sunset gradient with faint pinstripes in the hero, and grant charts gained a brighter gold fill. An organization's own palette, footer links, favicon, and social preview image were planned as the brand kit (PLT-13) for wave 3, with the public site. |
| 2026-11-07 | ORCID's public API answers without credentials, so no credentials decision is needed. Tessera reads only directory members' iDs, at most four requests a second. |
| 2026-11-08 | Slice 1 done. iDs are suggested from papers but only a curator confirms one, because a confirmed iD is decisive. Support for a suggestion is judged on evidence other than ORCID, so a decision about one iD never withdraws a person's second iD and ORCID evidence never supports itself. The model version is `authorship/v1.1` (hand-set weights); learned weights remain `authorship/v2` in slice 4. Found while testing: tied topics were listed in a random order in explanations, so every resolve rewrote hundreds of unchanged suggestions. |
| 2026-11-08 | Slice 2 done. Merges move rows rather than resolving links through a pointer at read time, so every reader stays simple; a journal makes them exactly reversible. Automation never merges: duplicates are proposed for a curator. A roster without identifiers used to split a person listed under two units into two records; rows with the same full name in one file are now one person. Memberships stay one per person and unit (a second spell in the same unit updates the dates). |
| 2026-11-08 | Slice 3 done. Accounts, roles, and sessions live in the ledger with no new dependency (ADR-0009). Authentication runs in a middleware so that it covers the API document and files too; authorization reads one policy per route. Found while testing: on SQLite, Python's driver kept schema changes outside the migration's transaction, so a failed migration could leave a half-built table; SQLAlchemy now issues BEGIN itself and DDL rolls back whole. Personal API tokens and single sign-on are left for later (wave 6 for SSO). |
| 2026-11-08 | Slice 4 done. Learned weights scale the hand-set features rather than replace them, so explanations stay the same and a few decisions barely move the model (an L2 pull toward 1, strength 20, chosen so that 30 decisions move a multiplier by less than 0.2). The synthetic organization is too separable to learn from (fewer than 20 rejections), so learning is tested on generated decisions with known weights. Undoing an auto-accept batch also switches auto-accept off, since the next sync would otherwise accept the same links again. |
