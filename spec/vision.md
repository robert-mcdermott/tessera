# Vision

## The idea

A *tessera* is a single tile in a mosaic. On its own it is a small square of color. Set beside thousands of others, it becomes part of a picture no single tile could show.

Research organizations are mosaics too. Every paper, grant, clinical study, dataset, and person is a tile. Today those tiles sit in separate boxes: a publication index here, a funding database there, a trial registry somewhere else, and a staff directory that knows none of it. Nobody can see the picture.

**Tessera puts the tiles together.** It assembles an organization's research footprint from public sources, connects every contribution to the people and work around it, and shows the evidence behind each connection. Then it helps people act on that picture: find collaborators, see where expertise lives, follow a grant to its impact, and tell the story of their work.

## Positioning

> Tessera is an open research intelligence platform that connects people, publications, funding, and clinical studies, with evidence behind every connection.

Tessera is a new kind of research information management system (RIMS). Traditional systems start from administration: forms, records, compliance. Tessera starts from **connection and discovery**, and it earns trust by **showing its work**.

## Who it is for

| Persona | What they need from Tessera |
|---|---|
| **Researcher** | A profile that maintains itself; collaborators they didn't know existed; a fast way to confirm or reject suggested papers |
| **Trainee or new hire** | A map of who works on what, and who to talk to first |
| **Research development staff** | Teams for large proposals, expertise for a funding opportunity, evidence of capacity |
| **Curator or librarian** | A review queue that explains each suggestion, with fast keyboard-driven decisions and a full audit trail |
| **Program or center leader** | Portfolio views of funding, outputs, topics, and clinical studies, built on reproducible definitions |
| **Lab manager or communicator** | A living lab showcase that updates itself and asks for approval instead of hand edits |
| **Administrator at an adopting organization** | A system that runs on a single machine, uses only public data, is easy to re-brand, and keeps data portable |

## What makes Tessera different

These are the product bets. Each one is a deliberate design choice, not a feature checkbox.

1. **Public-first, integration-free start.** Tessera stands up from public data alone. Point it at an organization's public footprint (its names in grant records, its affiliation strings in publications, its sites in trial registries) and it builds a working research graph. There are no HR feeds, no sponsored-research system connections, and no IT project before anyone sees value. Institutional integrations are optional add-ons later, never a precondition.

2. **Evidence on every link.** Every connection, whether "this person wrote this paper" or "this grant funded this study", carries its source, the method that produced it, a confidence, a review status, and a plain-language explanation. Users can always ask *why* and get a real answer.

3. **The mosaic: one connected graph.** People, publications, grants, clinical studies, and research concepts live in one graph. Any tile is one click from its neighbors: from a paper to the grant that funded it, to the trial that cited it, to the other investigators on that trial.

4. **Explainable discovery.** Tessera suggests collaborators and experts using a transparent concept model built from a controlled biomedical vocabulary, not a black box. It highlights *missed connections*: people with strongly overlapping interests who have never worked together.

5. **Grant-to-impact threads.** Follow funding forward to publications, to clinical studies, to later clinical citations. Association is shown as association, never as causation.

6. **Human decisions are durable.** When a person confirms or rejects a link, that decision survives every future data refresh. Machines suggest; people decide; the record remembers who decided what and when.

7. **Responsible metrics by design.** No researcher league tables. Every metric shows its definition, cohort, period, and source. Unknown is never displayed as zero, and small numbers carry warnings.

8. **Open, portable, re-brandable.** Apache 2.0 licensed. Runs on a laptop or a single virtual machine. The core data lives in open Parquet files, so an organization's data is always exportable and never locked in. Name, logo, and colors are configuration, not code.

## Scope

### Tessera does

- Build researcher profiles from public publication, funding, and clinical-study records
- Match publications to people with explainable evidence and human review
- Link grants, publications, and clinical studies to each other and to people
- Provide search, browse, and a connected graph across all of it
- Suggest collaborators and experts with reasons
- Show research landscapes, funding portfolios, and clinical study portfolios with transparent definitions *(later waves)*
- Host living lab and group showcases with an approval workflow *(later waves)*
- Match public funding opportunities to people and teams *(later waves)*

### Tessera does not (non-goals)

- **Replace HR, identity, or sponsored-research systems.** It may *read* from them someday; it never becomes them.
- **Handle proposal submission or post-award accounting.**
- **Touch patient data.** Clinical study information is public registry metadata only.
- **Become a publication index.** It reads public indexes; it does not try to replace them.
- **Rank researchers.** There is no single quality score, by design.
- **Require any paid data source, cloud service, or AI provider.** Optional AI features may come later; the core never depends on them.

## Guiding principles

1. **Show the work.** If Tessera can't explain a connection, it doesn't assert it.
2. **Suggest, don't overwrite.** Automation proposes; humans confirm. Human decisions outrank re-ingested data.
3. **Stand alone.** Every capability must work with public data on a single machine. Anything requiring an institutional system is optional.
4. **Few dependencies, well chosen.** Each runtime dependency must justify itself in an ADR. The browser gets no frameworks.
5. **Data is the user's.** Open formats, full exports, no lock-in.
6. **Generic by default.** Nothing in the code or docs assumes a particular institution. Branding and vocabulary are configuration.
7. **Accessible and fast.** WCAG 2.2 AA, keyboard-first workflows, light and dark themes, and pages that load instantly on modest hardware.
8. **Unknown is not zero.** Missing data is shown as missing, with its freshness, never silently counted as absence.

## What success looks like

- **Wave 1:** An administrator configures an organization's public footprint, runs one command, and within an hour browses a searchable directory of researchers whose publications, grants, and trials are linked with visible evidence. A curator can confirm or reject suggestions in seconds each.
- **Wave 2:** A researcher finds a promising collaborator they had never heard of, and Tessera can say exactly why they match.
- **v1.0:** Another research organization adopts Tessera, re-brands it, and runs it without help from the original team.
