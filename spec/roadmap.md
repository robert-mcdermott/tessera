# Roadmap

Tessera ships in **waves**. Each wave is a release (wave 1 → v0.1.0, wave 2 → v0.2.0, …) and is split into **slices**: coherent chunks of related work. The last slice of every wave prepares the release. See [process.md](process.md) for how this works day to day.

The order is chosen so that every wave ends with something demonstrable using **public data only**, and each wave builds on evidence the previous one collected.

| Wave | Version | Theme | The demo at the end |
|---|---|---|---|
| 1 | v0.1.0 | **First light** | Configure an organization, run one command, browse linked researchers, papers, grants, and studies with evidence, and curate suggestions |
| 2 | v0.2.0 | **Connections** | Find experts and collaborators with reasons; explore the collaboration graph; sign in with local accounts |
| — | — | **Documentation site** | A product site with illustrated guides, screenshots of a fictional demo organization, and an API reference, published from `gh-pages` |
| 3 | v0.3.0 | **Showcase** | Labs maintain living showcase pages through a weekly approval inbox; a public, allowlisted site goes live |
| 4 | v0.4.0 | **Insight** | Leaders explore funding, topic, and study portfolios with transparent metrics, and export reproducible reports |
| 5 | v0.5.0 | **Impact** | Follow grants to their impact; match funding opportunities to teams; optional grounded AI assistant |
| 6 | v0.6.0 | **Profiles** | Every researcher has a full profile (positions, education, honors, service, activities) that they keep, open to everyone inside, public only if they opt in, with CVs and biosketches built from it |
| 7 | v0.7.0 | **Adoption** | Another organization installs, re-brands, and runs Tessera on PostgreSQL with single sign-on |
| — | v1.0.0 | **Stable** | Hardened, audited, documented, with API and upgrade guarantees |

Status key: ☐ planned · ◐ in progress · ☑ done

---

## Wave 1: First light (v0.1.0) ☑

**Goal:** prove that a useful, trustworthy research graph can be built from public data alone, with evidence on every link.

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | Foundation | Project skeleton, config, data home, ledger and migrations, lake writer and reader, CLI and API skeletons, tooling | ☑ |
| 2 | Connectors | Shared HTTP client; PubMed, iCite, RePORTER, and ClinicalTrials.gov connectors into the raw zone and lake; run records; incremental checkpoints | ☑ |
| 3 | Evidence engine v1 | People bootstrap, name handling, candidate retrieval, `authorship/v1` scoring, source-reported links, assertions with evidence, `tessera sync` | ☑ |
| 4 | API and search | `/api/v1` for people, publications, grants, studies, assertions, and sources; FTS5 search with facets | ☑ |
| 5 | Web app and design system | Vanilla JS shell, tokens, light/dark themes, branding, command palette; Home, Search, People, profile, and detail pages; source health | ☑ |
| 6 | Curation | Review queue with evidence drawer, keyboard flow, bulk within band, undo, manual additions, discovered-people queue | ☑ |
| 7 | Release prep | Docs, quickstart, changelog, version bump, evaluation report, release | ☑ |

Released as [v0.1.0](https://github.com/robert-mcdermott/tessera/releases/tag/v0.1.0) on 2026-11-07. Detail: [waves/wave-1.md](waves/wave-1.md)

## Wave 2: Connections (v0.2.0) ☑

**Goal:** turn the graph into discovery: who works on what, who should meet, and why.

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | ORCID | ORCID connector, iDs suggested from people's own papers, ORCID evidence (`authorship/v1.1`) | ☑ |
| 2 | People model | Possible duplicates, reversible merges, dated unit memberships | ☑ |
| 3 | Accounts and roles | Local accounts, sessions, roles, scoped curators, access policy on every route | ☑ |
| 4 | Evidence v2 | Learned weights (`authorship/v2`) with held-out evaluation; safe auto-accept | ☑ |
| 5 | Concepts and expert finder | Concept profiles, expert finder, similar people, what drives a profile | ☑ |
| 6 | Collaboration graph | Collaboration network, person focus, concept landscape, with list views | ☑ |
| 7 | Missed connections and units | Missed connections, groups and bridges, the unit matrix | ☑ |
| 8 | Release prep | Guides, evaluation, dependency review, changelog, release | ☑ |

The MeSH hierarchy, approved after this release, arrived in wave 3.

Released as [v0.2.0](https://github.com/robert-mcdermott/tessera/releases/tag/v0.2.0) on 2026-11-08. Detail: [waves/wave-2.md](waves/wave-2.md)

- Concept profiles from MeSH with IDF and recency weighting; MeSH hierarchy (if the source is approved)
- Expert finder and "similar people" with shared-concept explanations
- Collaboration graph: person focus, collaboration network, concept landscape; canvas renderer with worker layout and list fallback
- Missed connections and bridges; unit-by-unit matrix
- ORCID connector as strong authorship evidence; identifier claims; ORCID consistency across a person's own papers as evidence, allowing for people with more than one iD (see the v0.1.0 pilot check in evidence-engine.md)
- Local accounts, sessions, and roles (admin, curator, researcher, viewer); researchers review their own suggestions; scoped curators
- Reversible person merges; dated unit memberships
- Safe auto-accept gated by evaluation; first learned weights (`authorship/v2`)

## Documentation site (between v0.2.0 and v0.3.0) ☑

**Goal:** a product site with the full, illustrated documentation, so that people outside the pilot can find Tessera, see it, and try it.

It waits until the main screens have settled: once wave 2's discovery views exist there is enough to show, and screenshots taken earlier would go stale within a wave. If an outside audience needs it sooner, it can move earlier; it must land before wave 7's adopter's guide, which it will host. Until then the Markdown in the repository is the documentation, and it is written so that the site can be built from it unchanged.

- **The guides stay the source.** `README.md` and the guides in `docs/*.md` are what GitHub shows and what the site is built from, so the two never disagree. Links between guides are written as GitHub reads them (`curation.md#bulk-decisions`).
- **Site generator:** `docs/site/build.mjs`, needing nothing beyond Node 22 and no packages. It builds a home page (`docs/site/pages/index.html`) and one page per guide or group of sections, with a menu in reading order, a client-side search, light and dark themes from Tessera's own design tokens, and an API reference generated from the OpenAPI document (`docs/openapi.json`, kept current by a test). The build checks every local link, anchor, and image and fails on a broken one; CI runs it. Output goes to `site/`, which git ignores.
- **Screenshots of the real app:** `docs/capture/capture.mjs` with a small Chrome DevTools Protocol driver (`docs/capture/cdp.mjs`, Node 22 only; `CHROME` picks Chrome, Chromium, Edge, or Brave). It starts its own Tessera instance in a temporary data home, opens each scene in headless Chrome, and saves it in both themes as `docs/images/<name>-light.webp` and `<name>-dark.webp`. Each scene is one line: a URL, a size, and an optional step (open the evidence drawer, open the palette). Named scenes can be retaken alone (`capture.mjs profile evidence --theme dark`).
- **A fictional demo organization.** Screenshots never show a real institution or real researchers. `tessera demo` builds a data home for an invented organization: its public records are written in the lake's own schemas under recorded fetch runs, then the real evidence engine, a scripted curator, and the search and discovery builds run, so every screen is real Tessera output about invented people. Detail: [waves/docs-site.md](waves/docs-site.md)
- **Figures in the guides:** a paragraph that is only an image, `![What it shows](images/<name>-light.webp "Caption")`, becomes a figure that switches with the theme on the site; GitHub shows the light one. Alternative text says what the picture shows.
- **Diagrams:** `docs/capture/diagrams.mjs` draws the architecture, data-flow, and evidence diagrams as plain SVG in both themes, from one color table.
- **Publishing:** the site is whatever is committed on the `gh-pages` branch, served by GitHub Pages. Maintainers keep a worktree of that branch beside the repository; the build writes into it only with `--publish` and refuses otherwise, so nothing is published by accident. The site states the version it describes.
- **Each release:** retake screenshots for screens that changed, check the guides against the changelog, build and review in both themes and at phone width, then publish once the release is out. This becomes part of every release-prep slice from then on.

## Wave 3: Showcase (v0.3.0) ☑

**Goal:** give labs and groups a living home that maintains itself, and open a public window. Released as 0.3.0 on 2026-11-08: MeSH synonyms and hierarchy, groups of every kind with roles and alumni, group pages with lab attribution, managers and an editorial workflow, the weekly inbox and digests, openings and themes, a static public site, and the brand kit.

Detail: [waves/wave-3.md](waves/wave-3.md)

- Labs as first-class entities: roster with dates and alumni, explicit lab attribution of outputs
- Modular page blocks; draft, preview, publish, history, rollback
- Weekly approval inbox for lab owners
- Research themes and projects; openings with expiry
- Public allowlisted projection: public profiles and lab pages (generated as static files, so the app stays private); SEO and accessibility; abstract display rules
- Configurable vocabulary for unit kinds, roles, and labels
- Brand kit (PLT-13), arriving with the public site, where an organization's own look matters most: an optional `[branding.palette]` that sets core colors by day and night (surfaces, text, accent and its text, record types, stripe), footer links, a favicon, and a social preview image. `tessera doctor` runs the same WCAG AA check used on rendered pages over every configured color pair, including a configured accent, and fails any pair below AA
- In-app digest notifications

## Wave 4: Insight (v0.4.0) ☑

**Goal:** trustworthy portfolio intelligence and reproducible reporting.

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | Metrics, cohorts, and the figure | Metric definitions as versioned records; cohorts (the organization, a group, picked people, a search); every figure shows definition, cohort, period, sources, and data timestamps; small-number marks | ☑ |
| 2 | Funding portfolio | Strict counting per core project and fiscal-year record; awards by year, institute, activity, mechanism, and group; grants with upcoming end dates | ☑ |
| 3 | Topics and studies | Topic landscape and trends with small-number warnings; studies by condition, intervention, phase, sponsor class, and registry status | ☑ |
| 4 | Reports and exports | Saved searches with new matches; reports; snapshots that regenerate exactly; evidence-carrying CSV, Parquet, and JSON exports | ☑ |
| 5 | Backup and operations | Backup, restore (across SQLite and PostgreSQL), and full exit export; Admin → Operations; failure alerts | ☑ |
| 6 | Embeds | Group content embedded in an existing website, from the public site | ☑ |
| 7 | Release prep | Guides, screenshots, evaluation, changelog, release | ☑ |

Released as [v0.4.0](https://github.com/robert-mcdermott/tessera/releases/tag/v0.4.0) on 2026-11-08. Detail: [waves/wave-4.md](waves/wave-4.md)

## Wave 5: Impact (v0.5.0) ☑

**Goal:** connect discovery to outcomes and opportunities.

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | Grant-to-impact threads | Grant → publications → studies → clinical citations (practice guidelines marked), typed and sourced, as an interactive thread; Insight → Impact | ☑ |
| 2 | Funding opportunities | Grants.gov connector; opportunities an organization tracks itself (foundation, internal, limited submission) | ☑ |
| 3 | Opportunity navigator | Fit and eligibility as separate signals with reasons; opportunities for a person and in a group's inbox and digest | ☑ |
| 4 | Team builder | Assemble people for an opportunity, see covered and missing concepts, export a team brief | ☑ |
| 5 | The AI layer | Optional, **off by default and invisible until enabled** (no menu item, setting, route, or mention in the app, the API document, or the public site); any OpenAI-compatible or Anthropic-compatible endpoint, official or self-hosted (including local models), and Amazon Bedrock, with credentials from the environment; full model, prompt, and source logging | ☑ |
| 6 | Grounded questions and answers | Answers that cite Tessera records and decline what the records don't support; the assistant reads only what the signed-in person may read | ☑ |
| 7 | Plain-language summaries | Reviewable lay summaries for profiles and group pages, approved before they are shown | ☑ |
| 8 | Release prep | Guides, screenshots, evaluation, changelog, release | ☑ |

Released as [v0.5.0](https://github.com/robert-mcdermott/tessera/releases/tag/v0.5.0) on 2026-11-08. Detail: [waves/wave-5.md](waves/wave-5.md)

## Wave 6: Profiles (v0.6.0) ☑

**Goal:** a full profile for every researcher, kept by the researcher, open to everyone inside the organization, and public only by their choice, with CVs and biosketches built from it. Added 2026-11-08 at the maintainer's request.

- Every profile stays visible to every signed-in account; the public page stays opt-in, set by the researcher (or someone acting for them), who also chooses which sections and items it shows
- Sections the researcher keeps, beside what Tessera builds from public records: overview, research interests, positions and appointments, education and training, honors and awards, memberships and service, teaching and mentoring, talks and presentations, links, and featured publications
- Kept by hand by the researcher, a curator, or an assistant the researcher names; edits take effect directly and every change is recorded
- CVs and NIH-format biosketches (following NIH's form in force when the wave is built): choose sections, items, and order; write the personal statement and contributions to science from confirmed papers; keep several versions (one for a grant, one for a promotion); print to PDF or download as Markdown
- Simple by design: no annual-review or reporting workflow, and no external integration required. Each entry records where it came from, so imports (ORCID's public record, an HR feed) can be added later without duplicating anything
- Wave 5's plain-language summary and the opportunities that fit a researcher appear on their profile

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | The sections a researcher keeps | Profile entries by section, with their sources, checks, and a history of every change | ☑ |
| 2 | The profile page | About first, edited in place by those who keep it, read by everyone | ☑ |
| 3 | Someone to act for them | A researcher names accounts that keep their profile, CVs, and biosketches | ☑ |
| 4 | Public by choice | The researcher chooses whether the profile is public and which sections and entries it shows | ☑ |
| 5 | CVs | Named CV versions from the profile and confirmed records, printable and downloadable | ☑ |
| 6 | NIH biosketches | NIH's Common Form and Supplement, with its limits checked, ready for SciENcv | ☑ |
| 7 | Release prep | Guides, screenshots, evaluation, changelog, release | ☑ |

Released as [v0.6.0](https://github.com/robert-mcdermott/tessera/releases/tag/v0.6.0) on 2026-11-08. Detail: [waves/wave-6.md](waves/wave-6.md)

## Wave 7: Adoption (v0.7.0) ◐

**Goal:** make Tessera easy for another organization to adopt and operate.

- PostgreSQL ledger with the full test suite running against both databases
- Optional single sign-on with OpenID Connect, beside local accounts, which stay the default and may be all a small organization needs: any standards-based provider (Microsoft Entra ID, Okta, Google, Keycloak, and others) through the authorization-code flow with PKCE; accounts matched or created at first sign-in; an optional mapping from the provider's groups or app roles to Tessera roles and group scopes, applied at every sign-in; a local admin account always works as a way back in
- White-label kit: a visual theme builder on top of wave 3's brand kit, logo guidance, vocabulary presets
- Optional container image and reference service files (systemd)
- Adopter's guide: install, configure the organization profile, first sync, curation playbook, backups, upgrades
- Performance at 10,000 people and 1 million publications
- WCAG 2.2 AA audit and fixes; security review

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | Both databases | The full suite on SQLite and PostgreSQL, with one command for both | ☑ |
| 2 | Single sign-on | OpenID Connect with PKCE, account linking, role mapping, a local admin always | ☑ |
| 3 | At scale | A simulated 10,000 people and 1,000,000 publications, a benchmark command, and the fixes | ☑ |
| 4 | The organization's look and words | A theme builder in the app, logo guidance, vocabulary presets | ☑ |
| 5 | Running it as a service | Container image, systemd units, reverse-proxy examples, health checks | ☐ |
| 6 | Accessible and reviewed | A WCAG 2.2 AA audit and a security review, with fixes | ☐ |
| 7 | The adopter's guide | From installation to a curated, published site | ☐ |
| 8 | Release prep | Guides, screenshots, checks, changelog, release | ☐ |

Detail: [waves/wave-7.md](waves/wave-7.md)

## v1.0.0: Stable ☐

- API stability guarantee for `/api/v1`; documented deprecation policy
- Upgrade path tested across all 0.x schemas
- Hardening from pilot feedback
- A nightly sync whose work follows what changed: resolve only the new papers and the people whose records or decisions changed, rather than the whole organization, checked against a full resolve and measured with `tessera bench` (wave 7 recorded the miss: about 15 minutes at 1,000,000 publications)

## Later (after leadership commits resources)

Optional adapters for institutional systems, each behind the same connector interface and never required: identity and HR rosters, sponsored-research awards and proposals (enabling success rates), local clinical-study status, repositories and content management systems.
