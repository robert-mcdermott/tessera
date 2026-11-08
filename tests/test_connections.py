"""Groups and bridges, missed connections, and the unit matrix (DIS-08, DIS-09)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from tessera.analytics.concepts import _write, profile_path, similarity_path
from tessera.analytics.connections import missed_connections, unit_matrix, unit_pairs
from tessera.analytics.graph import collaboration_path
from tessera.analytics.groups import betweenness, build_groups, communities
from tessera.home import DataHome
from tessera.lake import Lake

from .test_discovery import Org, ok, org  # noqa: F401  (the synthetic organization)

# --- groups and bridges ------------------------------------------------------------------------


def two_teams() -> dict[str, dict[str, float]]:
    """Two tight teams of five, joined only through "bridge"."""
    graph: dict[str, dict[str, float]] = {}

    def link(a: str, b: str, w: float = 1.0) -> None:
        graph.setdefault(a, {})[b] = w
        graph.setdefault(b, {})[a] = w

    for team in ("a", "b"):
        members = [f"{team}{i}" for i in range(5)]
        for i, x in enumerate(members):
            for y in members[i + 1 :]:
                link(x, y, 3.0)
    link("bridge", "a0")
    link("bridge", "a1")
    link("bridge", "b0")
    link("bridge", "b1")
    return graph


def test_communities_find_the_teams() -> None:
    member = communities(two_teams())
    assert len({member[f"a{i}"] for i in range(5)}) == 1
    assert len({member[f"b{i}"] for i in range(5)}) == 1
    assert member["a0"] != member["b0"]
    assert communities(two_teams()) == member  # the same graph, the same groups


def test_the_bridge_is_on_the_most_shortest_paths() -> None:
    score = betweenness(two_teams())
    assert max(score, key=score.get) == "bridge"
    assert all(0 <= v <= 1 for v in score.values())
    assert score["a4"] == 0  # inside a team, everyone is one step from everyone


def collaboration_table(graph: dict[str, dict[str, float]]) -> pa.Table:
    rows = [(a, b, w) for a, nbrs in graph.items() for b, w in nbrs.items() if a < b]
    return pa.table(
        {
            "person_id": [r[0] for r in rows],
            "other_id": [r[1] for r in rows],
            "strength": [r[2] for r in rows],
        }
    )


def test_groups_are_named_by_what_their_members_hold(tmp_path: Path) -> None:
    profile = tmp_path / "profile.parquet"
    people = [f"a{i}" for i in range(5)] + [f"b{i}" for i in range(5)] + ["bridge"]
    _write(
        pa.table(
            {
                "person_id": [p for p in people for _ in range(3)],
                "descriptor": [
                    d
                    for p in people
                    for d in (
                        ("Sleep", "Sleep Stages", "Neurons")
                        if p.startswith("a")
                        else ("Malaria", "Mosquito Vectors", "Neurons")
                    )
                ],
                "weight": [0.6, 0.3, 0.1] * len(people),
            }
        ),
        profile,
    )
    rows = build_groups(collaboration_table(two_teams()), profile).to_pylist()
    by_person = {r["person_id"]: r for r in rows}
    # "Sleep and Sleep Stages" says one thing twice; the label takes the next concept.
    assert by_person["a2"]["group_label"] == "Sleep and Neurons"
    assert by_person["b2"]["group_label"].startswith("Malaria")
    assert by_person["a2"]["group_size"] == 5 or by_person["a2"]["group_size"] == 6
    bridge = by_person["bridge"]
    assert bridge["bridge"] and len(bridge["links"]) == 2
    assert {link["people"] for link in bridge["links"]} == {2}
    assert not any(by_person[p]["bridge"] for p in people if p != "bridge")


# --- missed connections and the unit matrix, on hand-made files ------------------------------


@pytest.fixture
def small(tmp_path: Path) -> DataHome:
    """Five people: a-b alike and never together (c worked with both), a-c together,
    d-e alike and together, a-e alike but e is a former member."""
    home = DataHome(tmp_path / "var")
    home.create()
    home.derived.mkdir(parents=True, exist_ok=True)
    shared = [{"descriptor_ui": "D1", "descriptor": "Sleep", "contribution": 0.3}]
    _write(
        pa.table({"person_id": ["a"], "descriptor_ui": ["D1"], "weight": [1.0]}), profile_path(home)
    )
    pairs = [("a", "b", 0.6), ("d", "e", 0.5), ("a", "e", 0.4), ("b", "d", 0.2)]
    _write(
        pa.table(
            {
                "person_id": [x for a, b, _ in pairs for x in (a, b)],
                "other_id": [x for a, b, _ in pairs for x in (b, a)],
                "score": [s for *_, s in pairs for _ in (0, 1)],
                "shared": [shared] * (2 * len(pairs)),
            }
        ),
        similarity_path(home),
    )
    together = [("a", "c", 2.0), ("b", "c", 1.0), ("d", "e", 4.0)]
    _write(
        pa.table(
            {
                "person_id": [a for a, _, _ in together],
                "other_id": [b for _, b, _ in together],
                "papers": [2, 1, 4],
                "confirmed_papers": [2, 1, 4],
                "grants": [0, 0, 0],
                "studies": [0, 0, 0],
                "strength": [w for *_, w in together],
                "first_year": [2020, 2021, 2019],
                "last_year": [2024, 2021, 2025],
                "recent": [["1", "2"], ["3"], ["4", "5", "6", "7"]],
            }
        ),
        collaboration_path(home),
    )
    return home


PEOPLE = {
    "a": {"status": "active", "units": ["Sleep Lab"]},
    "b": {"status": "active", "units": ["Neurology"]},
    "c": {"status": "active", "units": ["Neurology"]},
    "d": {"status": "active", "units": ["Neurology"]},
    "e": {"status": "former", "units": ["Sleep Lab"]},
}


def test_missed_connections_are_alike_current_and_never_together(small: DataHome) -> None:
    with Lake(small.lake) as lake:
        found = missed_connections(lake, small, PEOPLE, min_similarity=0.1)
        # a-b: alike, never together. d-e: together. a-e: e has left. b-d: alike enough.
        assert [(i["a"], i["b"]) for i in found["items"]] == [("a", "b"), ("b", "d")]
        assert found["items"][0]["introducers"] == ["c"]  # worked with both
        assert found["items"][0]["shared"][0]["descriptor"] == "Sleep"
        assert missed_connections(lake, small, PEOPLE, min_similarity=0.25)["total"] == 1
        across = missed_connections(lake, small, PEOPLE, min_similarity=0.1, across=True)
        assert [(i["a"], i["b"]) for i in across["items"]] == [("a", "b")]
        assert missed_connections(lake, small, PEOPLE, 0.1, unit="Sleep Lab")["total"] == 1
        assert (
            missed_connections(lake, small, PEOPLE, 0.1, limit=1, offset=1)["items"][0]["b"] == "d"
        )


def test_the_unit_matrix_counts_pairs_as_densities(small: DataHome) -> None:
    members = {"Sleep Lab": {"a", "e"}, "Neurology": {"b", "c", "d"}, "Solo": {"z"}}
    with Lake(small.lake) as lake:
        result = unit_matrix(lake, small, members, min_similarity=0.25)
    assert result["available"] == 2  # "Solo" has one member
    cells = {(c["a"], c["b"]): c for c in result["cells"]}
    across = cells[("Neurology", "Sleep Lab")]
    # 3 x 2 possible pairs; a-b alike (0.6) and missed; a-c together; e-d alike and together.
    assert across["possible"] == 6
    assert across["similar_pairs"] == 2 and across["similarity"] == pytest.approx(1.1 / 6, abs=1e-4)
    assert across["collaborating_pairs"] == 2 and across["missed"] == 1
    within = cells[("Neurology", "Neurology")]
    assert within["possible"] == 3 and within["collaborating_pairs"] == 1  # b-c
    assert within["similar_pairs"] == 1 and within["missed"] == 0  # b-d is below 0.25
    assert cells[("Sleep Lab", "Sleep Lab")]["possible"] == 1
    # Ordered by interest density: Sleep Lab 0.4 within + 1.1/6 across; Neurology 0.2/3 + 1.1/6.
    assert [u["name"] for u in result["units"]] == ["Sleep Lab", "Neurology"]


def test_a_cell_opens_its_pairs(small: DataHome) -> None:
    with Lake(small.lake) as lake:
        pairs = unit_pairs(lake, small, {"a", "e"}, {"b", "c", "d"})
    keyed = {(p["a"], p["b"]): p for p in pairs}
    assert set(keyed) == {("a", "b"), ("a", "c"), ("e", "d")}
    assert keyed[("a", "b")]["together"] is None and keyed[("a", "b")]["score"] == 0.6
    assert keyed[("a", "c")]["score"] is None and keyed[("a", "c")]["together"]["papers"] == 2
    assert pairs[0]["score"] == 0.6  # most alike first


# --- the API, on the synthetic organization --------------------------------------------------


def test_network_names_groups_and_bridges(org: Org) -> None:  # noqa: F811
    body = ok(org.client.get("/api/v1/graph/network"))
    nodes, meta = body["data"]["nodes"], body["meta"]
    assert meta["groups"] and all(n["cluster"] for n in nodes)
    assert sum(g["people"] for g in meta["groups"]) == len(nodes)
    flagged = sorted(n["label"] for n in nodes if n["bridge"])
    assert [b["name"] for b in meta["bridges"]] == flagged  # listed by name, not ranked
    for b in meta["bridges"]:
        assert len(b["groups"]) >= 2


def test_missed_connections_endpoint(org: Org) -> None:  # noqa: F811
    body = ok(org.client.get("/api/v1/graph/missed", params={"min_similarity": 0.1}))
    assert body["meta"]["ready"] and body["meta"]["total"] == len(body["data"])
    # In this world everyone alike already works together.
    assert body["data"] == []


def test_unit_matrix_endpoint(org: Org) -> None:  # noqa: F811
    body = ok(org.client.get("/api/v1/graph/units"))
    units, cells = body["data"]["units"], body["data"]["cells"]
    assert len(units) >= 2 and body["meta"]["available"] == len(units)
    n = len(units)
    assert len(cells) == n * (n + 1) // 2
    for c in cells:
        assert 0 <= c["similarity"] <= 1 and 0 <= c["collaboration"] <= 1
        assert c["collaborating_pairs"] <= c["possible"] and c["similar_pairs"] <= c["possible"]
    a, b = units[0]["name"], units[1]["name"]
    pairs = ok(org.client.get("/api/v1/graph/units/pairs", params={"a": a, "b": b}))
    assert pairs["meta"]["units"] == [a, b]
    cell = next(c for c in cells if {c["a"], c["b"]} == {a, b})
    assert pairs["meta"]["total"] >= max(cell["similar_pairs"], cell["collaborating_pairs"])
    assert all(p["a"]["name"] and p["b"]["name"] for p in pairs["data"])
    assert (
        org.client.get("/api/v1/graph/units/pairs", params={"a": a, "b": "Nowhere"}).status_code
        == 404
    )


def test_similar_people_say_whether_they_worked_together(org: Org) -> None:  # noqa: F811
    person = next(n["id"] for n in ok(org.client.get("/api/v1/graph/network"))["data"]["nodes"])
    similar = ok(org.client.get(f"/api/v1/people/{person}/similar"))["data"]
    assert similar and all("together" in p for p in similar)
    assert any(p["together"] and p["together"]["papers"] >= 1 for p in similar)
