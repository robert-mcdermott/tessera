"""Live contract tests against the real public APIs. Skipped unless run with ``-m network``.

They check that each API still answers the way the connectors expect. They use small,
long-stable public records and never write outside a temporary data home.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.engine import Engine

from tessera.connectors.base import IngestOptions
from tessera.jobs.ingest import ingest
from tessera.lake import Lake
from tessera.ledger.db import make_engine

from .conftest import Instance

pytestmark = pytest.mark.network

STABLE_PMID = "7993409"  # a 1994 journal article
STABLE_NCT = "NCT04368728"  # a large, completed, widely cited trial


@pytest.fixture
def engine(instance: Instance) -> Engine:  # type: ignore[misc]
    engine = make_engine(instance.config.ledger_url)
    yield engine
    engine.dispose()


def test_pubmed_and_icite(instance: Instance, engine: Engine) -> None:
    result = ingest(
        instance.config, instance.home, engine, "pubmed", IngestOptions(ids=[STABLE_PMID])
    )
    assert result.status == "succeeded", result.report.to_json()
    metrics = ingest(
        instance.config, instance.home, engine, "icite", IngestOptions(ids=[STABLE_PMID])
    )
    assert metrics.status == "succeeded"
    with Lake(instance.home.lake) as lake:
        article = lake.rows("SELECT * FROM pubmed_article")[0]
        assert article["pmid"] == STABLE_PMID and article["pub_year"] == 1994
        assert lake.scalar("SELECT count(*) FROM pubmed_author") > 0
        assert lake.scalar("SELECT count(*) FROM pubmed_mesh") > 0
        assert lake.scalar("SELECT citation_count FROM icite_metrics") > 0


def test_pubmed_search(instance: Instance, engine: Engine) -> None:
    options = IngestOptions(
        query="hemoglobin[ti]", since=date(2024, 1, 1), until=date(2024, 12, 31), max_results=5
    )
    result = ingest(instance.config, instance.home, engine, "pubmed", options)
    assert result.report.total_found and result.report.total_found > 5
    assert result.status == "partial"  # capped on purpose


def test_reporter(instance: Instance, engine: Engine) -> None:
    query = '{"fiscal_years": [2024], "activity_codes": ["R35"], "agencies": ["NIGMS"]}'
    result = ingest(
        instance.config,
        instance.home,
        engine,
        "reporter",
        IngestOptions(query=query, max_results=3),
    )
    assert result.report.data_version
    with Lake(instance.home.lake) as lake:
        assert lake.scalar("SELECT count(*) FROM reporter_project") == 3
        assert lake.scalar("SELECT count(*) FROM reporter_investigator") >= 3
        assert lake.scalar("SELECT count(*) FROM reporter_publication_link") > 0


def test_ctgov(instance: Instance, engine: Engine) -> None:
    result = ingest(
        instance.config, instance.home, engine, "ctgov", IngestOptions(ids=[STABLE_NCT])
    )
    assert result.status == "succeeded", result.report.to_json()
    with Lake(instance.home.lake) as lake:
        study = lake.rows("SELECT * FROM ctgov_study")[0]
        assert study["nct_id"] == STABLE_NCT and study["overall_status"]
        assert lake.scalar("SELECT count(*) FROM ctgov_site") > 0


def test_mesh_descriptor_file() -> None:
    """NLM's file is still where the connector looks, compressed, and in the expected XML."""
    import zlib

    import httpx

    from tessera.connectors.mesh import MeshConnector, descriptors

    year = date.today().year
    with httpx.Client(
        headers={"Accept-Encoding": "identity", "User-Agent": "Tessera tests"}
    ) as client:
        response = client.get(
            f"{MeshConnector.base_url}desc{year}.gz", headers={"Range": "bytes=0-400000"}
        )
    assert response.status_code in (200, 206) and response.content[:2] == b"\x1f\x8b"
    text = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(response.content)
    cut = text.rfind(b"</DescriptorRecord>") + len(b"</DescriptorRecord>")
    head = text[:cut].split(b"\n", 2)[2] + b"\n</DescriptorRecordSet>"  # drop the XML and DTD lines
    records = list(descriptors(b'<?xml version="1.0"?>\n' + head, year))
    assert records and records[0]["descriptor_ui"] == "D000001" and records[0]["tree_numbers"]
