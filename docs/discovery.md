# Finding experts

Tessera describes each person's research with the MeSH headings that PubMed indexers attach to their papers. From those it answers "who works on this?", lists people with similar interests on every profile, and shows exactly what drives each person's topics. This guide explains what you see and how it is built.

## Who works on…?

Open **Find experts** in the sidebar (or press `/` and type "experts") and describe a topic in your own words: a disease, a method, a cell type, a question.

Tessera reads your words through the papers they find. It searches the organization's papers, gathers the MeSH headings those papers share, and turns them into a small set of concepts. They are shown above the results as **Understood as**:

- Select **×** on a concept to drop it, if the search drifted somewhere you did not mean.
- Type in **Add a concept** to choose a heading by name, for example "Neoplasms", and add it.
- Select a concept on any result, or on a profile, to start a new search from that concept alone.

People are ranked by how well their topics match those concepts, and each result lists the concepts that matched, so you can see why they are there. The bar is relative to the first result; it is not a grade.

Reading words through papers matters because MeSH uses its own names. A search for "cancer" finds papers indexed under "Neoplasms", so it finds the people who work on them, even though the two words never meet.

## Topics on a profile

Each profile's **Topics** card lists the person's most distinctive concepts. **How this is built** opens the full list, with, for each concept:

- its weight, relative to their strongest concept;
- how many of their papers carry it, and how many of those are confirmed;
- **from keywords**, when the concept comes only from authors' keywords on papers that have no MeSH headings yet.

A concept counts for more when it is a major topic of the paper, when the person is first or last author, and when the paper is recent: weight halves every five years. Concepts that many people in the organization share count for less, so the list shows what sets this person apart rather than what everyone has in common. Headings that describe the study rather than its subject, such as "Humans", "Female", or "Mice", are left out, as are laboratory tools such as "Green Fluorescent Proteins".

Profiles use confirmed papers and, at half weight, strong suggestions that nobody has confirmed yet, so they are useful before curation catches up. Weaker suggestions never count. To use confirmed papers only, set `likely_weight = 0` in [`[discovery]`](configuration.md#discovery).

## Similar people

The **Similar people** card on a profile lists the people whose topics overlap most, with the concepts they share. Pairs are worked out when profiles are built, not when you look, so the list is instant.

Similarity reflects the topics of people's papers. It says nothing about the quality of anyone's work or whether they are available to collaborate.

## Keeping it current

`tessera sync` rebuilds profiles and similar people at the end of every run. To rebuild them alone, for example after many curation decisions or a change to `[discovery]`:

```bash
tessera build profiles
```

Until profiles have been built once, **Find experts** says so instead of showing results.
