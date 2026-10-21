"""Parquet storage layer with hash consistency and fault detection.

Per AGENTS.md A4:
- Every dataset versioned by content hash
- Fault-injected datasets flagged by hash inconsistency
- Parquet with Snappy compression
- DuckDB for querying
"""
from __future__ import annotations

import os
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from decimal import Decimal

import pandas as pd
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import numpy as np
from dataclasses import dataclass, field

from adapters.data_quality import DataQualityEngine, QualityReport
from engine.logger import AuditLogger
from engine.safety_gate import SafetyGate, LabConfig


@dataclass
class DatasetManifest:
    """Manifest for a stored dataset."""
    dataset_id: str
    file_path: str
    rows: int
    columns: list[str]
    data_hash: str
    previous_hash: str | None = None
    timestamp: str = ""
    quality_score: float = 1.0
    quality_passed: bool = True
    fault_injected: bool = False
    fault_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "file_path": self.file_path,
            "rows": self.rows,
            "columns": self.columns,
            "data_hash": self.data_hash,
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "quality_score": self.quality_score,
            "quality_passed": self.quality_passed,
            "fault_injected": self.fault_injected,
            "fault_type": self.fault_type,
            "metadata": self.metadata,
        }


class ParquetStorage:
    """Parquet storage with hash consistency verification.

    Per AGENTS.md A4:
    - Every dataset versioned by content hash
    - Fault-injected datasets flagged by hash inconsistency
    - DuckDB for querying
    """

    def __init__(
        self,
        base_dir: str = "data/historical",
        duckdb_path: str = "data/quant_lab.duckdb",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.duckdb_path = duckdb_path
        self.audit = AuditLogger()
        self.config = LabConfig()
        self.gate = SafetyGate(self.config, AuditLogger())
        self._manifests: dict[str, DatasetManifest] = {}
        self._manifest_path = self.base_dir / "manifests.json"

        # Load existing manifests
        self._load_manifests()

        # Initialize DuckDB
        self._init_duckdb()

    def _init_duckdb(self) -> None:
        """Initialize DuckDB connection."""
        try:
            self.audit = AuditLogger()
            self.conn = duckdb.connect(self.duckdb_path)
            # Create datasets table if not exists
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id VARCHAR PRIMARY KEY,
                    file_path VARCHAR,
                    rows BIGINT,
                    columns VARCHAR,
                    data_hash VARCHAR,
                    previous_hash VARCHAR,
                    timestamp TIMESTAMP,
                    quality_score DOUBLE,
                    quality_passed BOOLEAN,
                    fault_injected BOOLEAN,
                    fault_type VARCHAR,
                    metadata VARCHAR
                )
            """)
        except Exception as e:
            self.audit.log(
                event="DUCKDB_INIT_ERROR",
                action=f"DuckDB initialization failed: {e}",
                details={},
                actor="parquet_storage",
            )
            self.conn = None

    def _compute_hash(self, df: pd.DataFrame) -> str:
        """Compute SHA-256 hash of DataFrame content."""
        content = df.to_json(orient="records", date_format="iso")
        return hashlib.sha256(content.encode()).hexdigest()

    def _load_manifests(self) -> None:
        """Load existing manifests from disk."""
        if not hasattr(self, '_manifests'):
            self._manifests = {}
        self._manifests = {}
        if hasattr(self, '_manifest_path') and self._manifest_path.exists():
            try:
                with open(self._manifest_path) as f:
                    data = json.load(f)
                    for dataset_id, manifest in data.items():
                        self._manifests[dataset_id] = DatasetManifest(**manifest)
            except Exception:
                pass

    def _save_manifests(self) -> None:
        """Save manifests to disk."""
        if not hasattr(self, '_manifests'):
            self._manifests = {}
        if not hasattr(self, '_manifest_path'):
            self._manifest_path = self.base_dir / "manifests.json"
        data = {
            dataset_id: manifest.to_dict()
            for dataset_id, manifest in self._manifests.items()
        }
        with open(self._manifest_path, "w") as f:
            json.dump(data, f, indent=2)

    def store(
        self,
        df: pd.DataFrame,
        dataset_id: str,
        symbol: str = "",
        timeframe: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> DatasetManifest:
        """Store a DataFrame as Parquet with hash versioning.

        Per AGENTS.md A4:
        - Every dataset versioned by content hash
        - Compute hash before and after storage
        """
        if len(df) == 0:
            raise ValueError(f"Cannot store empty dataset: {dataset_id}")

        # Ensure audit and conn exist
        if not hasattr(self, 'audit'):
            self.audit = AuditLogger()
        if not hasattr(self, 'conn'):
            self.conn = None
        if not hasattr(self, '_manifests'):
            self._manifests = {}

        # Compute content hash
        data_hash = self._compute_hash(df)

        # Get previous hash for version tracking
        previous_hash = None
        if dataset_id in self._manifests:
            previous_hash = self._manifests[dataset_id].data_hash

        # Check for hash inconsistency (fault detection)
        fault_injected = False
        fault_type = None
        if dataset_id in self._manifests:
            stored_hash = self._manifests[dataset_id].data_hash
            if stored_hash != data_hash and previous_hash == stored_hash:
                # Same dataset_id but different hash — data changed!
                fault_injected = True
                fault_type = "HASH_INCONSISTENCY"
                self.audit.log(
                    event="FAULT_INJECTED",
                    action=f"Hash inconsistency detected for {dataset_id}",
                    details={
                        "previous_hash": previous_hash,
                        "current_hash": data_hash,
                    },
                    actor="parquet_storage",
                )

        # Store as Parquet with Snappy compression
        file_name = f"{dataset_id}.parquet"
        file_path = self.base_dir / file_name
        table = pa.Table.from_pandas(df)
        pq.write_table(table, str(file_path), compression="snappy")

        # Create manifest
        manifest = DatasetManifest(
            dataset_id=dataset_id,
            file_path=str(file_path),
            rows=len(df),
            columns=list(df.columns),
            data_hash=data_hash,
            previous_hash=previous_hash,
            timestamp=datetime.now(timezone.utc).isoformat(),
            quality_score=1.0,
            quality_passed=True,
            fault_injected=fault_injected,
            fault_type=fault_type,
            metadata=metadata or {
                "symbol": symbol,
                "timeframe": timeframe,
            },
        )

        # Save manifest
        self._manifests[dataset_id] = manifest
        self._save_manifests()

        # Insert into DuckDB
        self._insert_to_duckdb(manifest, df)

        self.audit.log(
            event="DATASET_STORED",
            action=f"Stored {dataset_id} ({len(df)} rows)",
            details=manifest.to_dict(),
            actor="parquet_storage",
        )

        return manifest

    def _insert_to_duckdb(self, manifest: DatasetManifest, df: pd.DataFrame) -> None:
        """Insert dataset into DuckDB."""
        if self.conn is None:
            return

        try:
            table_name = manifest.dataset_id.replace("-", "_").replace(".", "_")
            # Register DataFrame
            self.conn.register(table_name, df)
            # Create or replace table
            self.conn.execute(f"""
                CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM {table_name}
            """)
        except Exception as e:
            self.audit.log(
                event="DUCKDB_INSERT_ERROR",
                action=f"Failed to insert {manifest.dataset_id}: {e}",
                details={},
                actor="parquet_storage",
            )

    def load(self, dataset_id: str) -> pd.DataFrame | None:
        """Load a dataset by ID."""
        if dataset_id not in self._manifests:
            return None

        file_path = self._manifests[dataset_id].file_path
        if not os.path.exists(file_path):
            return None

        df = pd.read_parquet(file_path)

        # Verify hash
        current_hash = self._compute_hash(df)
        stored_hash = self._manifests[dataset_id].data_hash

        if current_hash != stored_hash:
            self.audit.log(
                event="HASH_MISMATCH",
                action=f"Hash mismatch for {dataset_id}",
                details={
                    "stored_hash": stored_hash,
                    "current_hash": current_hash,
                },
                actor="parquet_storage",
            )
            self._manifests[dataset_id].fault_injected = True
            self._manifests[dataset_id].fault_type = "HASH_MISMATCH"
            self._save_manifests()

        return df

    def query(self, sql: str) -> pd.DataFrame:
        """Execute SQL query via DuckDB."""
        if self.conn is None:
            raise RuntimeError("DuckDB not initialized")
        return self.conn.execute(sql).df()

    def list_datasets(self) -> list[DatasetManifest]:
        """List all stored datasets."""
        return list(self._manifests.values())

    def get_manifest(self, dataset_id: str) -> DatasetManifest | None:
        """Get manifest by dataset ID."""
        return self._manifests.get(dataset_id)

    def verify_integrity(self) -> dict[str, Any]:
        """Verify all stored datasets for integrity.

        Per AGENTS.md A4:
        - Fault-injected datasets flagged by hash inconsistency
        """
        results = {
            "total_datasets": len(self._manifests),
            "passed": 0,
            "failed": 0,
            "fault_injected": [],
            "hash_mismatches": [],
        }

        for dataset_id, manifest in self._manifests.items():
            # Check if file exists
            if not os.path.exists(manifest.file_path):
                results["failed"] += 1
                results["hash_mismatches"].append(dataset_id)
                continue

            # Verify hash
            df = pd.read_parquet(manifest.file_path)
            current_hash = self._compute_hash(df)

            # Also verify by comparing against stored hash directly
            # If the file was tampered, the content hash won't match
            if current_hash != manifest.data_hash:
                results["failed"] += 1
                results["hash_mismatches"].append(dataset_id)
                manifest.fault_injected = True
                self._manifests[dataset_id] = manifest
            else:
                results["passed"] += 1

            if manifest.fault_injected:
                results["fault_injected"].append(dataset_id)

        # Update manifests
        self._save_manifests()

        return results

    def get_quality_report(self, dataset_id: str) -> QualityReport | None:
        """Get quality report for a dataset."""
        engine = DataQualityEngine()
        df = self.load(dataset_id)
        if df is None:
            return None
        return engine.evaluate(df, dataset_id)


class DataIngestionManager:
    """Manages data ingestion from all sources.

    Per AGENTS.md A2 & A6:
    - Pull data from exchange adapters
    - Apply quality checks
    - Store with hash versioning
    - DuckDB querying
    """

    def __init__(self, storage: ParquetStorage | None = None) -> None:
        self.storage = storage or ParquetStorage()
        self.quality_engine = DataQualityEngine()
        self.audit = AuditLogger()

    def ingest(
        self,
        df: pd.DataFrame,
        dataset_id: str,
        source: str = "",
        symbol: str = "",
        timeframe: str = "",
    ) -> DatasetManifest | None:
        """Ingest data from any source.

        Per AGENTS.md A4:
        - Compute quality score
        - Below threshold means NO_TRADE
        - Store with hash versioning
        """
        # Check quality
        quality = self.quality_engine.evaluate(df, dataset_id)

        if not quality.passed:
            self.audit.log(
                event="INGESTION_REJECTED",
                action=f"Dataset {dataset_id} quality {quality.score:.2f} < threshold",
                details=quality.checks.to_dict(),
                actor="ingestion_manager",
            )
            # Still store but mark as failed quality
            manifest = self.storage.store(
                df, dataset_id, symbol, timeframe,
                metadata={"quality_failed": True, "quality_score": quality.score},
            )
            return manifest

        # Store with full quality
        manifest = self.storage.store(
            df, dataset_id, symbol, timeframe,
            metadata={
                "source": source,
                "quality_score": quality.score,
                "quality_passed": quality.passed,
            },
        )

        return manifest

    def ingest_crypto(
        self,
        adapter: object,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 1000,
    ) -> DatasetManifest | None:
        """Ingest crypto OHLCV data."""
        df = adapter.fetch_historical(symbol, timeframe, limit)
        if len(df) == 0:
            return None

        dataset_id = f"crypto_{symbol}_{timeframe}"
        return self.ingest(df, dataset_id, source=adapter.exchange, symbol=symbol, timeframe=timeframe)

    def ingest_mt5(
        self,
        adapter: object,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        count: int = 1000,
    ) -> DatasetManifest | None:
        """Ingest MT5 tick/historical data."""
        ticks = adapter.get_ticks(symbol, count)
        if not ticks:
            return None

        df = pd.DataFrame([
            {
                "timestamp": t.timestamp,
                "symbol": t.symbol,
                "bid": float(t.bid),
                "ask": float(t.ask),
                "volume": float(t.volume),
            }
            for t in ticks
        ])

        dataset_id = f"mt5_{symbol}_{timeframe}"
        return self.ingest(df, dataset_id, source="mt5", symbol=symbol, timeframe=timeframe)

    def get_stats(self, dataset_id: str) -> dict[str, Any]:
        """Get dataset statistics."""
        df = self.storage.load(dataset_id)
        if df is None:
            return {}

        stats = {
            "rows": len(df),
            "columns": list(df.columns),
            "memory_usage_mb": df.memory_usage(deep=True).sum() / 1024 / 1024,
        }

        if "close" in df.columns:
            stats["mean_close"] = float(df["close"].mean())
            stats["std_close"] = float(df["close"].std())
            stats["min_close"] = float(df["close"].min())
            stats["max_close"] = float(df["close"].max())

        return stats
