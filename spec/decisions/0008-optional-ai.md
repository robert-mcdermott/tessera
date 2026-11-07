# ADR-0008: AI features are optional, off by default, and arrive after the evidence model

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Language models can help with plain-language summaries, question answering over research records, and classification. They also add cost, a provider dependency, data-governance questions, and the risk of confident, unsupported claims. Tessera's core promise is that every connection can be explained.

## Decision

- The core (ingestion, matching, search, discovery, analytics) uses **no language model**. Concepts come from a controlled vocabulary; matching uses an explainable evidence model.
- AI features are planned for **wave 5**, after the assertion and evidence model is mature, and are **off by default**.
- When enabled, the provider is **pluggable** and chosen by the adopter (an OpenAI-compatible endpoint, including locally hosted models).
- AI outputs are **suggestions**: stored as assertions or drafts with the model, prompt version, inputs, and time recorded, and reviewed like any other suggestion before display.
- Answers must **cite Tessera records** and decline when the records don't support an answer.
- **Data minimization:** no contact details and no unreviewed suggestions are sent to a model.

## Consequences

- Tessera works fully in environments where AI use is restricted or unfunded.
- AI features inherit Tessera's provenance and review machinery rather than bypassing it.
- AI capabilities arrive later than they could have. That is accepted in exchange for trustworthiness.

## Alternatives

- **AI-generated research tags as the topic model:** quick to produce, but inconsistent, hard to audit, and costly to regenerate. A controlled vocabulary is more stable and explainable.
- **A required AI provider:** simpler to build against, but it excludes adopters who cannot use one.
