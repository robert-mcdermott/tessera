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
| 6 | v0.6.0 | **Adoption** | Another organization installs, re-brands, and runs Tessera on PostgreSQL with single sign-on |
| — | v1.0.0 | **Stable** | Hardened, audited, documented, with API and upgrade guarantees |

Status key: ☐ planned · ◐ in progress · ☑ done

---

## Wave 1: First light (v0.1.0) ◐

**Goal:** prove that a useful, trustworthy research graph can be built from public data alone, with evidence on every link.

| Slice | Name | Delivers | Status |
|---|---|---|---|
| 1 | Foundation | Project skeleton, config, data home, ledger and migrations, lake writer and reader, CLI and API skeletons, tooling | ☑ |
| 2 | Connectors | Shared HTTP client; PubMed, iCite, RePORTER, and ClinicalTrials.gov connectors into the raw zone and lake; run records; incremental checkpoints | ☑ |
| 3 | Evidence engine v1 | People bootstrap, name handling, candidate retrieval, `authorship/v1` scoring, source-reported links, assertions with evidence, `tessera sync` | ☑ |
| 4 | API and search | `/api/v1` for people, publications, grants, studies, assertions, and sources; FTS5 search with facets | ☑ |
| 5 | Web app and design system | Vanilla JS shell, tokens, light/dark themes, branding, command palette; Home, Search, People, profile, and detail pages; source health | ☐☑ |
| 6 | Curation | Review queue with evidence drawer, keyboard flow, bulk within band, undo, manual additions, discovered-people queue | ☐☑ |
| 7 | Release prep | Docs, quickstart, changelog, version bump, evaluation report, release | ☐ |

Detail: [waves/wave-1.md](waves/wave-1.md)

## Wave 2: Connections (v0.2.0) ☐

**Goal:** turn the graph into discovery: who works on what, who should meet, and why.

- Concept profiles from MeSH with IDF and recency weighting; MeSH hierarchy (if the source is approved)
- Expert finder and "similar people" with shared-concept explanations
- Collaboration graph: person focus, collaboration network, concept landscape; canvas renderer with worker layout and list fallback
- Missed connections and bridges; unit-by-unit matrix
- ORCID connector as strong authorship evidence; identifier claims
- Local accounts, sessions, and roles (admin, curator, researcher, viewer); researchers review their own suggestions; scoped curators
- Reversible person merges; dated unit memberships
- Safe auto-accept gated by evaluation; first learned weights (`authorship/v2`)

## Documentation site (between v0.2.0 and v0.3.0) ☐

**Goal:** a product site with the full, illustrated documentation, so that people outside the pilot can find Tessera, see it, and try it.

It waits until the main screens have settled: once wave 2's discovery views exist there is enough to show, and screenshots taken earlier would go stale within a wave. If an outside audience needs it sooner, it can move earlier; it must land before wave 6's adopter's guide, which it will host. Until then the Markdown in the repository is the documentation, and it is written so that the site can be built from it unchanged.

- **The guides stay the source.** `README.md` and the guides in `docs/*.md` are what GitHub shows and what the site is built from, so the two never disagree. Links between guides are written as GitHub reads them (`curation.md#bulk-decisions`).
- **Site generator:** `docs/site/build.mjs`, needing nothing beyond Node 22 and no packages. It builds a home page (`docs/site/pages/index.html`) and one page per guide or group of sections, with a menu in reading order, a client-side search, light and dark themes from Tessera's own design tokens, and an API reference generated from the OpenAPI document (`docs/openapi.json`, kept current by a test). The build checks every local link, anchor, and image and fails on a broken one; CI runs it. Output goes to `site/`, which git ignores.
- **Screenshots of the real app:** `docs/capture/capture.mjs` with a small Chrome DevTools Protocol driver (`docs/capture/cdp.mjs`, Node 22 only; `CHROME` picks Chrome, Chromium, Edge, or Brave). It starts its own Tessera instance in a temporary data home, opens each scene in headless Chrome, and saves it in both themes as `docs/images/<name>-light.webp` and `<name>-dark.webp`. Each scene is one line: a URL, a size, and an optional step (open the evidence drawer, open the palette). Named scenes can be retaken alone (`capture.mjs profile evidence --theme dark`).
- **A fictional demo organization.** Screenshots never show a real institution or real researchers. A `tessera demo` command builds a data home by replaying synthetic source payloads (from the generator the tests already use) through the real connectors, evidence engine, and search index, so every screen is real Tessera output about invented people.
- **Figures in the guides:** a paragraph that is only an image, `![What it shows](images/<name>-light.webp "Caption")`, becomes a figure that switches with the theme on the site; GitHub shows the light one. Alternative text says what the picture shows.
- **Diagrams:** `docs/capture/diagrams.mjs` draws the architecture, data-flow, and evidence diagrams as plain SVG in both themes, from one color table.
- **Publishing:** the site is whatever is committed on the `gh-pages` branch, served by GitHub Pages. Maintainers keep a worktree of that branch beside the repository; the build writes into it only with `--publish` and refuses otherwise, so nothing is published by accident. The site states the version it describes.
- **Each release:** retake screenshots for screens that changed, check the guides against the changelog, build and review in both themes and at phone width, then publish once the release is out. This becomes part of every release-prep slice from then on.

## Wave 3: Showcase (v0.3.0) ☐

**Goal:** give labs and groups a living home that maintains itself, and open a public window.

- Labs as first-class entities: roster with dates and alumni, explicit lab attribution of outputs
- Modular page blocks; draft, preview, publish, history, rollback
- Weekly approval inbox for lab owners
- Research themes and projects; openings with expiry
- Public allowlisted projection: server-rendered public profiles and lab pages; SEO and accessibility; abstract display rules
- Configurable vocabulary for unit kinds, roles, and labels
- In-app digest notifications

## Wave 4: Insight (v0.4.0) ☐

**Goal:** trustworthy portfolio intelligence and reproducible reporting.

- Metric definitions as first-class records; every chart shows definition, cohort, period, source, and timestamp
- Funding portfolio with strict counting rules; grants with upcoming end dates
- Topic landscape and trends with small-number warnings
- Study portfolio by condition, intervention, phase, sponsor class, and registry status
- Cohorts and saved searches; reporting snapshots; evidence-carrying exports (CSV, Parquet, JSON)
- Backup, restore, and full exit export; operational dashboard for jobs and failures
- Embeddable lab widgets

## Wave 5: Impact (v0.5.0) ☐

**Goal:** connect discovery to outcomes and opportunities.

- Grant-to-impact threads: grant → publications → studies → clinical citations, as an interactive thread view
- Grants.gov connector; opportunity navigator with fit and eligibility as separate signals
- Team builder: assemble people for an opportunity, see covered and missing concepts, export a team brief
- Optional AI layer, off by default: pluggable provider, grounded Q&A with citations, reviewable plain-language summaries, full model and prompt logging

## Wave 6: Adoption (v0.6.0) ☐

**Goal:** make Tessera easy for another organization to adopt and operate.

- PostgreSQL ledger with the full test suite running against both databases
- Optional OIDC single sign-on
- White-label kit: theme builder, logo guidance, vocabulary presets
- Optional container image and reference service files (systemd)
- Adopter's guide: install, configure the organization profile, first sync, curation playbook, backups, upgrades
- Performance at 10,000 people and 1 million publications
- WCAG 2.2 AA audit and fixes; security review

## v1.0.0: Stable ☐

- API stability guarantee for `/api/v1`; documented deprecation policy
- Upgrade path tested across all 0.x schemas
- Hardening from pilot feedback

## Later (after leadership commits resources)

Optional adapters for institutional systems, each behind the same connector interface and never required: identity and HR rosters, sponsored-research awards and proposals (enabling success rates), local clinical-study status, repositories and content management systems.
