# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Tessera is meant to be adopted and extended by organizations other than the one that started it. Future maintainers need to know not just what the system does but why it was built this way.

## Decision

Record significant decisions as short Markdown ADRs in `spec/decisions/`, numbered sequentially. Each ADR is immutable once accepted; a change of direction is a new ADR that supersedes the old one, and the old one's status is updated to point to it.

Template:

```markdown
# ADR-NNNN: Title
- **Status:** Proposed | Accepted | Superseded by ADR-XXXX
- **Date:** YYYY-MM-DD
## Context        — the forces at play
## Decision       — what we will do
## Consequences   — what becomes easier, what becomes harder
## Alternatives   — what else we considered and why not
```

## Consequences

Decisions are discoverable and reviewable. Writing an ADR adds a small cost to significant changes, which is intended.
