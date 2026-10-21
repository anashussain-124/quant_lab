"""Data layer for the Quant Research Lab.

Per AGENTS.md A2 & A4:
- Ingestion from all sources
- Quality checks per dataset
- DuckDB querying
- Parquet storage with hash versioning
"""
from data.storage import ParquetStorage, DatasetManifest, DataIngestionManager

__all__ = ["ParquetStorage", "DatasetManifest", "DataIngestionManager"]
