"""Real data pipeline with validation and persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from adapters.crypto import CCXTAdapter, BinanceAdapter
from adapters.mt5 import MetaTrader5Adapter
from adapters import DataQualityScore
from engine.logger import AuditLogger


@dataclass
class DataSource:
    """Data source configuration."""
    name: str
    adapter: str  # "ccxt" or "mt5"
    exchange: str = ""
    symbol: str = ""
    timeframe: str = "1h"
    enabled: bool = True


@dataclass
class PipelineResult:
    """Result of a pipeline run."""
    source: str
    symbol: str
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    quality: DataQualityScore = field(default_factory=lambda: DataQualityScore(score=0))
    success: bool = False
    error: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "symbol": self.symbol,
            "success": self.success,
            "error": self.error,
            "rows": len(self.data),
            "quality_score": self.quality.score,
            "quality_passed": self.quality.passed,
            "timestamp": self.timestamp.isoformat(),
        }


class DataPipeline:
    """Real data pipeline with validation and persistence."""

    def __init__(self, storage_path: str = "data/store") -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.audit = AuditLogger()
        self._adapters: dict[str, Any] = {}

    def get_adapter(self, source: DataSource) -> Any:
        """Get or create adapter for a data source."""
        key = f"{source.adapter}:{source.exchange}:{source.symbol}"

        if key not in self._adapters:
            if source.adapter == "ccxt":
                self._adapters[key] = BinanceAdapter() if source.exchange == "binance" else CCXTAdapter(source.exchange)
            elif source.adapter == "mt5":
                adapter = MetaTrader5Adapter()
                adapter.initialize()
                self._adapters[key] = adapter

        return self._adapters[key]

    def fetch_and_validate(self, source: DataSource) -> PipelineResult:
        """Fetch data from source and validate."""
        result = PipelineResult(source=source.name, symbol=source.symbol)

        try:
            adapter = self.get_adapter(source)

            if source.adapter == "ccxt":
                df = adapter.fetch_historical(source.symbol, source.timeframe)
            elif source.adapter == "mt5":
                df = adapter.fetch_historical(source.symbol, source.timeframe)
            else:
                result.error = f"Unknown adapter: {source.adapter}"
                return result

            if df.empty:
                result.error = "No data returned"
                return result

            quality = adapter.compute_quality(df)
            result.data = df
            result.quality = quality
            result.success = True

            if not quality.passed:
                self.audit.log(
                    event="DATA_QUALITY_LOW",
                    action=f"Quality {quality.score:.2f} for {source.symbol}",
                    details=quality.to_dict(),
                    actor="data_pipeline",
                )

        except Exception as e:
            result.error = str(e)
            self.audit.log(
                event="PIPELINE_ERROR",
                action=f"Failed to fetch {source.symbol}: {e}",
                details={"source": source.name},
                actor="data_pipeline",
            )

        return result

    def save_to_parquet(self, result: PipelineResult, tag: str = "") -> Path:
        """Save validated data to Parquet."""
        if not result.success or result.data.empty:
            return Path()

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{result.source}_{result.symbol.replace('/', '_')}_{tag}_{ts}.parquet"
        filepath = self.storage_path / filename

        self.storage_path.mkdir(parents=True, exist_ok=True)
        result.data.to_parquet(filepath, index=False)

        self.audit.log(
            event="DATA_SAVED",
            action=f"Saved {len(result.data)} rows to {filename}",
            details={
                "filepath": str(filepath),
                "rows": len(result.data),
                "quality": result.quality.score,
            },
            actor="data_pipeline",
        )

        return filepath

    def run_pipeline(self, sources: list[DataSource]) -> list[PipelineResult]:
        """Run pipeline for multiple sources."""
        results = []
        for source in sources:
            if not source.enabled:
                continue
            result = self.fetch_and_validate(source)
            results.append(result)
            if result.success:
                self.save_to_parquet(result)
        return results
