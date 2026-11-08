"""MeSH: the annual descriptor file, the organization's vocabulary, and research areas."""

from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

import httpx
import pyarrow as pa
import pytest

from tessera.analytics.vocabulary import _areas, ancestors
from tessera.config import Config, load_config
from tessera.connectors.base import IngestOptions
from tessera.connectors.mesh import MeshConnector, descriptors

from .fakes import FakeApi
from .test_connectors import collect, make

FIXTURE = (Path(__file__).parent / "fixtures" / "mesh" / "desc-sample.xml").read_bytes()


@pytest.fixture
def config(config_file: Path) -> Config:
    return load_config(config_file, env={})


def test_descriptors_are_read_from_gzipped_or_plain_xml() -> None:
    for content in (FIXTURE, gzip.compress(FIXTURE)):
        rows = {r["name"]: r for r in descriptors(content, 2026)}
        assert set(rows) == {"Neoplasms", "Breast Neoplasms", "Humans"}
    neo = rows["Neoplasms"]
    assert neo["descriptor_ui"] == "D009369" and neo["tree_numbers"] == ["C04"]
    # Synonyms from every concept, once each; never the name itself or a permuted form.
    assert neo["entry_terms"] == ["Cancer", "Malignancy", "Tumors"]
    assert neo["scope_note"] == "New abnormal growth of tissue."
    assert (neo["mesh_year"], neo["last_updated"], neo["descriptor_class"]) == (
        2026,
        date(2024, 6, 12),
        1,
    )
    breast = rows["Breast Neoplasms"]
    assert breast["tree_numbers"] == ["C04.588.180", "C17.800.090.500"]
    assert breast["entry_terms"] == ["Breast Cancer"]  # systematic chemical names are left out
    assert rows["Humans"]["descriptor_class"] == 3


def mesh_api(files: dict[str, bytes]) -> FakeApi:
    """NLM's file server: known files, and an HTML page with a 200 status for the rest."""
    api = FakeApi()

    def serve(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name in files:
            return httpx.Response(200, content=files[name])
        return httpx.Response(200, text="<!doctype html><html><body>Page not found</body></html>")

    return api.route(".gz", serve)


def test_next_years_file_is_tried_from_november() -> None:
    connector = MeshConnector.__new__(MeshConnector)
    assert connector.plan(IngestOptions(), None, date(2026, 11, 8)).params["years"] == [2027, 2026]
    assert connector.plan(IngestOptions(), None, date(2026, 6, 1)).params["years"] == [2026]
    current = connector.plan(IngestOptions(), "2026", date(2026, 6, 1))
    assert current.empty and "current" in current.description
    assert connector.plan(IngestOptions(), "2026", date(2026, 12, 1)).params["years"] == [2027]


def test_an_unpublished_year_falls_back_and_sets_the_checkpoint(config: Config) -> None:
    api = mesh_api({"desc2026.gz": gzip.compress(FIXTURE)})
    connector = make(MeshConnector, config, api)
    plan = connector.plan(IngestOptions(), None, date(2026, 11, 8))
    payloads, report = collect(connector, plan)
    assert report.complete and len(payloads) == 1
    assert [r.url.path.rsplit("/", 1)[-1] for r in api.requests] == ["desc2027.gz", "desc2026.gz"]
    assert plan.new_watermark == "2026" and payloads[0].source_version == "MeSH 2026"
    assert len(MeshConnector.parse(payloads[0])["mesh"]) == 3
    # The file is fetched as it is, so it is kept compressed.
    assert all(r.headers["Accept-Encoding"] == "identity" for r in api.requests)


def test_nothing_newer_is_not_a_failure(config: Config) -> None:
    connector = make(MeshConnector, config, mesh_api({}))
    plan = connector.plan(IngestOptions(), "2025", date(2026, 3, 1))
    payloads, report = collect(connector, plan)
    assert payloads == [] and report.complete and plan.new_watermark is None
    assert any("No newer MeSH" in note for note in report.notes)


def test_ancestors() -> None:
    assert ancestors("C04.588.180") == ["C04", "C04.588"]
    assert ancestors("C04") == []


def test_research_areas_are_shares_weighed_by_how_few_share_them() -> None:
    by_ui = {
        "D1": {"name": "Breast Neoplasms", "tree_numbers": ["C04.588.180"]},
        "D2": {"name": "Neurons", "tree_numbers": ["A08.675", "A11.671"]},
        "D3": {"name": "Cells", "tree_numbers": ["A11"]},
        "D4": {"name": "Proteins", "tree_numbers": ["D12.776"]},  # molecules are not areas
        "C04": {"name": "Neoplasms", "tree_numbers": ["C04"]},
        "A08": {"name": "Nervous System", "tree_numbers": ["A08"]},
        "A11": {"name": "Cells", "tree_numbers": ["A11"]},
    }
    by_tree = {"C04": "C04", "A08": "A08", "A11": "A11"}
    profile = pa.table(
        {
            "person_id": ["p1", "p1", "p1", "p1", "p2"],
            "descriptor_ui": ["D1", "D2", "D3", "D4", "D3"],
            "descriptor": ["Breast Neoplasms", "Neurons", "Cells", "Proteins", "Cells"],
            "weight": [0.6, 0.4, 0.3, 0.6, 0.5],
        }
    )
    rows = _areas(profile, by_ui, by_tree).to_pylist()
    p1 = {r["area"]: r for r in rows if r["person_id"] == "p1"}
    assert set(p1) == {"Neoplasms", "Nervous System", "Cells"}  # no area for "Proteins"
    assert sum(r["share"] for r in p1.values()) == pytest.approx(1.0, abs=1e-3)
    # Only p1 holds anything under Neoplasms, so it leads; Cells, which both hold, counts less.
    assert max(p1.values(), key=lambda r: r["share"])["area"] == "Neoplasms"
    assert p1["Cells"]["share"] < p1["Nervous System"]["share"]
    assert p1["Neoplasms"]["concepts"] == ["Breast Neoplasms"]
