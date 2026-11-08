# ADR-0005: Public data only; institutional integrations are optional and later

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Research information systems usually start with integrations: HR feeds for people, sponsored-research systems for awards, clinical systems for studies. Those integrations are slow to negotiate, specific to each organization, and require access a new project doesn't have. They are why such systems take years to show value.

Much of an organization's research footprint is already public: publications in PubMed, NIH awards in RePORTER, studies in ClinicalTrials.gov, citation metrics in iCite, researcher-confirmed identities in ORCID, and funding opportunities in Grants.gov.

## Decision

- Tessera's core uses **only free, public data sources**. The approved set is listed in [data-sources.md](../data-sources.md); adding one requires updating that document.
- The organization is recognized through a configured **organization profile** (names, aliases, affiliation patterns, email domains), not through an HR feed.
- An administrator may import a **roster file** (CSV). It is a file they choose to provide, and Tessera works without it.
- Integrations with institutional systems are **optional adapters**, built later behind the same connector interface, and never required for any core capability.

## Consequences

- Any organization can try Tessera in an afternoon with no IT project.
- People without public footprints (new hires, staff scientists without publications or NIH grants) are missing until a roster is imported or they are added by hand. The directory says so.
- Funding data covers NIH awards only. Other sponsors, unfunded proposals, and success rates are out of reach until an optional adapter exists, and analytics say this explicitly.
- Matching quality depends on public signals (affiliations, ORCID, grant acknowledgments), which is why the evidence engine and its evaluation are central.

## Alternatives

- **Start with an HR integration:** most accurate people list, but it blocks every demo on access and makes the product organization-specific from day one.
- **Licensed bibliographic or analytics databases:** broader coverage, but they add cost and contracts, and their terms can restrict display, export, and AI processing.
