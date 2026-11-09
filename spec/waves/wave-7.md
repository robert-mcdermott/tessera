# Wave 7: Adoption (v0.7.0)

**Goal:** make Tessera easy for another organization to adopt and run: on PostgreSQL, behind its own single sign-on, in its own colors and words, as a service on its own server, at the size of a large research institution, accessible, reviewed for security, and with a guide that takes an adopter from nothing to a curated, published installation.

**Branch:** `wave-7` · **Target version:** `0.7.0` · **Status:** ☑ released as 0.7.0 (2026-11-09)

## Exit criteria

The wave is done when, on a fresh checkout:

1. The full test suite passes on SQLite and on PostgreSQL 16, every test on each, with one command for both (PLT-09).
2. With single sign-on configured, people sign in through a standards-based OpenID Connect provider, using the authorization-code flow with PKCE, checked end to end against a real provider running locally; accounts are matched or created at first sign-in; the provider's app roles or groups, when mapped, set Tessera roles and group scopes at every sign-in; local accounts keep working, and a local admin can always sign in. The guide covers Microsoft Entra ID, Okta, Google, and Keycloak (PLT-10).
3. An admin chooses the organization's look in the app, with a live preview, contrast checked in both modes, and the result written as configuration; vocabulary presets name groups and roles for common kinds of organization (PLT-05, PLT-11, PLT-13).
4. Tessera runs as a service from reference files: a container image definition, systemd units for the server, the sync, and the backup, and reverse-proxy examples, with health and readiness checks.
5. On a simulated organization of 10,000 people and 1,000,000 publications, the sync's local steps, the derived builds, and the app's pages are measured, the budgets below are met or the misses are recorded with what would fix them, and the benchmark is a command an adopter can run on their own server (PLT-14).
6. The app passes a WCAG 2.2 AA audit, and a security review's findings are fixed or recorded (UX-06, PLT-15).
7. A guide, *Adopting Tessera*, takes a new organization from installation to a curated, published site.

Requirements: PLT-09, PLT-10, PLT-14, PLT-15, UX-06; PLT-05, PLT-11, and PLT-13 extended.

## Decisions from the maintainer

- **Sign-in** (2026-11-08, before wave 4): local accounts stay the default, and may be all a small organization needs. Single sign-on is optional, through OpenID Connect with any standards-based provider (Microsoft Entra ID is the example). Roles may optionally map from the provider's groups.
- **Scale** (2026-11-08): the 10,000 people and 1,000,000 publications are simulated, not downloaded. Benchmarks run on the maintainer's laptop (a 10-core M4 with an SSD) to show how Tessera performs and scales; production runs on a different server, so the benchmark must be easy to rerun there.

- **Incremental resolve** (2026-11-08): the nightly resolve recomputes every suggestion, so at 1,000,000 publications it misses its budget. The miss is recorded in slice 3, with what was made faster; resolving only what changed is its own slice later (under v1.0.0 in the roadmap), with its own design and tests, rather than part of this wave. On 2026-11-09, after a load test with 20 simulated users, the maintainer moved it past 1.0, to *Later, for much larger organizations*, and put three smaller fixes in v1.0.0 instead: each Insight figure built once, several server processes, and a load test in `tessera bench`.

### Defaults taken (either can change)

- **OpenID Connect only.** SAML 2.0 is left out unless an adopter's identity team requires it; every provider named supports OpenID Connect for new applications.
- **App roles over groups for Entra ID.** Entra leaves groups out of a token when someone belongs to more than about 200, so the guide recommends app roles, and group mapping stays available for other providers.
- **No new runtime dependencies.** PKCE, the ID token's checks, and RS256 signatures are written with the standard library (RSA verification is one modular exponentiation), and HTTP uses the client Tessera already has.
- **Configuration stays the source of truth.** The theme builder writes the `[branding]` section of the configuration file; it does not keep a second copy in the ledger.
- **The container image is defined, not published.** The repository has the image's definition and how to build it; publishing an image to a registry waits for the maintainer.

## Budgets at 10,000 people and 1,000,000 publications

| Measure | Budget | Measured: 1,000 people, 100,000 papers | Measured: 10,000 people, 1,000,000 papers |
|---|---|---|---|
| A profile (p95) | < 300 ms | 64 ms | 303 ms; 108 ms median ✗ |
| Search, people and publications (p95) | < 200 ms | 129 to 195 ms | a name: 30 ms; people: 174 ms; papers with a topic word: 568 ms median, 2.3 s the first time ✗ |
| Directory and record lists with facets (p95) | < 300 ms | 2 to 8 ms | 3 to 71 ms |
| The review queue's next item (p95) | < 100 ms | 44 ms | 588 ms ✗ |
| An Insight figure for the organization (p95) | < 1 s | 2 ms; the first after a change, 0.5 to 1.1 s | 3 ms; the first after a change, 7 to 21 s ✗ |
| Resolving one day's new papers (about 300) | < 1 min | 45 s | 15 min ✗ |
| Rebuilding the search index | < 15 min | 25 s | 12 min |
| Rebuilding the concept profiles, similar people, and the graph | < 30 min | 19 s | 8 min |
| Memory of the server process | < 2 GB | 0.55 GB | 1.97 GB |

Measured with `tessera bench` on the laptop (a 10-core M4 with 32 GB and an SSD; SQLite), 20 samples of each request. ✗ is a miss; each is recorded in the log with its cause and what would fix it.

---

## Slice 1: Both databases ☑

**Goal:** every test runs on PostgreSQL as well as SQLite, and passes on both (PLT-09).

- [x] A switch that runs the whole suite against PostgreSQL: each installation a test creates gets a fresh PostgreSQL database instead of a SQLite file
- [x] Whatever behaves differently on PostgreSQL is fixed in the code, not skipped in the tests (types, ordering, case, JSON, locking, identifiers)
- [x] One command runs the suite on both, starting and removing a disposable PostgreSQL
- [x] *Running Tessera* says when to choose PostgreSQL and how to move an installation from SQLite

**Acceptance:** the full suite passes on SQLite and on PostgreSQL 16, with no test skipped on either except those that are about the other database.

## Slice 2: Single sign-on ☑

**Goal:** optional OpenID Connect sign-in with any standards-based provider, beside local accounts (PLT-10).

- [x] `[auth.oidc]` in the configuration: the provider's issuer, the client ID, the client secret's environment variable, the scopes, the button's label, and the claims to read; the provider's settings found through its discovery document
- [x] Sign-in through the authorization-code flow with PKCE, a state value, and a nonce; the ID token checked (signature against the provider's published keys, issuer, audience, expiry, nonce) before anyone is signed in
- [x] Accounts linked to the provider by its subject identifier; at first sign-in, matched to an existing account by a verified email address or created with a default role, or refused when creation is turned off
- [x] An optional mapping from the provider's app roles or groups to Tessera roles and group scopes, applied at every sign-in, so a change at the provider takes effect at the next sign-in
- [x] Local sign-in stays available for accounts that have a password, so a local admin can always get in; single sign-on can be the only choice shown to everyone else
- [x] **Admin → Accounts** shows how each account signs in and when it last did; `tessera doctor` checks the provider's discovery document and keys
- [x] A decision record for single sign-on; the sign-in guide with Microsoft Entra ID, Okta, Google, and Keycloak

**Acceptance:** tests sign in through a scripted provider and refuse a bad signature, a wrong audience, an expired token, a replayed state, and a wrong nonce; a sign-in through a Keycloak running locally creates an account with the mapped role, and a role removed at the provider is removed at the next sign-in; a local admin signs in with single sign-on turned on.

## Slice 3: At scale ☑

**Goal:** know how Tessera performs at 10,000 people and 1,000,000 publications, and make it meet the budgets (PLT-14).

- [x] A simulated organization of any size, shaped like a real one (papers per person, authors per paper, MeSH headings, grants, studies, groups), written straight to the lake and ledger with no network
- [x] A benchmark command that times the sync's local steps, the derived builds, and the app's main pages and reports p50 and p95 with the machine it ran on
- [x] The slow parts found and fixed; the results at 10,000 and 1,000,000 recorded beside smaller sizes, so the scaling is visible
- [x] *Running Tessera* gains sizing advice: memory, disk, and how long a first sync and a nightly sync take

**Acceptance:** the budgets above are met on the laptop, or each miss is recorded with its cause and what would fix it; the benchmark runs on another machine with one command.

## Slice 4: The organization's look and words ☑

**Goal:** an adopter makes Tessera theirs without editing files by hand (PLT-05, PLT-11, PLT-13).

- [x] **Admin → Branding**: the product name, palette (day and night), accent, logos, favicon, and footer links, with a live preview of the app and the public site and every color pair's contrast shown as it changes; saving writes the `[branding]` section of the configuration file and refuses pairs below WCAG AA
- [x] Guidance for logos: sizes, formats, clear space, and light and dark versions
- [x] Vocabulary presets for common kinds of organization (an academic medical center, a university, a research institute), chosen in the configuration and adjusted from there

**Acceptance:** an admin changes the palette and logo in the app, sees both modes before saving, cannot save a pair below AA, and the saved configuration reloads to the same look; a preset changes the group kinds and roles the app shows.

## Slice 5: Running it as a service ☑

**Goal:** reference files for running Tessera on a server.

- [x] A container image definition (built with `uv`, running as an unprivileged user, the data home a volume) and a compose example with PostgreSQL
- [x] systemd units for the server and timers for the sync and the backup, with sandboxing options
- [x] Reverse-proxy examples for Caddy and nginx with HTTPS and the headers Tessera expects
- [x] Health and readiness endpoints for load balancers and container checks; `tessera doctor` reports what a production installation needs

**Acceptance:** the image builds and serves the demo; the compose example runs Tessera on PostgreSQL; the units pass `systemd-analyze verify`.

## Slice 6: Accessible and reviewed ☑

**Goal:** a WCAG 2.2 AA audit and a security review, with what they find fixed (UX-06, PLT-15).

- [x] An automated pass over every page in both themes: names for every control, landmarks and headings, focus order and visible focus, focus not hidden behind sticky bars, targets of at least 24 by 24 pixels, contrast of rendered text, and no keyboard traps; the pass runs with the documentation's screenshots
- [x] A manual pass with a screen reader and keyboard only over the main tasks: curating, searching, editing a group page, keeping a profile, building a CV
- [x] A security review: a threat model; authentication, sessions, CSRF, access control, input handling, outbound requests, files, headers, logging, and dependencies, against OWASP's Application Security Verification Standard at level 1
- [x] Every finding fixed, or recorded with its reason; `SECURITY.md` says how to report a vulnerability

**Acceptance:** the automated pass reports nothing; the manual findings are fixed; the review's findings are fixed or recorded.

## Slice 7: The adopter's guide ☑

**Goal:** a guide that takes a new organization from nothing to a curated, published installation.

- [x] *Adopting Tessera*: what to decide first (where it runs, SQLite or PostgreSQL, accounts or single sign-on), installing, describing the organization, the first sync and how long it takes, a curation playbook for the first weeks, groups and their managers, the public site, backups, upgrades, and getting help
- [x] A fresh-clone run through the guide on a clean machine, fixing whatever it trips on

**Acceptance:** following the guide from a fresh clone gives a working, signed-in, curated, published installation of the demo organization without other help.

## Slice 8: Release prep ☑

- [x] Guides, screenshots, the documentation site's home page, `CHANGELOG.md` for 0.7.0, and the version bump
- [x] Checks: the full suite on SQLite and PostgreSQL 16; migrations up and down on both; the pilot's ORCID works-list check; dependency and license review; the private-terms check; a pilot-name scan of every change since 0.6.0
- [x] PR, merge, tag `v0.7.0`, release, and the documentation site published, on the maintainer's go-ahead

**Acceptance:** everything above passes, and the release is published on the maintainer's go-ahead.

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-09 | Slice 8 done; released as 0.7.0 on the maintainer's go-ahead, given before the slice began, to work through the release unattended. Every screenshot was taken again with the audit running (92 scenes by day and after dark; it found nothing), the documentation site's home page gained *In your colors and words* and four pillars for adopters, and the guides' index and menu list *Adopting Tessera* and *Look and words*. Checks: the full suite passes on SQLite (913) and PostgreSQL 16 (914); migrations 0024 and 0023 were run down to 0022 and back up on a copy of the pilot's ledger in SQLite, and in PostgreSQL 16 after moving it there by backup and restore, every row of 41 tables kept; the pilot, re-resolved with this release, gives the ORCID works-list check's result of 0.5.0 and 0.6.0 exactly (196 listed, 94.9% found, 97.3% strong), so the speed-ups changed no decision; no dependency was added or changed (`uv.lock` differs only in Tessera's own version), and the images the service files name (Python, uv, PostgreSQL, Caddy, nginx) are referenced, not shipped; the private-terms check and a scan of every change and commit message since 0.6.0 for the pilot's people are clean. |
| 2026-11-09 | Slice 7 done. *Adopting Tessera* puts an organization's steps in order and links to the guides that hold the details: a rehearsal on the demo organization, what to decide first (where it runs, the ledger, the size of the server, sign-in, who curates, a public site), installing, describing the organization and making it its own, people and groups, the first sync and how long it takes, a plan for the first weeks of curation, pages and profiles, the public site, keeping it running, and getting help. It is linked from the documentation's index, its menu, and *Getting started*. The run-through: a fresh clone of the pushed branch into a new folder with its own environment, then the rehearsal exactly as written: the demo built, accounts turned on, an admin added, the app served, two decisions made as that admin and logged under that name, a profile made public and previewed, and the site published with that person's page in it. It tripped once: another program held port 8000, and `tessera serve` printed that it was running before failing to listen; it now checks the port first, says that something else holds it, and suggests another, and the guide says the same. (This machine, not a clean one; the clone and its environment were new.) |
| 2026-11-09 | Slice 6 done. **The automated pass** (`docs/capture/audit.mjs`, run by the screenshot script with `--audit`) checks every scene in both themes: accessible names from Chrome's own accessibility tree; one main landmark, a level-one heading (unless a modal is open), no skipped levels, a language, a title, and image alternatives; the contrast of rendered text, its colors resolved by the browser's own canvas, so oklch and color-mix count as drawn; targets of 24 by 24 pixels or the spacing exception; and a walk with the Tab key that checks focus changes how a control looks, is not under the sticky bars, and moves on. Planted faults of each kind were all caught. The first pass found 166 findings in 6 scenes, most from one cause: with the command palette open, Tab left it for the page behind. Fixed: the palette makes the page inert while open; the sidebar had no overflow, so on a short screen its Admin items could be neither seen nor reached, by keyboard or mouse; topic chips' remove buttons were faded below 4.5:1; research-area links were 20 pixels high with neighbors close; the biosketch's statement and the palette's results had no names; branding's file inputs were hidden while taking focus. The pass now finds nothing. **The manual pass**, with the keyboard alone and the accessibility tree a screen reader reads, went through curating (decisions by key, announced, focus kept), searching (the palette, its results, and focus on the new page's heading), keeping a profile (a named modal editor that keeps focus and returns it), building a CV, and editing a group page, where it found buttons that said only *Move up* or *Remove this block*, nothing announced, and focus lost on removal: now each names its block, moves and removals are announced, and focus stays in the list. Five buttons that said only *Add* now say what they add. A pass with a screen reader itself (VoiceOver or NVDA) by a person is still worth doing before 1.0. **The security review** (`spec/security.md`): a threat model by asset, actor, and entry point, and ASVS 4.0.3 level 1 chapter by chapter. Already sound: deny-by-default routes, scrypt, throttling, session tokens and cookies, CSRF tokens with origin checks, no HTML built from strings anywhere in the app, escaping on the public site, parameterized SQL, safe archive extraction, outbound requests only to configured hosts, strict security headers; `pip-audit` of the 76 locked runtime packages found no known vulnerability. Fixed: the public-page preview, served from Tessera's origin, now allows no scripts; the nginx example limits sign-in attempts by address. Recorded with what would close them: no breached-password list (2.1.7), no message when a password changes (2.2.3), and passwords alone for local admins (4.3.1), each met by using single sign-on; and two the reverse proxy covers (request body size, and sign-in counting by address). GitHub's private vulnerability reporting, which `SECURITY.md` points to first, is off on the repository; turning it on is the maintainer's. |
| 2026-11-09 | Slice 5 done. The reference files are in `deploy/`. The image (457 MB, Python 3.13 slim, uv 0.13.1) installs exactly the locked versions, runs as uid 10001, and keeps no state: `/srv/tessera/config` and `/srv/tessera/data` are volumes, and the data home is set by the environment (`TESSERA_DATA_HOME`). Checked: built with podman, it served the demo with accounts on (sign-in, people, and a refused read without a session). The compose file ran it on PostgreSQL 16, with the demo moved from SQLite by `tessera backup` and `tessera restore`, which also exercises the move *Running Tessera* describes. The units passed `systemd-analyze verify` (systemd 252, Debian 12), and `systemd-analyze security` rated the server 1.9; `MemoryDenyWriteExecute` and `ProcSubset` are left out, since DuckDB and Python libraries may need what they take away. The Caddy and nginx files passed `caddy validate` and `nginx -t`. The proxy must send `X-Forwarded-Proto` and `X-Forwarded-Host`, which Tessera reads for Secure cookies, its origin check, and the single sign-on address. Backups run at 00:30 and syncs at 02:30, since a second data job stops at once; old archives are kept unless the commented line that keeps 30 days is turned on. Podman drops an image's health check in its default format, so the guide builds with `--format docker`. |
| 2026-11-09 | Slice 4 done. **Admin → Branding** edits everything in `[branding]` beside a preview of the app and the public site, by day and after dark, drawn with CSS variables (the app's policy allows no inline styles). Its colors and contrast come from the server, from the same function the app and `tessera doctor` use, so the editor can never pass a palette the app would draw differently. Saving rewrites only the branding tables of `tessera.toml`: the result must parse to exactly the intended branding with every other setting unchanged, and must load, or nothing is written; the file before is kept as `.bak`, and comments inside the branding tables are not kept (the section says so). The running app takes the new branding at once and nothing else from the file. Images are checked by their first bytes, SVGs refused with scripts, event handlers, embedded documents, or outside references, kept under names drawn from their content, and served with a policy that runs nothing. Presets for an academic medical center, a university, and a research institute share keys wherever they mean the same, so changing preset keeps every group's kind and member's role. Tested end to end in the demo: an accent too light for links was refused, and a new name and accent saved, reloaded, and passed `tessera doctor`. |
| 2026-11-09 | Slice 3 done. `tessera bench` simulates an organization of any size straight into the lake and ledger: surnames that repeat as real ones do, labs, grants, studies, MeSH headings, and about 3.4 authorship candidates a paper. It times a first sync's local steps, a nightly one, and the app's main requests, with each step's peak memory, and saves the report as JSON. On the laptop at 1,000 people and 100,000 papers, everything meets its budget except the first Insight figure after a change (1.1 s), and a nightly sync's local work takes 1 min 40 s. At 10,000 people and 1,000,000 papers, a first sync's local work takes about 45 min and a nightly one 36 min; the ledger is 8.2 GB, a sync's peak memory 16 GB, and the server's 1.98 GB. Fixed on the way: similar people ran out of memory at a million papers (every pair sharing a concept was built, each with its explanation, before most were dropped; now a batch at a time, explained only for the pairs kept, with the same pairs and explanations); search joined every match to its document to check its kind, ran the full-text query again for the count, the page, and the facets, and marked a partial word by merging every word that begins so (now each document's kind is a word in the index that no text contains, the matches are found once into a temporary table, facet counts are kept per search, and partial words are marked only in indexes up to 200,000 documents); the review queue loaded and sorted every open suggestion (now counted in SQL and only the page read, in the same order); a group's page loaded every membership in the organization; and migration 0024's queue index made SQLite, which has no statistics, prefer it to the record index for a grant's thread, so the record index carries the status too. Misses, each with its cause and what would fix it: **the nightly resolve** recomputes every suggestion (15 min; resolve only what changed, a later slice under v1.0.0, the maintainer's choice); **the first Insight figure after a change** gathers 2.6 million links through Python rows (7 to 21 s; keep the inputs columnar and computed in DuckDB, or have the nightly build write a snapshot with the day's decisions applied on top); **a topic word in a quarter of the papers** is ranked by scoring every match, which FTS5 cannot prune, and its facets are counted over all of them (about half a second, up to 2.3 s while its facets are first counted, and a second to search everything; a name takes 30 ms; facet counts from a columnar copy of the facets held in memory would take most of it); **the review queue** counts 450,000 open suggestions on each request (0.59 s; counts kept by triggers); **a profile** of the most prolific simulated people, with up to 7,000 papers, takes up to half a second the first time. Noted for later: the ledger is the largest part (8.2 GB at a million papers, against a 0.4 GB lake), because it keeps every link with its evidence (1.4 KB of JSON for an authorship, uncompressed); storing each feature's code and numbers, with the explanation written when shown, would make it several times smaller. The simulation's text comes from templates, so its lake (0.4 GB at a million papers) and search index are smaller than real ones; *Running Tessera* says to allow three times the disk. |
| 2026-11-08 | Slice 2 done. Single sign-on is OpenID Connect's authorization-code flow with PKCE (S256), a state, and a nonce; the attempt is kept in the ledger by a hash of its state (migration 0023), used once, and bound to the browser by a Lax cookie, since the provider's redirect back is cross-site. The ID token's RS256 signature is verified with the standard library (one modular exponentiation and a comparison with the PKCS #1 v1.5 encoding), checked against a signature OpenSSL made, and a key the provider rotates is read again. Roles from the provider are stored as such and replaced at each sign-in, so an admin's own grants are untouched and the edit form shows the provider's as read-only. Accounts made at sign-in have the password hash `!`, which matches nothing. 28 tests sign in through a scripted provider that checks PKCE and the client secret as a real one does, and refuse eight kinds of forged or wrong token. End to end against Keycloak 26 in a container: the first sign-in created an account with the Curator role from Keycloak's realm roles, and after Keycloak swapped it for Admin, the next sign-in (with no prompt, through Keycloak's own session) took Curator away and gave Admin; a local admin signed in with a password throughout. |
| 2026-11-08 | Slice 1 done. With `TESSERA_TEST_LEDGER=postgres`, each data home a test creates gets its own empty PostgreSQL database (a migration takes 0.12 s there, so no template is needed and every test means exactly what it does on SQLite); the switch lives in the tests alone and changes nothing in Tessera. The whole suite then failed three tests: two were about the default SQLite file (one now says so and is skipped on PostgreSQL; the other checks the file only on SQLite), and one was a real difference: the review queue's ties came back in storage order, which on PostgreSQL changes after a restore. Assertions are now read in ID order, and the queue's sort ends with the ID. `scripts/test-both.sh` runs both: 827 tests on SQLite in 3.6 minutes and 828 on PostgreSQL 16 in 11 minutes, on the laptop. |
| 2026-11-08 | Wave 7 planned. Single sign-on is OpenID Connect only, with the authorization-code flow and PKCE, the ID token's signature checked against the provider's keys with the standard library, and app roles recommended for Entra ID because of its group limit. The scale target is simulated; the budgets extend the ones in [ux.md](../ux.md) from 5,000 people and 300,000 publications to 10,000 and 1,000,000, and add the sync's and builds' times. |
