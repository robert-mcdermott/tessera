# Wave 3: Showcase (v0.3.0)

**Goal:** give groups (labs, programs, divisions, centers, and others) a living home that their own representatives maintain, open a public window onto them, and deepen topics with the MeSH hierarchy.

**Branch:** `wave-3` · **Target version:** `0.3.0` · **Status:** ◐ slices 1 to 4 done; slice 5 next

## Exit criteria

The wave is done when, on a fresh checkout with the demo organization and the pilot:

1. `tessera sync` reads the year's MeSH descriptor data. Expert search understands synonyms ("cancer" finds the people whose papers are indexed under "Neoplasms"), a broad heading includes its narrower ones, and profiles and groups show broader research areas.
2. An admin creates, edits, nests, and archives groups of any configured kind (division, department, program, center, core, lab, and so on) in the app, through the API, or from a file whose rows carry external identifiers, so that an organization's own feed can supply groups later. Memberships carry roles and dates, and alumni stay visible as alumni.
3. Every group has a showcase page: what it does, its leaders and contact, its people (current and alumni), the publications attributed to it, its grants and studies, its topics, and its connections.
4. A group's managers, representatives with a role limited to that group, choose and arrange its page blocks, attribute publications to it, and publish with draft, preview, history, and rollback. Each week they review one inbox of new papers, grants, roster changes, and expiring openings.
5. Openings expire on their own; themes and projects link people, funding, outputs, and studies.
6. `tessera publish` generates a static public site from an allowlisted projection: public profiles and group pages, with citations and links rather than abstracts, that pass accessibility checks and carry the organization's brand kit.
7. The test suite passes offline; lint and the private-terms check are clean; the documentation site is refreshed.

Requirements delivered: LAB-01 to LAB-08, PLT-07, PLT-11, PLT-13, CUR-08, and the MeSH hierarchy for DIS-04.

## Decisions from the maintainer

- **MeSH descriptor data** is approved as a source (2026-11-08).
- **Groups are created and curated by hand for now**, designed so that an external source (an HR or organization feed) can supply them later: every group and membership records where it came from and the identifier it has there.
- **A group's manager or representative controls what its view shows.**
- **Defaults, accepted:** the public site is static pages that Tessera generates (the internal app stays off the internet); digests are in the app first, with email optional through a configured mail server; public pages show citations and links, not abstracts.

---

## Slice 1: MeSH ☑

**Goal:** a vocabulary that knows synonyms and hierarchy. See [data-sources.md](../data-sources.md#mesh-nlm).

- [x] A connector for NLM's annual MeSH descriptor file (`desc{year}.gz`, XML, about 17 MB), read once a year (from November, the next year's file is looked for); a `mesh_descriptor` lake table with each heading's identifier, name, tree numbers, entry terms (synonyms), scope note, class, and MeSH year
- [x] A derived vocabulary of the headings people hold and every heading above them, with synonyms and counts of the people beneath each, so searches stay small and fast
- [x] Expert search reads entry terms: phrases that name a heading or one of its synonyms are taken as that heading and lead what the papers suggest, with the synonym shown; concept search finds headings by synonym, exact matches first
- [x] A broad heading includes its narrower ones: asking for "Neoplasms" also finds people whose papers are indexed under "Breast Neoplasms", at 0.7
- [x] Research areas: profiles roll up the tree to the diseases, body systems, organisms, and processes people study, weighed by how few share them; the concept landscape is colored by MeSH category
- [x] NLM is credited as the source, with the MeSH year, wherever synonyms, hierarchy, or areas appear; tests run on a small fixture in NLM's format; the demo organization has a small MeSH of its own

**Acceptance (met):** on the pilot, MeSH 2026 (31,110 headings) loads in about ten seconds; "cancer" is read as Neoplasms by synonym alone and finds the people who work on pancreatic, breast, and lung neoplasms; "brain tumors" is Brain Neoplasms; research areas read as fields (Neoplasms, Nervous System, Viruses, Digestive System Diseases, Plant Structures); without MeSH data, everything works as before.

## Slice 2: Groups ☑

**Goal:** labs, programs, divisions, and any other grouping, as first-class records. See [groups.md](../../docs/groups.md).

- [x] Units become groups with configurable kinds (`[vocabulary]`, PLT-11) and nesting; a group may belong to several parents (a program spans divisions)
- [x] Every group and membership records its source (`manual`, `roster`, or a named feed) and its external identifier there; a later import from the same source updates rather than duplicates
- [x] Admin → Groups: create, edit, nest, archive, and merge groups; add and end memberships with roles and dates; `tessera groups import` reads CSV or JSON with external identifiers; the roster import becomes one way in
- [x] API for groups and memberships, with access policies; ledger migration 0007

**Acceptance (met):** existing units migrate to groups from the roster with addresses, and their memberships become plain members; an import from a named source creates, then updates, nests under several parents, archives what it no longer lists, and ends memberships it drops; a merged group's address and identifier lead to the group that remains; a curator scoped to a division reaches people in a program that spans two divisions; the directory's filters follow membership changes without a rebuild.

## Slice 3: Group pages ☑

**Goal:** a showcase for every group (LAB-01, LAB-02, LAB-03).

- [x] `/groups` (a directory) and `/groups/<slug>`: what the group does, leaders and contact, people (current and alumni), publications attributed to the group, grants, studies, topics and broader areas, and connections
- [x] Lab attribution as its own assertion (`attributed_to`): suggested from members' papers with evidence (authorship, grants, affiliation, the group's topics), decided by the group's managers or curators; a PI's authorship alone never makes a paper a group output
- [x] Profiles list a person's groups and roles; the demo organization gains labs, programs, and divisions

**Acceptance (met):** the demo has 5 divisions, 22 labs, 3 programs that each span two divisions, and a shared resource, from an import of its registry; each lab's page shows its leader, people and alumni, attributed papers by year, grants, topics and areas, and the labs and programs it writes with; suggestions show their evidence and are decided on the page; a leader's authorship alone scores below the review threshold.

## Slice 4: Managers and the editorial workflow ☑

**Goal:** representatives control their group's view (LAB-04, LAB-05).

- [x] A `manager` role limited to a group (and, optionally, the groups below it)
- [x] Page blocks from an approved set (about, people, featured publications, grants, studies, themes, openings, contact, news), arranged without code
- [x] Draft, preview, publish, history, and rollback for each group's page

**Acceptance (met):** a manager of one group saves a draft, previews it, and publishes it; viewers see the published page and never the draft; the manager cannot edit another group or decide about people; publishing again and restoring version 1 gives version 3 with version 1's blocks; text with markup stays text; the demo's immunotherapy program and busiest lab have published pages and the program has a manager account.

## Slice 5: Inbox, openings, themes ☐

- [ ] The weekly approval inbox for managers (LAB-06): new papers to attribute, new grants, roster changes, and expiring openings, decided in one place
- [ ] In-app digests of pending work (CUR-08); email when a mail server is configured
- [ ] Openings with an expiry date (LAB-08); research themes and projects that link people, funding, outputs, and studies (LAB-07)

## Slice 6: The public site ☐

- [ ] `tessera publish` writes a static site from an allowlisted projection (PLT-07): public profiles and group pages; people and groups opt in; citations and links, not abstracts
- [ ] Sitemap, page metadata, and accessibility checks; served by any web server

## Slice 7: Brand kit ☐

- [ ] `[branding.palette]`: core colors by day and night (surfaces, text, accent and its text, record types, stripe), footer links, a favicon, and a social preview image (PLT-13), for the app and the public site
- [ ] `tessera doctor` checks every configured color pair against WCAG AA and fails any below it

## Slice 8: Release prep ☐

- [ ] Guides for groups, managers, and the public site; screenshots retaken and the documentation site published
- [ ] Evaluation, dependency and license review, private-terms check over the whole repository
- [ ] `CHANGELOG.md` for 0.3.0; version bump; PR, merge, tag `v0.3.0`, and release on the maintainer's go-ahead

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-09 | Slice 4 done. Access levels gained MANAGE (below REVIEW) so that a manager reaches group routes without the review queue; each handler still checks the group. Blocks are settings, not HTML: about and news are plain paragraphs, links must be http(s), and featured papers are PubMed IDs, so a page can carry no markup into the app or the public site. Restoring a version publishes it again as a new version rather than rewinding, so the history only grows. Page drafts are per group, not per editor: two managers of one group share a draft, and the last save wins (with who and when shown), which is simple and enough for groups with one or two editors. |
| 2026-11-09 | Slice 3 done. Attribution is a separate link because membership and authorship together are not enough: a PI's collaborations and a postdoc's earlier work are not the lab's. Every attribution is decided by a person, so the score only orders suggestions and is kept simple (additive, documented in the guide); a learned model can wait for decisions to learn from. Divisions and departments are not attributable by default, and "writes most with" lists only attributable kinds, because a division would claim, and work with, everything. The graph's collaboration communities became "clusters" so that "group" means one thing. Group contact details (email, website) went into the ledger (migration 0008) rather than page blocks, because the public site needs them as data. |
| 2026-11-08 | Slice 2 done. The `unit` table keeps its name (and the `unt_` IDs, the curator scope type `unit`, and the `/graph/units` routes) so that nothing outside the groups work had to move; the product and the new API say "group". A membership's role is its qualifier key, so one person can be a lab's lead and a member of it at once, and a role change from a feed updates the same membership when the feed gives membership identifiers. Feeds are in charge of what they supply; removing a membership by hand is the one decision a feed cannot undo. Group merges are recorded but not undoable from the app, unlike person merges: a group merge touches roles and nesting as well as links, and a reversible journal can wait until someone needs it. |
| 2026-11-08 | Slice 1 done. Areas are rolled up only in the branches that say what someone studies (anatomy, organisms, diseases, psychology, phenomena); the first attempt rolled up everything and every chemist's top area was "Amino Acids, Peptides, and Proteins". Areas shared by nearly everyone ("Cells") are weighed down by IDF, as topics are. Two quirks of NLM's file server: a missing file comes back as an HTML page with a 200 status, and asking for gzip makes clients unpack the 17 MB file into 300 MB of XML. DuckDB 2.0 rejects the old `x -> …` lambda syntax. |
| 2026-11-08 | Wave planned. NLM discontinued the ASCII MeSH files in 2026, so the connector reads the XML descriptor file. NLM's terms ask users to credit NLM as the source clearly, not to imply its endorsement, and to name the MeSH version when the data is redistributed. |
