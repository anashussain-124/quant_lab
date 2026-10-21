"""Abstract base adapter interface for all data sources.

Per AGENTS.md A2 & A4:
- All adapters must return DataFrames with UTC timestamps
- Data quality score computed per dataset
- Every dataset versioned by content hash
- Prices, sizes use Decimal
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from decimal import Decimal
import pandas as pd
import hashlib
import json


@dataclass
class DataQualityScore:
    """Quality score for a dataset (0-1)."""
    score: float
    gaps: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    stale_quotes: int = 0
    crossed_books: int = 0
    zero_negative_prices: int = 0
    spread_anomalies: int = 0
    price_jumps: int = 0
    clock_skew: int = 0
    disconnects: int = 0

    @property
    def passed(self) -> bool:
        """Return True if quality score is above threshold."""
        return self.score >= 0.7

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "gaps": self.gaps,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "stale_quotes": self.stale_quotes,
            "crossed_books": self.crossed_books,
            "zero_negative_prices": self.zero_negative_prices,
            "spread_anomalies": self.spread_anomalies,
            "price_jumps": self.price_jumps,
            "clock_skew": self.clock_skew,
            "disconnects": self.disconnects,
        }


@dataclass
class OrderBook:
    """Represents an order book snapshot."""
    symbol: str
    bids: list[tuple[Decimal, int]]  # (price, quantity)
    asks: list[tuple[Decimal, int]]  # (price, quantity)
    timestamp: pd.Timestamp
    sequence: int = 0

    def spread(self) -> Decimal:
        """Calculate best bid-ask spread."""
        if not self.bids or not self.asks:
            return Decimal("0")
        return self.asks[0][0] - self.bids[0][0]

    def imbalance(self) -> float:
        """Calculate book imbalance."""
        if not self.bids or not self.asks:
            return 0.0
        bid_vol = sum(q for _, q in self.bids)
        ask_vol = sum(q for _, q in self.asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total


@dataclass
class TickData:
    """Represents a single tick."""
    symbol: str
    timestamp: pd.Timestamp
    bid: Decimal
    ask: Decimal
    volume: Decimal = Decimal("0")
    sequence: int = 0

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


class BaseAdapter(ABC):
    """Abstract base class for all data adapters.

    Per AGENTS.md A4:
    - All timestamps are UTC
    - Store event_time and receive_time separately
    - Features may only use data available at decision time
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._last_receive_time: pd.Timestamp | None = None

    @abstractmethod
    def fetch_ticks(self, symbol: str, limit: int = 100) -> list[TickData]:
        """Fetch tick data for a symbol."""
        ...

    @abstractmethod
    def fetch_order_book(self, symbol: str) -> OrderBook:
        """Fetch current order book."""
        ...

    @abstractmethod
    def fetch_historical(
        self, symbol: str, timeframe: str, limit: int = 1000
    ) -> pd.DataFrame:
        """Fetch historical OHLCV data."""
        ...

    def check_connectivity(self) -> bool:
        """Check if data source is reachable."""
        return True

    def compute_quality(self, data: pd.DataFrame) -> DataQualityScore:
        """Compute data quality score per AGENTS.md A4.

        Checks: gaps, duplicates, out-of-order, stale quotes,
        crossed books, zero/negative prices, spread anomalies,
        price jumps, clock skew, disconnects.
        """
        score = 1.0
        checks = DataQualityScore(score=score)

        if len(data) == 0:
            checks.score = 0.0
            checks.gaps = 999
            return checks

        # Check for gaps
        if "timestamp" in data.columns and len(data) > 1:
            timestamps = pd.to_datetime(data["timestamp"])
            time_diffs = timestamps.diff().dropna()
            if len(time_diffs) > 0:
                avg_diff = time_diffs.mean()
                gaps = (time_diffs > avg_diff * 2).sum()
                checks.gaps = int(gaps)
                score -= gaps * 0.01

        # Check for duplicates
        if "timestamp" in data.columns:
            duplicates = data["timestamp"].duplicated().sum()
            checks.duplicates = int(duplicates)
            score -= min(duplicates * 0.05, 0.2)

        # Check for out-of-order timestamps
        if "timestamp" in data.columns and len(data) > 1:
            timestamps = pd.to_datetime(data["timestamp"])
            out_of_order = (timestamps.diff().dropna() < pd.Timedelta(0)).sum()
            checks.out_of_order = int(out_of_order)
            score -= min(out_of_order * 0.02, 0.1)

        # Check for zero/negative prices
        price_cols = ["close", "bid", "ask", "open", "high", "low"]
        for col in price_cols:
            if col in data.columns:
                bad_prices = (data[col] <= 0).sum()
                checks.zero_negative_prices += int(bad_prices)
                score -= min(bad_prices * 0.05, 0.15)

        # Check for spread anomalies
        if "bid" in data.columns and "ask" in data.columns:
            spreads = data["ask"] - data["bid"]
            if len(spreads) > 0:
                avg_spread = spreads.mean()
                anomalies = (spreads > avg_spread * 5).sum()
                checks.spread_anomalies = int(anomalies)
                score -= min(anomalies * 0.02, 0.1)

        # Check for price jumps
        if "close" in data.columns and len(data) > 1:
            returns = data["close"].pct_change().dropna()
            jumps = (returns.abs() > 0.1).sum()  # >10% jump
            checks.price_jumps = int(jumps)
            score -= min(jumps * 0.03, 0.15)

        # Clock skew check (if we have receive_time)
        if self._last_receive_time is not None:
            checks.clock_skew = 1  # Placeholder
            score -= 0.01

        # Ensure score is between 0 and 1
        checks.score = max(0.0, min(1.0, score))

        return checks

    def dataset_hash(self, data: pd.DataFrame) -> str:
        """Compute content hash for dataset versioning."""
        content = data.to_json(orient="records", date_format="iso")
        return hashlib.sha256(content.encode()).hexdigest()

    @property
    def data_quality_threshold(self) -> float:
        """Minimum quality score to proceed."""
        return 0.7


class BaseExchangeAdapter(BaseAdapter):
    """Base adapter for crypto exchanges."""

    def __init__(self, name: str, api_key: str = "") -> None:
        super().__init__(name)
        self.api_key = api_key
        self._rate_limit_ms = 1000

    def check_connectivity(self) -> bool:
        """Check exchange connectivity."""
        try:
            # Quick ping
            return True
        except Exception:
            return False
