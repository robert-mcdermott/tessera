# How we work

## Waves and slices

- A **wave** is a release. Wave *n* becomes version `0.n.0`.
- A wave is planned in [`waves/wave-n.md`](waves/) as a set of **slices**: coherent chunks of related work with a goal, a checklist, and acceptance criteria.
- The **last slice** of every wave is release prep: documentation, changelog, version bump, final checks.
- Slices are done in order unless the wave file says otherwise. A slice may be split if it grows. It is not left half-finished at the end of a wave.

## Lifecycle of a wave

```text
main ──●────────────────────────────────────────●── tag v0.n.0 ──▶ release
        \                                      /
 wave-n  ●── slice 1 ── slice 2 ── … ── slice k (release prep) ── PR
```

1. Branch `wave-n` from an up-to-date `main`.
2. Work slice by slice. Each slice lands as one or more focused commits on `wave-n`, with the wave file's checklist and status updated in the same commits.
3. The final slice prepares the release (below).
4. Open a pull request from `wave-n` to `main`, then merge it.
5. Switch to `main` and pull.
6. Tag `v0.n.0` on the merge commit and publish a release whose notes come from the changelog.
7. Start the next wave.

## Definition of done (every slice)

- [ ] Acceptance criteria in the wave file are met
- [ ] `uv run pytest` passes with no network access
- [ ] `uv run ruff check` and `uv run ruff format --check` are clean
- [ ] New behavior has tests; bug fixes have regression tests
- [ ] Public API changes are reflected in OpenAPI and the docs
- [ ] Lake or ledger schema changes include a migration and an update to [data-model.md](data-model.md)
- [ ] Any new runtime dependency has an ADR
- [ ] The private-terms check passes
- [ ] The wave file's checklist and status are updated; `CHANGELOG.md` has an entry under *Unreleased*

## Release checklist (last slice)

- [ ] All slices in the wave are ☑ or explicitly moved to a later wave with a note
- [ ] README and user docs updated for new capabilities
- [ ] `CHANGELOG.md`: move *Unreleased* to the new version with the date
- [ ] Version bumped in `pyproject.toml`
- [ ] `uv lock` up to date; fresh-clone install and quickstart verified
- [ ] Private-terms check run over the whole repository
- [ ] Roadmap and wave status updated
- [ ] Documentation site: screenshots of changed screens retaken (`node docs/capture/capture.mjs SCENE…`), diagrams redrawn if the architecture changed, the guides checked against the changelog, and the site built and reviewed in both themes and at phone width
- [ ] After the release is published: the site published (below)

## The documentation site

The guides in `docs/` are the source; the site is built from them and published from the `gh-pages` branch, served by GitHub Pages.

```bash
node docs/capture/capture.mjs              # retake every screenshot (or name scenes)
node docs/capture/diagrams.mjs             # redraw the diagrams
node docs/site/build.mjs                   # build into ./site; fails on any broken link
python3 -m http.server -d site 8780        # review at http://127.0.0.1:8780
```

To publish, keep a worktree of the `gh-pages` branch beside the repository (once: `git worktree add ../tessera-site gh-pages`), then:

```bash
node docs/site/build.mjs --publish ../tessera-site
cd ../tessera-site && git add -A && git commit -m "Site for vX.Y.Z" && git push origin gh-pages
```

The build writes into the worktree only with `--publish`, and nothing reaches the public site until that branch is pushed. Screenshots show the demo organization only; never take them from a real organization's data.

## Versioning

- Semantic versioning. While below 1.0, a minor version (a wave) may contain breaking changes, and they are called out under **Breaking** in the changelog.
- The ledger schema is always migrated forward by Alembic. Lake schema versions are recorded per table; readers handle the previous version or the release notes say a `tessera replay` is required.

## Commits and pull requests

- Commit messages: a short imperative summary line (≤ 72 characters), a blank line, then *what* and *why* in plain prose. Reference requirement IDs where useful (e.g. `CUR-02`).
- One pull request per wave. Its description summarizes the slices, notable decisions (link the ADRs), and how it was verified.

## Content rules for everything committed

Tessera is generic and re-brandable, and the repository is public. Code, docs, tests, commit messages, pull requests, and release notes therefore follow these rules:

- **Generic examples only.** Use *Example University*, `example.edu`, and invented names. Never a real organization's configuration.
- **No personal data in fixtures.** Test fixtures are synthetic or scrubbed public records, with contact details removed.
- **Concepts, not comparisons.** Describe what Tessera does on its own terms. Market analysis is not kept in this repository.
- **No secrets.** API keys and credentials come from environment variables or untracked local config.

The private-terms check (`scripts/check_terms.py`) enforces the rules that can be automated, using a term list kept outside version control.

## Architecture decisions

Significant decisions are recorded as ADRs in [decisions/](decisions/), numbered sequentially, using the template in [ADR-0001](decisions/0001-record-architecture-decisions.md). An ADR is never rewritten after acceptance; a new ADR supersedes it.
