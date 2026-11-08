# Discovery: concepts, similarity, and the collaboration graph

*Delivered in wave 2. Designed now so wave 1 stores what wave 2 needs.*

Discovery helps people find **experts** ("who works on X?") and **collaborators** ("who should I talk to?"), and lets leaders see the **shape** of their research community. It has to be explainable: every suggestion comes with the concepts behind it.

## Concepts come from a controlled vocabulary

Tessera describes research with **MeSH descriptors**, the controlled vocabulary that NLM indexers attach to PubMed records. That gives us:

- **Consistency:** "cardiac failure" and "heart failure" are not separate free-text tags; both map to the same curated descriptor.
- **Hierarchy:** descriptors sit in trees, so specific concepts roll up into broader areas. This needs the MeSH descriptor data (a candidate source).
- **No model required:** concepts come from the records themselves. There is no LLM tagging step to run, pay for, or audit.
- **Coverage gap:** new publications lack MeSH for weeks or months. Until indexing arrives, Tessera falls back to author keywords and title terms and marks those concepts as provisional.

Grant terms and study condition/intervention MeSH terms add to a person's concepts with a lower weight.

## Concept profiles

Each person gets a **concept profile**: a weighted vector over descriptors, built from their *accepted* publications (and, at lower weight, their grants and studies).

For a person *p* and descriptor *c*:

```text
weight(p, c) = Σ over p's accepted papers containing c of
                 major(c)     × 2.0 if c is a major topic of the paper, else 1.0
               × position     × 1.5 if p is first or last author, else 1.0
               × recency      × 0.5 ^ (age_in_years / 5)
then           × idf(c)       = log(N_people / people_with_c)
then normalize the vector to unit length
```

- **IDF** keeps broad descriptors ("Humans", "Female", "Adult") from dominating. Check-tag descriptors are excluded outright.
- **Recency decay** (5-year half-life) keeps profiles current without discarding history. The half-life is a setting.
- Profiles are computed in DuckDB from the lake and the ledger's accepted authorships, and written to `derived/concept_profile.parquet` on each build.

## Similarity

- **Similarity between two people** is the cosine of their concept profiles: a 0–1 score.
- **Explanation** is the list of shared descriptors ranked by their contribution (the product of the two weights). The UI shows the top five with bars, so a user can see *why* two people match.
- **Precomputation:** all pairs above a floor (default 0.15) are computed at build time and stored in `derived/similarity_pair.parquet`. UI controls filter precomputed pairs and never trigger a recomputation.
- **Expert search** treats a query (free text mapped to descriptors, or descriptors chosen directly) as a mini profile and ranks people by cosine similarity, showing the matching concepts for each.

## Collaboration signals

| Signal | Meaning | Source |
|---|---|---|
| **Coauthorship** | They have published together | Accepted authorships on shared PMIDs |
| **Co-investigation** | They share a grant or study | Investigator and official assertions |
| **Similarity** | Their research overlaps | Concept profiles |
| **Missed connection** | High similarity, no shared papers, grants, or studies | Similarity minus collaboration |
| **Bridge** | Connects otherwise separate groups | Betweenness in the coauthor network |

*Missed connections* are Tessera's most distinctive discovery feature: people who should probably meet, ranked by overlap, with the shared concepts as the conversation starter.

## Graph views

| View | Nodes | Edges | Question it answers |
|---|---|---|---|
| **Person focus** | One person, their top concepts, and the people who share them | person–concept only | "Who shares my interests, and around what?" |
| **Collaboration network** | People | Coauthorship (solid) and missed connections (dashed) | "Who works together, and who should?" |
| **Concept landscape** | Concepts | Co-occurrence across people | "What are our research areas and how do they connect?" |
| **Unit matrix** | Units × units heatmap | Similarity versus actual collaboration | "Which departments share interests but rarely collaborate?" |

### Design lessons that shape these views

- **In person focus, draw person–concept edges only.** Drawing direct person–person edges as well collapses a force layout into one tangle around the focused person. When concepts act as hubs, people cluster naturally around what they share. Person–person similarity belongs in the side panel list, with scores and shared concepts.
- **Hover dims the rest.** Hovering a node fades everything except it, its neighbors, and their edges.
- **Search inside the graph.** Typing a name highlights matches and flies the view to the chosen node.
- **Focus and reset.** Any node can become the new focus; a breadcrumb trail allows stepping back.
- **Deep links.** Every profile links to its own focus view; every graph state is in the URL.
- **Bounded size.** Views default to about 150 nodes. Controls (minimum similarity, maximum people, unit filter) are debounced and only filter precomputed data.
- **Heatmaps sort by density** and open the underlying list on click, loading it in one request.

## Rendering (vanilla, no libraries)

- A small **force-directed layout** (charge, link springs, collision, centering, with a grid-based approximation for repulsion) runs in a **Web Worker** so the page stays responsive.
- Drawing uses **Canvas 2D** for the network with a hit-testing index for hover and click, and **SVG** for small views and heatmaps, where crisp text and accessibility matter more.
- The layout cools and stops. Users can pin nodes by dragging. `prefers-reduced-motion` skips the animation and shows the settled layout.
- **Accessibility:** every graph view has a "List" toggle that presents the same nodes and relationships as a sortable, keyboard-navigable table. The graph is never the only way to get the information.

## Responsible discovery

- Similarity says nothing about quality or availability. Copy and layout avoid suggesting rankings.
- People can only appear in discovery through their accepted publications, so unreviewed suggestions never drive recommendations.
- Concept profiles are visible to their owner, who can see exactly what drives them.
