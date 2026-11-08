# Documentation site (between v0.2.0 and v0.3.0)

**Goal:** a product site with the full, illustrated documentation, so that people outside the pilot can find Tessera, see it, and try it. See the [roadmap](../roadmap.md#documentation-site-between-v020-and-v030-).

**Branch:** `docs-site` · **Version:** none of its own; the site describes the latest release (0.2.0) · **Status:** ☑ done; published at [robert-mcdermott.github.io/tessera](https://robert-mcdermott.github.io/tessera/)

From now on, every release-prep slice refreshes the site: screenshots of changed screens are retaken, the guides are checked against the changelog, and the site is rebuilt and published once the release is out.

## Slice 1: The demo organization ☑

- [x] `tessera demo [PATH]` builds a complete installation for the Corvane Institute for Biomedical Research, an invented organization: eight fields in five divisions, about 100 people in 22 labs, 700 publications with titles, abstracts, and MeSH headings, 35 grants, 10 clinical studies, ORCID records, and citation metrics
- [x] Records are written in the lake's own schemas under recorded fetch runs (so sources read as fresh), then the real evidence engine runs, a scripted curator works through three people in four (accepting what is true and rejecting what is not), merges most duplicate records, confirms some ORCID iDs, and decides on discovered people; the model is evaluated, and the search index and discovery data are built
- [x] Identifiers can never be mistaken for real ones: PubMed IDs from 900,000,000, NCT numbers starting `NCT9`, grant serial numbers from 990000, ORCID iDs in the unissued `0000-0000` block, DOIs under the `10.5555` test prefix, MeSH-like identifiers from `D900000`, and emails at `corvane.example`
- [x] The same seed builds the same organization; a different seed builds different people

**Acceptance (met):** the demo builds in about four seconds and fills every screen (fresh sources, a review queue, possible duplicates, discovered people, an evaluation with enough rejections to offer learned weights, expert search, the graphs, missed connections, the unit matrix, and accounts); a test checks each.

## Slice 2: Site generator ☑

- [x] `docs/site/build.mjs` (Node 22, no packages, about 50 ms): a home page (`docs/site/pages/index.md`, until slice 4 makes it richer) and the guides as pages with a menu in reading order (Start, Use, Run, Reference), "on this page" contents, previous and next links, a client-side search over every section, copy buttons on code, light, dark, or system themes from Tessera's own `tokens.css`, the version it describes, and an API reference generated from `docs/openapi.json` (every endpoint, its parameters with defaults, and its JSON body)
- [x] Links are rewritten from GitHub's form: guides to pages, the changelog to its page, anything else in the repository to GitHub; anchors are GitHub's, so links written for GitHub land in the same place on the site
- [x] The build checks every local link, anchor, and image and fails on a broken one; a test builds it when Node.js is installed and checks that every guide is in the menu; JavaScript unit tests cover anchors, Markdown, and the link check; output goes to `site/`, which git ignores

**Acceptance (met):** 11 pages and 125 searchable sections build with no broken links, in light and dark, and at phone width with a menu button.

## Slice 3: Screenshots and diagrams ☑

- [x] `docs/capture/cdp.mjs`, a small Chrome DevTools Protocol driver (Node.js 22's own WebSocket; `CHROME` picks the browser), and `docs/capture/capture.mjs`, which builds the demo in a temporary directory (or reuses one with `--demo`), serves it on a free port, and saves each scene in light and dark as `docs/images/<scene>-light.webp` and `-dark.webp`, with motion reduced so graphs are shown settled
- [x] 17 scenes, each one line: home, people, a profile, the evidence drawer, the review queue, expert search, the three graphs, missed connections, the unit matrix with a pair of units open, possible duplicates, discovered people, the evidence model, sources, the command palette, and a profile at phone width. Scenes pick their people from the demo through the API (the most connected curated person; the person with the most open suggestions), and can be retaken alone (`capture.mjs profile evidence --theme dark`)
- [x] `docs/capture/diagrams.mjs`: how Tessera works and how a suggestion is scored, as SVG in light and dark from one color table close to the app's tokens

**Acceptance (met):** all 34 screenshots (3.4 MB) and 4 diagrams are produced in about a minute with no manual steps; every screen is the real app showing the invented organization.

## Slice 4: Home page, figures, and publishing ☑

- [x] A product home page (`docs/site/pages/index.html`, a fragment with `{{shot:name|alt}}` placeholders for screenshots): what Tessera is, four principles, five features with screenshots, how it works, the demo in five commands, and the guides
- [x] Figures in the guides (twelve, from the home page to the unit matrix) that follow the theme on the site; GitHub shows the light version, and the README's screenshot switches with GitHub's theme
- [x] A social preview card (`docs/images/social-preview.png`, 1200 × 630) rendered from `docs/capture/social.html`
- [x] Publishing to the `gh-pages` branch only with `--publish`; [process.md](../process.md) describes building, reviewing, and publishing, and the release-prep checklist includes the site
- [x] First publication on the maintainer's go-ahead: merged into `main` and published from the `gh-pages` branch

## Decisions and notes log

| Date | Note |
|---|---|
| 2026-11-08 | Slice 3 done. The screenshots found a bug: with reduced motion, the app gave every element a 0.01 ms transition, so every style change became a transition and the graphs, which read their colors back from computed styles, drew in black for anyone who prefers reduced motion. Reduced motion now turns transitions off, and a test keeps it so. The demo also showed that in the person focus view, people outgrew the topics they gather around and the person in focus drifted to the edge: topics are now the larger hubs and the person in focus is pinned at the centre. |
| 2026-11-08 | Planned after v0.2.0. The site will be public on GitHub Pages; the repository follows later. The demo writes records in the lake's schemas rather than replaying source payloads through the connectors: payload builders for five source formats would exist only for the demo, while every screen reads what lies downstream of parsing. Demo names were checked against the pilot's so that none coincide. |
