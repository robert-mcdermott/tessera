# ADR-0004: Vanilla web frontend with no build step

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

The web app must look modern and professional, support light and dark themes, and offer rich interactions (a command palette, data tables, charts, a network graph). It must also keep dependencies minimal and stay maintainable by small teams at adopting organizations, without a JavaScript toolchain.

## Decision

- **Plain HTML, CSS, and JavaScript ES modules**, served as static files by the API process. No framework, no npm, no bundler, no transpiler.
- **Modern platform features** carry the weight: ES modules, `fetch`, custom properties, `oklch()` colors, CSS nesting, container queries, `<dialog>`, the History API, Web Workers, and Canvas 2D.
- **Small in-house modules**: a router, a tiny DOM helper, a component pattern (a function that builds and returns DOM), a store for URL state, an SVG chart module, and a canvas graph with a worker-based force layout.
- **No third-party assets at runtime**: system fonts; original icons and logo as inline SVG; no CDNs.
- **Supported browsers:** current and previous major versions of Chromium-based browsers, Firefox, and Safari.

## Consequences

- Nothing to install or build for the frontend; changes are visible on reload.
- No supply-chain exposure from frontend packages.
- Tessera must implement and test its own components carefully, so the component set is kept deliberately small and consistent (see [ux.md](../ux.md)).
- A strict Content-Security-Policy is possible: no inline scripts and no external origins.

## Alternatives

- **A component framework:** faster for some UI work, but it adds a toolchain, an upgrade treadmill, and a dependency footprint the project explicitly wants to avoid.
- **A charting or graph library:** convenient, but the required charts are few and simple, and the graph needs custom interaction design anyway.
- **Server-rendered templates for everything:** good for public pages (planned for wave 3), but less suited to the interactive internal workspace.
