# ADR-0006: All relationships are evidenced assertions

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Tessera's value depends on trust. Users must be able to see why the system believes a person wrote a paper or a grant funded a study, correct it when it is wrong, and rely on their corrections persisting. Data is refreshed continuously from sources that change, disagree, and occasionally retract.

## Decision

Every relationship between entities (except structural parent/child records within one source) is stored as an **assertion** in the ledger with:

- a typed **predicate** and typed subject and object,
- an **origin** (`source`, `inferred`, or `human`) and a versioned **method**,
- a **score** for inferred links,
- a list of **evidence items**, each with a feature, value, weight, plain-language explanation, and source reference,
- a **status** (`suggested`, `accepted`, `rejected`, `deferred`) and a **locked** flag set by human decisions,
- an append-only history of **curation events**.

Automation may create assertions, attach evidence, and re-score unlocked assertions. It may never change the status of a locked assertion or delete an assertion. Links a source stops reporting are marked, not removed.

## Consequences

- The UI can always answer "why?" from stored data, not by recomputing.
- Human effort is never lost to a data refresh.
- Reports can be reproduced because decisions are timestamped and history is preserved.
- The ledger grows with every suggestion ever made. Unlikely-band suggestions are capped per person and can be pruned, since they are unlocked and reproducible.
- Every new kind of relationship needs a predicate definition, evidence explanations, and UI copy. This is intended friction.

## Alternatives

- **Plain join tables with a confidence column:** simpler, but they lose why, who, and when, and they make durable human overrides awkward.
- **Event sourcing for everything:** maximal history, but heavier than needed. Append-only curation events give the needed history for decisions only.
