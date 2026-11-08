"""Collaboration groups and the people who bridge them (DIS-08).

**Groups** are found in the collaboration graph by modularity: people are moved between
groups while that makes more of the collaboration fall inside groups than chance would, then
groups are merged and the process repeats, until nothing improves. The order is fixed, so the
same graph always gives the same groups. Each group is named by the concepts its members hold
more than the organization as a whole.

**Bridges** are people on many of the shortest paths between others (betweenness), whose
collaborators fall in more than one group: without them, those groups would be further apart.
Each bridge is explained by the groups they connect and how many people they work with in
each. Bridges are a set, not a ranking, and are listed by name.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

PIVOTS = 400  # betweenness from at most this many sources (exact below that)
BRIDGE_SHARE = 0.10  # bridges are among the 10% with the highest betweenness...
BRIDGE_MIN = 0.02  # ...and on at least 2% of shortest paths
LABEL_CONCEPTS = 2

Graph = dict[str, dict[str, float]]


def _key(node: str) -> str:
    return hashlib.sha256(node.encode()).hexdigest()


def _one_level(graph: Graph, total: float, resolution: float) -> tuple[dict[str, str], bool]:
    """Move nodes between communities while modularity rises (one level of the method)."""
    order = sorted(graph, key=_key)
    community = {n: n for n in graph}
    degree = {n: sum(graph[n].values()) for n in graph}  # self-loops stored doubled
    tot = dict(degree)
    improved = False
    moved = True
    while moved:
        moved = False
        for n in order:
            here = community[n]
            links: dict[str, float] = defaultdict(float)
            for m, w in graph[n].items():
                if m != n:
                    links[community[m]] += w
            tot[here] -= degree[n]
            best = here
            best_gain = links.get(here, 0.0) - resolution * tot[here] * degree[n] / total
            for c in sorted(links):
                gain = links[c] - resolution * tot[c] * degree[n] / total
                if gain > best_gain + 1e-12:
                    best, best_gain = c, gain
            tot[best] = tot.get(best, 0.0) + degree[n]
            if best != here:
                community[n] = best
                moved = improved = True
    return community, improved


def communities(graph: Graph, resolution: float = 1.0) -> dict[str, str]:
    """Each node's community, by repeated local moves and aggregation."""
    total = sum(w for n in graph for w in graph[n].values())
    if total <= 0:
        return {n: n for n in graph}
    member = {n: n for n in graph}
    level = {n: dict(nbrs) for n, nbrs in graph.items()}
    while True:
        community, improved = _one_level(level, total, resolution)
        if not improved:
            break
        member = {n: community[member[n]] for n in member}
        merged: Graph = defaultdict(lambda: defaultdict(float))
        for a, nbrs in level.items():
            for b, w in nbrs.items():
                merged[community[a]][community[b]] += w
        level = {c: dict(nbrs) for c, nbrs in merged.items()}
    return member


def betweenness(graph: Graph) -> dict[str, float]:
    """Share of shortest paths through each node (unweighted, normalized to 0..1).

    Exact for up to ``PIVOTS`` people; above that, estimated from a fixed sample of sources.
    """
    nodes = sorted(graph, key=_key)
    n = len(nodes)
    score = dict.fromkeys(nodes, 0.0)
    sources = nodes[:PIVOTS]
    for s in sources:
        stack: list[str] = []
        before: dict[str, list[str]] = defaultdict(list)
        paths = dict.fromkeys(nodes, 0.0)
        paths[s] = 1.0
        depth = {s: 0}
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in graph[v]:
                if w not in depth:
                    depth[w] = depth[v] + 1
                    queue.append(w)
                if depth[w] == depth[v] + 1:
                    paths[w] += paths[v]
                    before[w].append(v)
        share = dict.fromkeys(stack, 0.0)
        while stack:
            w = stack.pop()
            for v in before[w]:
                share[v] += paths[v] / paths[w] * (1 + share[w])
            if w != s:
                score[w] += share[w]
    pairs = (n - 1) * (n - 2)  # ordered pairs of other nodes
    scale = (n / len(sources)) if sources else 1.0
    return {v: (score[v] * scale / pairs if pairs > 0 else 0.0) for v in nodes}


def _labels(groups: dict[str, list[str]], profile: Path) -> dict[str, str]:
    """Name each group by the concepts its members hold more than everyone does."""
    if not profile.is_file():
        return {}
    rows = pq.read_table(profile, columns=["person_id", "descriptor", "weight"]).to_pylist()
    overall: dict[str, float] = defaultdict(float)
    by_person: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        overall[r["descriptor"]] += r["weight"]
        by_person[r["person_id"]].append((r["descriptor"], r["weight"]))
    people = len(by_person) or 1
    out = {}
    for gid, members in groups.items():
        held: dict[str, float] = defaultdict(float)
        for p in members:
            for name, w in by_person.get(p, []):
                held[name] += w
        share = len(members) / people
        excess = {c: w - share * overall[c] for c, w in held.items()}
        top: list[str] = []
        for c in sorted(excess, key=lambda c: (-excess[c], c)):
            # "Sleep and Sleep Stages" says one thing twice: take the next one.
            if any(_stem(c) == _stem(t) for t in top):
                continue
            top.append(c)
            if len(top) == LABEL_CONCEPTS:
                break
        if top:
            out[gid] = " and ".join(top)
    return out


def _stem(descriptor: str) -> str:
    return descriptor.replace(",", " ").replace("-", " ").split()[0].lower()


GROUP_SCHEMA = pa.schema(
    [
        ("person_id", pa.string()),
        ("group_id", pa.string()),
        ("group_label", pa.string()),
        ("group_size", pa.int64()),
        ("betweenness", pa.float64()),
        ("bridge", pa.bool_()),
        (
            "links",
            pa.list_(
                pa.struct(
                    [("group_id", pa.string()), ("label", pa.string()), ("people", pa.int64())]
                )
            ),
        ),
    ]
)


def build_groups(collaboration: pa.Table, profile: Path) -> pa.Table:
    """One row per collaborating person: their group, betweenness, and whether they bridge."""
    graph: Graph = defaultdict(dict)
    for r in collaboration.select(["person_id", "other_id", "strength"]).to_pylist():
        graph[r["person_id"]][r["other_id"]] = r["strength"]
        graph[r["other_id"]][r["person_id"]] = r["strength"]
    if not graph:
        return GROUP_SCHEMA.empty_table()
    member = communities(graph)
    groups: dict[str, list[str]] = defaultdict(list)
    for p, c in member.items():
        groups[c].append(p)
    # Stable, readable ids: largest group first.
    ordered = sorted(groups, key=lambda c: (-len(groups[c]), min(groups[c])))
    gid = {c: f"g{i + 1}" for i, c in enumerate(ordered)}
    named = {gid[c]: sorted(groups[c]) for c in ordered}
    labels = _labels(named, profile)
    between = betweenness(graph)
    positive = sorted((b for b in between.values() if b > 0), reverse=True)
    cut = positive[max(0, int(len(positive) * BRIDGE_SHARE) - 1)] if positive else 1.0
    rows: dict[str, list[Any]] = {name: [] for name in GROUP_SCHEMA.names}
    for p in sorted(graph):
        own = gid[member[p]]
        counts: dict[str, int] = defaultdict(int)
        for q in graph[p]:
            counts[gid[member[q]]] += 1
        links = [
            {"group_id": g, "label": labels.get(g, g), "people": k}
            for g, k in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        rows["person_id"].append(p)
        rows["group_id"].append(own)
        rows["group_label"].append(labels.get(own, own))
        rows["group_size"].append(len(named[own]))
        rows["betweenness"].append(round(between[p], 6))
        rows["bridge"].append(between[p] >= max(cut, BRIDGE_MIN) and len(counts) >= 2)
        rows["links"].append(links)
    return pa.table(rows, schema=GROUP_SCHEMA)


__all__ = ["betweenness", "build_groups", "communities"]
