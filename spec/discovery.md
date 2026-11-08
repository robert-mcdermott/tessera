# Discovery: concepts, similarity, and the collaboration graph

*Delivered in wave 2. Designed now so wave 1 stores what wave 2 needs.*

Discovery helps people find **experts** ("who works on X?") and **collaborators** ("who should I talk to?"), and lets leaders see the **shape** of their research community. It has to be explainable: every suggestion comes with the concepts behind it.

## Concepts come from a controlled vocabulary

Tessera describes research with **MeSH descriptors**, the controlled vocabulary that NLM indexers attach to PubMed records. That gives us:

- **Consistency:** "cardiac failure" and "heart failure" are not separate free-text tags; both map to the same curated descriptor.
- **Hierarchy:** descriptors sit in trees, so specific concepts roll up into broader areas. This needs the MeSH descriptor data (a candidate source).
- **No model required:** concepts come from the records themselves. There is no LLM tagging step to run, pay for, or audit.
- **Coverage gap:** new publications lack MeSH for weeks or months, and some journals are never indexed. Until indexing arrives, author keywords that are the names of descriptors stand in at half weight, and a concept known only that way is marked as provisional. Title terms were tried and left out: matched against descriptor names, they mostly found generic headings ("Disease", "Water", "Risk") rather than topics.

Study condition and intervention MeSH terms add to a person's concepts at half weight. Grant terms are machine-extracted and noisy, so they are not used.

## Concept profiles

Each person in the directory gets a **concept profile**: a weighted vector over descriptors, built from their confirmed papers, their strong suggestions at a lower weight, and the studies they are an official of.

For a person *p* and descriptor *c*:

```text
weight(p, c) = Σ over p's papers containing c of
                 status       × 1.0 if confirmed, 0.5 if a strong suggestion not yet confirmed
               × major(c)     × 2.0 if c is a major topic of the paper, else 1.0
               × position     × 1.5 if p is first or last author, else 1.0
               × recency      × 0.5 ^ (age_in_years / 5)
               × provisional  × 0.5 if c stands in from an author keyword, else 1.0
             + Σ over p's studies with c as a condition or intervention of 0.5 × status × recency
then           × idf(c)       = ln((N_people + 1) / people_with_c)
then normalize the vector to unit length, and keep the top 200 concepts
```

- **Status.** Most people's papers are suggestions long before anyone confirms them, so profiles that waited for confirmation would be empty for months. Strong suggestions (at or above `evidence.strong_threshold`) therefore count at half weight (`discovery.likely_weight`); setting it to 0 counts confirmed papers only. Weaker suggestions never count. A profile shows, for each concept, how many of its papers are confirmed.
- **IDF** keeps broad descriptors from dominating; the +1 keeps a concept everyone shares from vanishing entirely. Check-tag descriptors ("Humans", "Female", "Adult") are excluded outright, and so are laboratory tools that say how rather than what ("Green Fluorescent Proteins", "Transfection", "RNA, Small Interfering").
- **Recency decay** (5-year half-life) keeps profiles current without discarding history. The half-life is a setting.
- Profiles are computed in DuckDB from the lake and the ledger's links, and written atomically to `derived/concept_profile.parquet` by `tessera sync` and `tessera build discovery`.

## Similarity

- **Similarity between two people** is the cosine of their concept profiles, each limited to its top 60 concepts: a 0–1 score.
- **Explanation** is the list of shared descriptors ranked by their contribution (the product of the two weights). The UI shows the top five with bars, so a user can see *why* two people match.
- **Precomputation:** all pairs at or above a floor (`discovery.similarity_floor`, default 0.10) are computed at build time, at most 25 per person, and stored in `derived/similarity_pair.parquet` in both directions. UI controls filter precomputed pairs and never trigger a recomputation. On the pilot organization, a floor of 0.10 gave 52 of 57 people at least one similar person; 0.15 left too many with none.
- **Expert search** treats a query as a small profile and ranks people by the dot product with theirs, showing the matching concepts for each. Descriptors chosen directly are used as given. Words are read **through the papers they find**: without MeSH's entry terms (part of the descriptor data, a candidate source), "cancer" would never meet "Neoplasms" by name, but the organization's papers about cancer carry that descriptor. The query's concepts are the descriptors of the top 200 matching papers, weighted by IDF and by rank, plus any descriptor named exactly; the top 12 are kept and shown as "Understood as", where the reader can remove one or add another.

## Collaboration signals

| Signal | Meaning | Source |
|---|---|---|
| **Coauthorship** | They have published together | Shared papers, counted as for concept profiles (confirmed, or strong suggestions at half weight) |
| **Co-investigation** | They share a grant or study | Grant investigators (from the funder) and study officials |
| **Similarity** | Their research overlaps | Concept profiles |
| **Missed connection** | High similarity, no shared papers, grants, or studies | Similarity minus collaboration |
| **Bridge** | Connects otherwise separate groups | Betweenness in the coauthor network |

*Missed connections* are Tessera's most distinctive discovery feature: people who should probably meet, ranked by overlap, with the shared concepts as the conversation starter.

**Collaboration strength.** Each shared paper, grant, or study adds 1/(k − 1) to a pair, where k is the number of directory members on it. Two people who wrote twenty papers together outweigh forty people who share one consortium paper, without discarding either. Pairs are precomputed into `derived/collaboration_edge.parquet` with their counts, years, and most recent shared papers.

## Graph views

| View | Nodes | Edges | Question it answers |
|---|---|---|---|
| **Person focus** | One person, their top 10 concepts, and the 30 people whose profiles overlap most on those concepts | person–concept only | "Who shares my interests, and around what?" |
| **Collaboration network** | People | Coauthorship (solid) and missed connections (dashed) | "Who works together, and who should?" |
| **Concept landscape** | Concepts among people's top 15, held by at least 2 people | Co-occurrence across people; each concept keeps its 5 strongest links, and concepts with none are left out | "What are our research areas and how do they connect?" |
| **Unit matrix** | Units × units heatmap | Similarity versus actual collaboration | "Which departments share interests but rarely collaborate?" |

### Design lessons that shape these views

- **In person focus, draw person–concept edges only.** Drawing direct person–person edges as well collapses a force layout into one tangle around the focused person. When concepts act as hubs, people cluster naturally around what they share. Person–person similarity belongs in the side panel list, with scores and shared concepts.
- **Hover dims the rest.** Hovering a node fades everything except it, its neighbors, and their edges.
- **Search inside the graph.** Typing a name highlights matches; Enter flies the view to the best match and selects it.
- **Labels never collide.** Labels are placed by priority (hovered, selected, in focus, found, then each view's key nodes, then the largest) and skipped where they would overlap another label or cover a node, so the picture stays legible; zooming in reveals more.
- **Focus and reset.** Any person can become the new focus (double-click, or **Focus on them**); a breadcrumb trail of the last five allows stepping back. Double-clicking a concept opens the expert finder for it.
- **Deep links.** Every profile links to its own focus view; every graph state is in the URL.
- **Bounded size.** Views default to about 150 nodes; the network keeps the people with the most collaboration. Controls (minimum similarity, maximum people, unit filter) only filter precomputed data.
- **Heatmaps sort by density** and open the underlying list on click, loading it in one request.

## Rendering (vanilla, no libraries)

- A small **force-directed layout** (charge, link springs, collision, centering, with a grid-based approximation for repulsion) runs in a **Web Worker** so the page stays responsive. It is a pure module, deterministic for the same input, and unit-tested without a browser. On a laptop, 300 nodes and 600 links lay out with no main-thread task over 50 ms.
- The graph views load on first use, so they add nothing to the app's start-up size.
- Drawing uses **Canvas 2D** for the graphs, with a grid index for hit-testing hover and click, and colours read from the theme's tokens so the graphs follow the palette and light or dark mode. **SVG** is for small views and heatmaps, where crisp text and accessibility matter more.
- The layout cools and stops. Users can pin nodes by dragging. `prefers-reduced-motion` skips the animation and shows the settled layout.
- **Accessibility:** every graph view has a "List" toggle that presents the same nodes and relationships as a sortable, keyboard-navigable table. The graph is never the only way to get the information.

## Responsible discovery

- Similarity says nothing about quality or availability. Copy and layout avoid suggesting rankings.
- People appear in discovery through confirmed papers and strong suggestions only. Strong suggestions count half, every profile says how many of each concept's papers are confirmed, and an organization that wants confirmed work only sets `discovery.likely_weight = 0`.
- Concept profiles are visible to their owner (and to anyone who can read the profile), who can see exactly what drives them: each concept's weight, papers, confirmed papers, and whether it stands in for missing MeSH.
