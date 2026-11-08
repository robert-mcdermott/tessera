"""Connectors to public data sources. See spec/data-sources.md."""

from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan
from tessera.connectors.ctgov import CtgovConnector
from tessera.connectors.icite import ICiteConnector
from tessera.connectors.pubmed import PubMedConnector
from tessera.connectors.reporter import ReporterConnector

CONNECTORS: dict[str, type[Connector]] = {
    c.name: c for c in (PubMedConnector, ICiteConnector, ReporterConnector, CtgovConnector)
}

__all__ = ["CONNECTORS", "Connector", "FetchReport", "IngestOptions", "Payload", "Plan"]
