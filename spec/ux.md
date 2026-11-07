# UX and design system

Tessera should feel like a modern, professional product: calm, fast, and precise. It is used for serious work (curation, reporting, finding colleagues), so clarity beats decoration. The mosaic idea gives it a visual identity without getting in the way.

## Design principles

1. **Content first.** People, papers, grants, and studies are the interface. Chrome stays quiet.
2. **Show the evidence one click away.** Every link carries a small status mark. Clicking it opens the evidence drawer.
3. **The URL is the state.** Searches, filters, tabs, sort order, and graph focus all live in the URL. Back works; every view can be shared.
4. **Keyboard first, mouse friendly.** Everything is reachable by keyboard. Curation is fastest with keys.
5. **Honest data.** Freshness is visible. Unknown is shown as unknown. Metrics show their definitions.
6. **Fast by default.** Skeletons rather than spinners; instant navigation for cached views; no layout shift.
7. **Accessible.** WCAG 2.2 AA in both themes.

## Visual language

### Entity colors

Each entity type has one hue, used consistently in chips, icons, graph nodes, and accents. Color is never the only signal: an icon and label always accompany it.

| Entity | Hue family | Icon idea |
|---|---|---|
| Person | Indigo | Head-and-shoulders silhouette |
| Publication | Teal | Page |
| Grant | Amber | Banknote/award |
| Study | Rose | Clipboard with pulse line |
| Concept | Violet | Hexagon tile |
| Unit / Lab | Slate / Green | Building / flask |

### Tokens

All visual values are CSS custom properties defined once in `tokens.css`, using `oklch()` so the light and dark palettes stay perceptually balanced.

- **Surfaces:** `--bg`, `--surface-1..3`, `--border`, `--border-strong`
- **Text:** `--text`, `--text-muted`, `--text-subtle`, `--text-inverse`
- **Brand:** `--accent`, `--accent-hover`, `--accent-contrast` (set from branding configuration)
- **Entities:** `--ent-person`, `--ent-pub`, `--ent-grant`, `--ent-study`, `--ent-concept`, `--ent-unit`, each with a `-soft` tint for backgrounds
- **Status:** `--ok`, `--warn`, `--danger`, `--info`; and evidence states `--accepted`, `--suggested`, `--rejected`
- **Spacing:** 4 px base scale (`--space-1` = 4 px … `--space-10` = 64 px)
- **Radius:** `--radius-s` 6 px, `--radius-m` 10 px, `--radius-l` 16 px
- **Elevation:** three shadow levels, softened in dark mode
- **Motion:** `--ease-out`, durations 120/200/320 ms, all disabled under `prefers-reduced-motion`

### Typography

- **Font:** the platform's system UI stack. Zero downloads, native rendering. Numbers use `font-variant-numeric: tabular-nums` in tables and metrics.
- **Scale:** 12 / 13 / 14 (body) / 16 / 18 / 20 / 24 / 30 / 36 px, with line heights from 1.25 (headings) to 1.55 (body).
- **Weights:** 400, 500, 600. Headings avoid 700+ for a calmer look.

### Themes

- **Light, dark, and system.** The theme follows the operating system by default; a toggle in the top bar overrides it and is remembered.
- Implemented with `:root[data-theme="dark"]` token overrides plus a `prefers-color-scheme` default. No component has theme-specific CSS.
- Both themes are checked for AA contrast, including entity tints and chart colors.

### Branding (white-label)

An adopting organization sets its product name, organization name, logo (light and dark variants), favicon, and accent color in `tessera.toml`. The server generates `theme.css` from these settings. No code changes are needed, and "Tessera" appears only in a small "Powered by Tessera" footer link, which can be turned off.

## Information architecture

```text
Top bar:  [logo / product name]   [ ⌘K  Search everything… ]   [theme] [operator / user]
Sidebar:  Home · People · Publications · Grants · Studies · Discover (w2) · Labs (w3)
          Insights (w4) · Opportunities (w5) ─── Review queue (badge) · Admin
```

- **Command palette (⌘K / Ctrl-K):** search all entities, jump to pages, and run actions ("Review suggestions for…", "Toggle theme").
- **Sidebar** collapses to icons on narrow screens and becomes a drawer on mobile.

## Key screens (wave 1)

| Screen | Purpose | Highlights |
|---|---|---|
| **Home** | A view of the whole mosaic | Counts by entity with sparklines; recent publications; active grants; source freshness strip; quick search |
| **Search** | One search for everything | Grouped results by type with counts; facets (type, year, unit, concept, status); keyword highlighting; result previews |
| **People directory** | Browse researchers | Card grid or dense table toggle; column chooser saved per user; facets with counts; sort by name or activity |
| **Person profile** | Everything about one person | Header (name, units, identifiers, top concepts); tabs: Overview, Publications, Grants, Studies, (Connections in w2); activity timeline; "suggested" items visually distinct with review actions |
| **Publication detail** | One paper in context | Citation, abstract, author list with matched people highlighted, MeSH concepts, grants acknowledged, studies referencing it, citation metrics with definitions |
| **Grant detail** | One grant in context | Investigators, fiscal-year funding bars (amount type labeled), linked publications, linked studies |
| **Study detail** | One study in context | Registry status (clearly labeled as registry status), phase, conditions, sites at the organization, officials, linked publications and grants, link to the official record |
| **Review queue** | Fast curation | Split view: queue on the left, evidence on the right; keyboard flow; bulk accept within band; undo toast |
| **Discovered people** | Grow the directory | Candidate clusters with affiliated paper counts; promote or dismiss, singly or in bulk |
| **Admin: Sources** | Data health | Per-source last run, status, counts, freshness, errors; run now; checkpoint view |

## Components

Each is a small ES module that returns DOM. There is no virtual DOM and no templating library.

- **Layout:** app shell, sidebar, top bar, page header, split view, drawer, modal, tabs
- **Data display:** entity card, entity chip, data table (sortable, resizable, column chooser, dense mode, virtual scrolling past 500 rows), definition list, stat tile with sparkline, timeline, empty state, skeleton
- **Evidence:** status mark (accepted, suggested, rejected, deferred, no longer reported), evidence drawer (evidence items with weight bars, sources, and history)
- **Input:** search field with suggestions, facet list with counts, select, segmented control, toggle, date-range picker, button set, keyboard shortcut hints
- **Feedback:** toast with undo, inline alert, freshness badge, progress bar for jobs
- **Charts (SVG):** bar, stacked bar, line, sparkline, heatmap, with accessible tables behind them
- **Graph (wave 2):** canvas network with worker layout, plus a list fallback

## Interaction details

- **Status marks:** ● accepted (solid), ◐ suggested (half), ○ deferred, ✕ rejected (struck, hidden by default). Each mark has a tooltip and an accessible label.
- **Evidence drawer:** opens from the right without leaving the page. It shows "Why Tessera thinks this", the evidence list with signed weight bars, the sources with retrieval times, and the decision history.
- **Undo everywhere:** every curation action shows a toast with Undo for 8 seconds, and history keeps it forever.
- **Freshness badges:** "PubMed · 2 days ago". Amber when older than the source's expected cadence; red with an explanation when the last run failed.
- **Empty states teach:** an empty directory explains how to run the first sync; an empty queue celebrates and links to Discovered people.

## Accessibility checklist

- Landmarks, headings in order, skip link
- Visible focus ring (2 px, offset) in both themes
- All interactive elements reachable and operable by keyboard; no keyboard traps in drawers or modals
- `aria-live` announcements for async results and curation actions
- Charts and graphs have tabular alternatives
- Targets at least 24 × 24 px; comfortable spacing on touch devices
- Respects `prefers-reduced-motion` and `prefers-contrast`

## Performance budgets

| Budget | Target |
|---|---|
| App shell JS + CSS (gzipped) | ≤ 100 KB |
| First meaningful render on a laptop | < 1 s |
| Profile API response (p95), 5k people, 300k publications | < 300 ms |
| Search API response (p95) | < 200 ms |
| Review queue: next item after a decision | < 100 ms (prefetched) |
