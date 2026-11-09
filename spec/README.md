# Tessera specification

> **Every contribution, connected.**

This folder is Tessera's public planning space: the product vision, the architecture, the decisions behind it, and the roadmap we build against. It is the source of truth for *what* we are building and *why*. Code says *how*.

## Start here

| Document | What it answers |
|---|---|
| [vision.md](vision.md) | What Tessera is, who it serves, what makes it different, and what it deliberately is not |
| [requirements.md](requirements.md) | The capability catalog, with IDs, priorities, and the wave that delivers each |
| [architecture.md](architecture.md) | System shape: components, data flow, runtime, and directory layout |
| [data-model.md](data-model.md) | Entities, the assertion/evidence model, and the ledger and lake schemas |
| [data-sources.md](data-sources.md) | Each public source: what we take, how often, the limits, and the terms |
| [evidence-engine.md](evidence-engine.md) | How Tessera finds people and decides who wrote what, and how it explains itself |
| [discovery.md](discovery.md) | Concept profiles, similarity, and the collaboration graph |
| [ux.md](ux.md) | Design principles, design system, information architecture, and key screens |
| [security.md](security.md) | The threat model, the controls for each threat, and the review against OWASP ASVS level 1 |
| [screen-reader-check.md](screen-reader-check.md) | A script for checking the app with a screen reader, by hand |
| [roadmap.md](roadmap.md) | Waves (releases) and their slices, from v0.1.0 to v1.0.0 |
| [waves/](waves/) | Detailed slice plans and status for each wave |
| [process.md](process.md) | How we work: waves and slices, branches, releases, and the definition of done |
| [decisions/](decisions/) | Architecture decision records (ADRs) |
| [glossary.md](glossary.md) | Shared vocabulary |

## How to use these documents

- **Before starting a slice**, read its section in the current wave file and the documents it links to.
- **When a decision changes the architecture**, write a new ADR in [decisions/](decisions/) rather than silently editing an old one. Supersede, don't rewrite.
- **When a slice lands**, update its status in the wave file and add a line to the changelog.
- **Keep this folder generic.** Tessera is built for any research organization to adopt and re-brand. Nothing here names a specific institution, a specific person, or another product. Examples use *Example University*.
