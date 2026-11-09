# Wave 6: Profiles (v0.6.0)

**Goal:** a full profile for every researcher: what Tessera builds from public records, beside the sections the researcher keeps (overview, interests, positions, education, honors, service, teaching, talks, links, featured papers). Every profile is open to everyone inside the organization, public only by the researcher's choice, and the source of the researcher's CVs and NIH biosketches.

**Branch:** `wave-6` · **Target version:** `0.6.0` · **Status:** ◐ slices 1 to 6 done; slice 7 ready, waiting for the go-ahead to release

## Exit criteria

The wave is done when, on a fresh checkout with the demo organization and the pilot:

1. Every signed-in account can open any directory member's profile and see every section. Only the person, a curator for them, an admin, or someone the person names can change it. Edits take effect at once, and every change is in the profile's history, with who made it and when (PRF-01, PRF-02, PRF-05).
2. The researcher, or someone acting for them, opts the profile in to or out of the public site and chooses which sections and which entries it shows. The public page shows exactly those; internal views always show everything (PRF-03).
3. A researcher keeps several named CVs and NIH biosketches. For each, they choose its sections, entries, and order, then print it to PDF or download it as Markdown. A biosketch follows NIH's Biographical Sketch Common Form and NIH Biographical Sketch Supplement, with NIH's limits checked as they type (PRF-04).
4. Every entry records where it came from, so that an import (ORCID's public record, an HR feed) could later add to a profile without duplicating anything; nothing requires one (PRF-06, design only).
5. Wave 5's plain-language summary and the opportunities that fit the researcher sit in the new profile; with the assistant off, nothing on a profile mentions it.
6. The test suite passes offline; lint and the private-terms check are clean; the documentation site is refreshed.

Requirements: PRF-01 to PRF-05; PRF-06 shapes the design but schedules no import.

## Decisions from the maintainer

- **Scope** (2026-11-08): profiles where people can see a researcher's activities, honors, education, and so on; researchers can customize their CV and biosketch, and opt their profile in to or out of the public site; every profile stays visible inside. Not too complex, no external integration required (but none precluded), and no annual-review process.
- **Order** (2026-11-08): Profiles are wave 6, after Impact; Adoption moves to wave 7.

### Defaults taken (either can change)

- **Who keeps a profile:** the person (through the account linked to them), a curator whose scope covers them, an admin, and anyone the person names to act for them. Viewers and other researchers read it but cannot change it.
- **Who sees a CV or biosketch:** they are working documents, so the person, the people acting for them, their curators, and admins see them; everyone else sees the profile itself.
- **Edits are direct.** Personal sections have no drafts or approvals: a change shows at once, and the history says who made it and what it was before.
- **The biosketch is prepared, not certified.** NIH requires the Common Form and its Supplement to be completed and certified in SciENcv by the investigator themselves (a delegate may help fill them in, but not certify). Tessera arranges the content in NIH's sections, checks the limits, and gives it in a form that can be copied into SciENcv section by section. It says so where the biosketch is built.
- **One citation style:** authors, title, journal, year, volume and pages, with the DOI and PubMed ID, close to the National Library of Medicine's style. Other styles can come later.
- **With the assistant on** (and only then), a researcher can ask for a first draft of the biosketch's personal statement or of a contribution to science from papers they choose. It arrives in the editor as a draft, labeled, and nothing is saved until they save it.

## What NIH's biosketch is now

Checked 2026-11-08 against NIH's notice [NOT-OD-26-018](https://grants.nih.gov/grants/guide/notice-files/NOT-OD-26-018.html): for application due dates on or after January 25, 2026, a senior or key person's biosketch is the **Biographical Sketch Common Form** together with the **NIH Biographical Sketch Supplement**, completed and certified in SciENcv as one PDF, with no page limit for the two together.

- **Common Form:** identifying details; persistent identifier, where the person's **ORCID iD** must appear (it is required and linked to eRA Commons); professional preparation (education and training); appointments and positions; products, **up to 5 closely related to the project and up to 5 other significant products**; certification.
- **Supplement:** **personal statement** (no citations, at most 3,500 characters); **honors** (at most 15); **contributions to science** (no citations, at most 5, each at most 2,000 characters; they may refer to the products listed in the Common Form).
- **Gone:** Section D, scholastic performance, is no longer accepted.

Tessera follows this form; if NIH changes it, the biosketch's checks change with it, and a saved biosketch says which form it was built for.

## Principles for this wave

- **Simple by design.** Sections and entries, edited directly, with history. No workflows, no review cycles, no reporting.
- **The researcher's words stay theirs.** Tessera never rewrites what someone entered. A later import would add entries marked with their source and never overwrite a hand-made one.
- **Built from confirmed work.** Featured papers, a CV's publications, and a biosketch's products come from the person's confirmed links only; suggestions never appear on a profile's sections, a CV, or a biosketch.
- **Inside sees everything; outside sees only what the researcher chooses.** The public flag and the public choices are the researcher's, and the public site is still built from an allowlist.
- **Every change is recorded.** Who changed what, when, from what, and on whose behalf.
- **No league tables.** Profiles, CVs, and biosketches show no metric that ranks a person.

---

## Slice 1: The sections a researcher keeps ☑

**Goal:** the data and rules behind profile sections, with their history (PRF-02, PRF-06).

- [x] Profile entries (migration 0019): each belongs to a person and a section, with the section's fields, an order, whether it may appear publicly, and where it came from (entered by hand, or a source and that source's identifier for it, so an import can match instead of duplicating). Sections: overview and research interests (text), positions and appointments, education and training, honors and awards, memberships and service, teaching and mentoring, talks and presentations, links, and featured publications (confirmed papers only, at most 10)
- [x] Each section's fields are checked: required ones, dates as a year or year and month, lengths, links that are web addresses
- [x] Every add, change, removal, and reordering is a profile event: the actor, on whose behalf when it was someone acting for the person, the entry before and after, and when
- [x] API: read a person's profile (everyone signed in), and add, change, remove, and reorder entries (the person, a curator for them, an admin), with the history
- [x] Positions from the group directory stay as they are, shown beside the positions a researcher adds; a researcher adds what Tessera cannot know (earlier positions elsewhere, joint and adjunct appointments)
- [x] The demo gives a dozen people filled-in profiles (invented education, honors, service, talks) so every new screen has something to show

**Acceptance:** a researcher adds, edits, reorders, and removes entries on their own profile, and the history shows each change; a viewer and another researcher cannot change it; a featured publication must be one of the person's confirmed papers; an entry with a source identifier is found again by that identifier, not duplicated.

## Slice 2: The profile page ☑

**Goal:** a profile that reads well for everyone and edits in place for those who keep it (PRF-01, PRF-02).

- [x] A profile opens on **About**: the overview and interests, then positions (the group directory's first, then the researcher's own in their order), education, honors, service, teaching, talks, links, and featured publications
- [x] Those who keep the profile edit each section in place: add, edit, reorder, remove, with a link to the history; everyone else sees the same page without the controls
- [x] Empty sections are hidden from readers and offered to those who keep the profile
- [x] The **History** tab includes profile changes beside curation decisions
- [x] Wave 5's summary card (when the assistant is on) and the opportunities that fit them keep their places; phones and print get a clean layout

**Acceptance:** a viewer sees a complete, read-only profile; the person sees the same page with editing controls; a screen reader announces each section and control; the app shell stays within its budget.

## Slice 3: Someone to act for them ☑

**Goal:** a researcher names an account that keeps their profile for them (PRF-05).

- [x] A researcher (or a curator for them, or an admin) names one or more accounts that may keep their profile, CVs, and biosketches, and removes them again (migration 0020)
- [x] Someone acting for a person may change their profile sections, public choices, CVs, and biosketches, but not their authorship decisions, identifiers, or anything else of theirs
- [x] Every change made on someone's behalf records both the account that made it and the person it was for; the profile shows who keeps it
- [x] An account sees the profiles it keeps for others in its own menu

**Acceptance:** an assistant named by a researcher edits the researcher's profile and the history says "by the assistant, for the researcher"; the same assistant cannot confirm the researcher's papers; removing them takes effect at once.

## Slice 4: Public by choice ☑

**Goal:** the researcher decides whether their profile is public and what it shows there (PRF-03).

- [x] The researcher, or someone acting for them, turns the public profile on and off (curators and admins still can)
- [x] Public choices (migration 0021): which sections appear publicly (overview, interests, positions, education, honors, service, teaching, talks, links, featured publications, publications, grants, studies, topics), and, within a section, which entries; new entries follow the section's choice
- [x] The public site's person page shows the chosen sections, in the profile's order, from the allowlist; nothing that is not chosen, and never contact details
- [x] **Preview the public page** from the profile shows exactly what `tessera publish` will build

**Acceptance:** an entry hidden from the public stays on the internal profile and is absent from the built public site; turning the public profile off removes the page at the next publish; the public site's checks (accessibility, links, contrast) pass with every section shown.

## Slice 5: CVs ☑

**Goal:** CVs built from the profile, kept as named versions (PRF-04).

- [x] CV documents (migration 0022): a person's named versions ("Promotion dossier", "Visiting scholar"), each with its sections, their order, the entries chosen in each, and options (publications: all confirmed, chosen, or since a year; numbered or not; the person's name in bold)
- [x] Sections from the profile and from the records: positions, education, honors, service, teaching, talks, publications (confirmed only), grants (as investigator), and studies (as official)
- [x] An editor with a live preview: reorder sections and entries (with up and down buttons), choose entries, copy a version, rename, delete
- [x] **Print or save as PDF** through a print layout, and **Download Markdown**
- [x] A CV is seen by its person, those acting for them, their curators, and admins

**Acceptance:** two versions of one person's CV differ in sections and order and both survive a restart; adding a confirmed paper to the profile appears in a CV that lists all confirmed papers, not in one that lists chosen papers; the printed PDF has no app chrome; the Markdown opens cleanly in a word processor.

## Slice 6: NIH biosketches ☑

**Goal:** a biosketch in NIH's current form, prepared for SciENcv (PRF-04).

- [x] A biosketch is a CV document of its own kind, built for the Common Form and Supplement (the form's version stored with it)
- [x] Common Form: name, position title, and organization; the ORCID iD (flagged when the profile has none); professional preparation from education entries; appointments and positions from positions; products, up to 5 closely related to the project and up to 5 other significant, chosen from confirmed papers or entered by hand
- [x] Supplement: personal statement, honors (at most 15, chosen from the profile), and up to 5 contributions to science, each able to name the products it rests on
- [x] Limits checked as one types: characters left, counts, and citations flagged where NIH allows none (a PMID or DOI in a narrative)
- [x] Output in NIH's order: a print layout, Markdown, and **Copy for SciENcv**, each section as plain text ready to paste, with the products as citations; a note that NIH requires the investigator to complete and certify the forms in SciENcv
- [x] With the assistant on, **Draft with the assistant** writes a first personal statement or contribution to science from papers the researcher chooses, within the limit and without citations, shown first in a drawer; **Use this draft** puts it in the editor for the researcher to edit; with it off, nothing mentions it

**Acceptance:** a biosketch with a 3,600-character personal statement, 16 honors, or six contributions says what is over and by how much; a product must be a confirmed paper or entered by hand; Copy for SciENcv gives each section separately; with the assistant on, a drafted contribution stays a draft until the researcher saves it.

## Slice 7: Release prep ◐

- [x] A guide, *Profiles*, covering sections, who keeps a profile, public choices, CVs, and biosketches (with what NIH requires); updates to *Curating*, *The public site*, *The assistant*, and *Configuration*
- [x] Screenshots from the demo: a profile's About, editing a section, public choices, a CV editor, a biosketch with its checks
- [x] Evaluation and checks: access tests for every role, someone acting for another, and the public site; dependency and license review; the private-terms check over the whole repository
- [x] `CHANGELOG.md` for 0.6.0; version bump; the documentation site's home page
- [ ] PR, merge, tag `v0.6.0`, release, and the documentation site published, on the maintainer's go-ahead

**Acceptance:** the full suite passes on SQLite and PostgreSQL 16; migrations 0019 to 0022 run up and down on both; the ORCID works-list check on the pilot gives the same result as 0.5.0 against the same lists; no dependencies were added; a scan of every change since 0.5.0 for pilot names is clean.

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-08 | Slice 7 ready. A guide, *Profiles*, covers the sections, who keeps a profile and someone acting for them, public choices, CVs, and NIH biosketches with what NIH requires; *Curating*, *The public site*, *The assistant*, *Running Tessera*, and *Getting started* point to it. The demo now begins a CV and a biosketch for the lab head with the most confirmed papers, the biosketch's second contribution still citing a paper the way a manuscript would, so its checks have something to say. New screenshots: a profile's About, changing an entry, public choices, a public profile, a CV, a biosketch, and (with the assistant) a drafted personal statement; the existing profile shot keeps its publications. Screenshots showed one title held in three groups as three identical lines in the editor, so the editor's lists now name each entry's group and place, less the organization's own name. Checks: the full suite passes (825 tests on SQLite); ruff, the JavaScript tests, and the private-terms check are clean; PostgreSQL 16 passes the ledger and operations tests, migrations 0019 to 0022 included, up and down; the ORCID works-list check on the pilot gives 0.5.0's result exactly (196 listed, 94.9% found, 97.3% strong); on a copy of the pilot with strong suggestions accepted, CVs and biosketches for the 40 people with the most papers were built from 1,590 confirmed papers without error, the slowest in 476 ms; no dependencies changed; no pilot name appears in any change since 0.5.0. |
| 2026-11-08 | Slice 6 done. A biosketch is a CV document of its own kind, so it needed no migration; its spec holds what the researcher writes and chooses, and the form is stored with it (`nih-2026-01`). NIH's limits are reported rather than refused, so a draft can be saved while it is still too long, and each check says what is over and by how much. A narrative is checked for what looks like a citation (a PubMed or PMC ID, a DOI, an "et al." reference, a numbered reference), with no false alarm on ordinary numbers and years. Dates follow NIH's MM/YYYY. The default position title is a real title from the group directory ("Associate Professor") rather than a group role ("Lead"). Copy for SciENcv gives each section as plain text in SciENcv's order, since the certified PDF can only come from SciENcv. With the assistant on, a first draft is written from the chosen confirmed papers, read through the API as the person asking; on the demo, `gemma4:31b-cloud` wrote a 1,665-character first-person statement with no citations in a few seconds. The draft goes to a drawer, never straight into the biosketch. |
| 2026-11-08 | Slice 5 done. A CV stores only its spec (sections, order, chosen entries, options) and is built from the profile and confirmed records whenever it is shown, so it never goes stale; a section set to all entries picks up new ones, and a chosen list does not. Positions include the group directory's, with the organization's name. Publications take their full author lists and volume, issue, and pages from the lake, and are cited close to NLM's style, the person's own author position in bold (from the confirmed authorship's position). Grants show the person's role (PI, contact PI, or MPI) from RePORTER's investigators; studies their official role. Printing hides the editor and the app around the CV; the Markdown download is the same document. Reordering uses up and down buttons rather than dragging, as on the profile, for keyboards and screen readers. The public site's person page now formats entries with the same shared code. |
| 2026-11-08 | Slice 4 done. The public choices store the sections left off rather than those shown, so a section added in a later version appears on a public page unless the person hides it, as their own sections do when first filled in. The public switch moved from curators only to everyone who keeps the profile, someone acting for the person included; each change is in the profile's history. The preview is the public page built by the same code as `tessera publish`, for that person alone (public or not yet), with the site's stylesheet served beside it, so it shows exactly what the next publish will. |
| 2026-11-08 | Slice 3 done. Someone acting for a person is a fourth capacity for keeping a profile, beside the person, their curators, and admins; it grants nothing else, since every other decision about a person goes through the curation scope, which a delegate does not enter. Only the person, their curators, and admins name or remove delegates, and both are recorded in the profile's history. Everyone sees the names of those who keep a profile; only its keepers see their usernames. The account is chosen by username, so no list of accounts is exposed to everyone. Without accounts (single-operator mode) there is nobody to name, and the card is not shown. |
| 2026-11-08 | Slice 2 done. The About tab is a module loaded when the tab first opens, like impact threads, so the app's first load is unchanged. Positions from the group directory come first, marked as such, and the researcher's own follow in their own order rather than being merged by date, so a reordering is never undone by a sort. Entries are edited in a drawer built from the section's fields, with the server's message shown in the form when a check fails; entries move with up and down buttons (keyboard and screen-reader friendly) rather than dragging. On phones each entry's buttons move below it. People outside the directory have no About tab and open on their papers. |
| 2026-11-08 | Slice 1 done. Entries keep their fields as JSON checked by each section's rules in code, rather than a table per section, so a section can gain a field without a migration and every section shares one history. Dates are a year or a year and month (a talk may give the day); an entry cannot end before it starts. New entries go first in dated sections and last in links and featured papers; the researcher can reorder any section. Featured papers must be the person's confirmed papers; one later rejected stays on the profile for its keepers, marked, and disappears for everyone else. Someone keeps a profile as themselves (their account is linked to the person, whatever their role), as a curator whose scope covers the person, or as an admin. An import upserts by its source and its key for the entry and can never match an entry made by hand. The demo fills in the profiles of the twelve lab heads with the most confirmed papers. |
| 2026-11-08 | Wave 6 planned. NIH's biosketch was checked against NOT-OD-26-018: since January 25, 2026 it is the Biographical Sketch Common Form and the NIH Biographical Sketch Supplement, completed and certified in SciENcv by the investigator, so Tessera prepares the content in that form, checks NIH's limits, and gives it ready to paste, rather than producing the certified PDF. Positions from the group directory stay where they are kept and are shown beside the positions a researcher adds, rather than copied into profile entries. |
