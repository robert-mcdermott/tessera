# Wave 2: Connections (v0.2.0)

**Goal:** turn the graph into discovery: who works on what, who should meet, and why. Make the evidence stronger with ORCID, and let more than one person use Tessera, each with the right permissions.

**Branch:** `wave-2` · **Target version:** `0.2.0` · **Status:** ◐ slice 1 done; slice 2 next

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

## Slice 2: People model ☐

**Goal:** one record per real person, and memberships with dates.

- [ ] `same_as` proposals: same ORCID iD, same NIH profile, or a strong name, coauthor, and topic match; reviewed like other suggestions
- [ ] Merge re-points every link through the surviving record; unmerge restores the originals exactly (REC-07), with a ledger migration and a test that round-trips a populated ledger
- [ ] Dated unit memberships, concurrent memberships, and a roster import that carries start and end dates (REC-08)

**Acceptance:** merge then unmerge leaves the ledger identical; memberships show on profiles with their dates.

## Slice 3: Accounts and roles ☐

**Goal:** more than one person can use Tessera, each with the right permissions.

- [ ] Ledger tables `user`, `role_grant`, and `session`; passwords hashed with the standard library's scrypt; sessions in `HttpOnly`, `SameSite=Strict` cookies with CSRF protection; sign-in and sign-out pages
- [ ] Roles admin, curator, researcher, and viewer, enforced in every API route and in search (PLT-06); curators can be scoped to people or units; researchers review their own suggestions (CUR-06)
- [ ] Every decision is attributed to the signed-in user; `tessera users add | list | disable | reset-password`
- [ ] Single-operator mode remains for a server that listens on this machine only; listening on any other address requires accounts

**Acceptance:** a test signs in as each role and checks every route; a scoped curator cannot decide outside their scope; no route is reachable without a session when accounts are on.

## Slice 4: Evidence v2 ☐

**Goal:** weights learned from local decisions, and auto-accept that is safe.

- [ ] Fit `authorship/v2` weights to local decisions (logistic regression in pure Python) with a held-out evaluation; short given names that many people share weigh more heavily against a match (a lesson from the v0.1.0 pilot check)
- [ ] Evaluation report in the CLI and the admin area: precision, recall, and calibration by band
- [ ] Auto-accept (CUR-05): off by default; an admin can enable it only at a threshold whose latest evaluated precision is at least 99%; auto-accepted links are marked as such and can be undone together

**Acceptance:** v2 beats v1 on held-out decisions or is not released; enabling auto-accept below the precision bar is refused.

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
