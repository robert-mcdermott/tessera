"""The lake: normalized public-source records in Parquet, read with DuckDB.

See spec/decisions/0003-storage-ledger-lake-derived.md.
"""

from tessera.lake.reader import Lake
from tessera.lake.schemas import DATASETS, TABLES
from tessera.lake.writer import LakeWriter, WriteStats

__all__ = ["DATASETS", "TABLES", "Lake", "LakeWriter", "WriteStats"]
