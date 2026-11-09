# Wave 7: Adoption (v0.7.0)

**Goal:** make Tessera easy for another organization to adopt and run: on PostgreSQL, behind its own single sign-on, in its own colors and words, as a service on its own server, at the size of a large research institution, accessible, reviewed for security, and with a guide that takes an adopter from nothing to a curated, published installation.

**Branch:** `wave-7` · **Target version:** `0.7.0` · **Status:** ◐ slice 1 done

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

### Defaults taken (either can change)

- **OpenID Connect only.** SAML 2.0 is left out unless an adopter's identity team requires it; every provider named supports OpenID Connect for new applications.
- **App roles over groups for Entra ID.** Entra leaves groups out of a token when someone belongs to more than about 200, so the guide recommends app roles, and group mapping stays available for other providers.
- **No new runtime dependencies.** PKCE, the ID token's checks, and RS256 signatures are written with the standard library (RSA verification is one modular exponentiation), and HTTP uses the client Tessera already has.
- **Configuration stays the source of truth.** The theme builder writes the `[branding]` section of the configuration file; it does not keep a second copy in the ledger.
- **The container image is defined, not published.** The repository has the image's definition and how to build it; publishing an image to a registry waits for the maintainer.

## Budgets at 10,000 people and 1,000,000 publications

| Measure | Budget |
|---|---|
| A profile (p95) | < 300 ms |
| Search, people and publications (p95) | < 200 ms |
| Directory and record lists with facets (p95) | < 300 ms |
| The review queue's next item (p95) | < 100 ms |
| An Insight figure for the organization (p95) | < 1 s |
| Resolving one day's new papers (about 300) | < 1 min |
| Rebuilding the search index | < 15 min |
| Rebuilding the concept profiles, similar people, and the graph | < 30 min |
| Memory of the server process | < 2 GB |

---

## Slice 1: Both databases ☑

**Goal:** every test runs on PostgreSQL as well as SQLite, and passes on both (PLT-09).

- [x] A switch that runs the whole suite against PostgreSQL: each installation a test creates gets a fresh PostgreSQL database instead of a SQLite file
- [x] Whatever behaves differently on PostgreSQL is fixed in the code, not skipped in the tests (types, ordering, case, JSON, locking, identifiers)
- [x] One command runs the suite on both, starting and removing a disposable PostgreSQL
- [x] *Running Tessera* says when to choose PostgreSQL and how to move an installation from SQLite

**Acceptance:** the full suite passes on SQLite and on PostgreSQL 16, with no test skipped on either except those that are about the other database.

## Slice 2: Single sign-on ☐

**Goal:** optional OpenID Connect sign-in with any standards-based provider, beside local accounts (PLT-10).

- [ ] `[auth.oidc]` in the configuration: the provider's issuer, the client ID, the client secret's environment variable, the scopes, the button's label, and the claims to read; the provider's settings found through its discovery document
- [ ] Sign-in through the authorization-code flow with PKCE, a state value, and a nonce; the ID token checked (signature against the provider's published keys, issuer, audience, expiry, nonce) before anyone is signed in
- [ ] Accounts linked to the provider by its subject identifier; at first sign-in, matched to an existing account by a verified email address or created with a default role, or refused when creation is turned off
- [ ] An optional mapping from the provider's app roles or groups to Tessera roles and group scopes, applied at every sign-in, so a change at the provider takes effect at the next sign-in
- [ ] Local sign-in stays available for accounts that have a password, so a local admin can always get in; single sign-on can be the only choice shown to everyone else
- [ ] **Admin → Accounts** shows how each account signs in and when it last did; `tessera doctor` checks the provider's discovery document and keys
- [ ] A decision record for single sign-on; the sign-in guide with Microsoft Entra ID, Okta, Google, and Keycloak

**Acceptance:** tests sign in through a scripted provider and refuse a bad signature, a wrong audience, an expired token, a replayed state, and a wrong nonce; a sign-in through a Keycloak running locally creates an account with the mapped role, and a role removed at the provider is removed at the next sign-in; a local admin signs in with single sign-on turned on.

## Slice 3: At scale ☐

**Goal:** know how Tessera performs at 10,000 people and 1,000,000 publications, and make it meet the budgets (PLT-14).

- [ ] A simulated organization of any size, shaped like a real one (papers per person, authors per paper, MeSH headings, grants, studies, groups), written straight to the lake and ledger with no network
- [ ] A benchmark command that times the sync's local steps, the derived builds, and the app's main pages and reports p50 and p95 with the machine it ran on
- [ ] The slow parts found and fixed; the results at 10,000 and 1,000,000 recorded beside smaller sizes, so the scaling is visible
- [ ] *Running Tessera* gains sizing advice: memory, disk, and how long a first sync and a nightly sync take

**Acceptance:** the budgets above are met on the laptop, or each miss is recorded with its cause and what would fix it; the benchmark runs on another machine with one command.

## Slice 4: The organization's look and words ☐

**Goal:** an adopter makes Tessera theirs without editing files by hand (PLT-05, PLT-11, PLT-13).

- [ ] **Admin → Branding**: the product name, palette (day and night), accent, logos, favicon, and footer links, with a live preview of the app and the public site and every color pair's contrast shown as it changes; saving writes the `[branding]` section of the configuration file and refuses pairs below WCAG AA
- [ ] Guidance for logos: sizes, formats, clear space, and light and dark versions
- [ ] Vocabulary presets for common kinds of organization (an academic medical center, a university, a research institute), chosen in the configuration and adjusted from there

**Acceptance:** an admin changes the palette and logo in the app, sees both modes before saving, cannot save a pair below AA, and the saved configuration reloads to the same look; a preset changes the group kinds and roles the app shows.

## Slice 5: Running it as a service ☐

**Goal:** reference files for running Tessera on a server.

- [ ] A container image definition (built with `uv`, running as an unprivileged user, the data home a volume) and a compose example with PostgreSQL
- [ ] systemd units for the server and timers for the sync and the backup, with sandboxing options
- [ ] Reverse-proxy examples for Caddy and nginx with HTTPS and the headers Tessera expects
- [ ] Health and readiness endpoints for load balancers and container checks; `tessera doctor` reports what a production installation needs

**Acceptance:** the image builds and serves the demo; the compose example runs Tessera on PostgreSQL; the units pass `systemd-analyze verify`.

## Slice 6: Accessible and reviewed ☐

**Goal:** a WCAG 2.2 AA audit and a security review, with what they find fixed (UX-06, PLT-15).

- [ ] An automated pass over every page in both themes: names for every control, landmarks and headings, focus order and visible focus, focus not hidden behind sticky bars, targets of at least 24 by 24 pixels, contrast of rendered text, and no keyboard traps; the pass runs with the documentation's screenshots
- [ ] A manual pass with a screen reader and keyboard only over the main tasks: curating, searching, editing a group page, keeping a profile, building a CV
- [ ] A security review: a threat model; authentication, sessions, CSRF, access control, input handling, outbound requests, files, headers, logging, and dependencies, against OWASP's Application Security Verification Standard at level 1
- [ ] Every finding fixed, or recorded with its reason; `SECURITY.md` says how to report a vulnerability

**Acceptance:** the automated pass reports nothing; the manual findings are fixed; the review's findings are fixed or recorded.

## Slice 7: The adopter's guide ☐

**Goal:** a guide that takes a new organization from nothing to a curated, published installation.

- [ ] *Adopting Tessera*: what to decide first (where it runs, SQLite or PostgreSQL, accounts or single sign-on), installing, describing the organization, the first sync and how long it takes, a curation playbook for the first weeks, groups and their managers, the public site, backups, upgrades, and getting help
- [ ] A fresh-clone run through the guide on a clean machine, fixing whatever it trips on

**Acceptance:** following the guide from a fresh clone gives a working, signed-in, curated, published installation of the demo organization without other help.

## Slice 8: Release prep ☐

- [ ] Guides, screenshots, the documentation site's home page, `CHANGELOG.md` for 0.7.0, and the version bump
- [ ] Checks: the full suite on SQLite and PostgreSQL 16; migrations up and down on both; the pilot's ORCID works-list check; dependency and license review; the private-terms check; a pilot-name scan of every change since 0.6.0
- [ ] PR, merge, tag `v0.7.0`, release, and the documentation site published, on the maintainer's go-ahead

**Acceptance:** everything above passes, and the release is published on the maintainer's go-ahead.

---

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-08 | Slice 1 done. With `TESSERA_TEST_LEDGER=postgres`, each data home a test creates gets its own empty PostgreSQL database (a migration takes 0.12 s there, so no template is needed and every test means exactly what it does on SQLite); the switch lives in the tests alone and changes nothing in Tessera. The whole suite then failed three tests: two were about the default SQLite file (one now says so and is skipped on PostgreSQL; the other checks the file only on SQLite), and one was a real difference: the review queue's ties came back in storage order, which on PostgreSQL changes after a restore. Assertions are now read in ID order, and the queue's sort ends with the ID. `scripts/test-both.sh` runs both: 827 tests on SQLite in 3.6 minutes and 828 on PostgreSQL 16 in 11 minutes, on the laptop. |
| 2026-11-08 | Wave 7 planned. Single sign-on is OpenID Connect only, with the authorization-code flow and PKCE, the ID token's signature checked against the provider's keys with the standard library, and app roles recommended for Entra ID because of its group limit. The scale target is simulated; the budgets extend the ones in [ux.md](../ux.md) from 5,000 people and 300,000 publications to 10,000 and 1,000,000, and add the sync's and builds' times. |
