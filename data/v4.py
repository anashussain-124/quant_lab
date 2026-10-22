"""Real-source data collection, normalization, hashing, and audit helpers.

Collectors only download or adapt external data. They never fabricate bars.
All source timestamps are normalized at the boundary and event/receive times
remain separate in the manifest.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
import requests


@dataclass(frozen=True)
class SourceSpec:
    name: str
    url: str
    symbol: str
    timeframe: str
    terms_url: str


@dataclass(frozen=True)
class ManifestRecord:
    source: str
    symbol: str
    timeframe: str
    path: str
    sha256: str
    rows: int
    event_start_utc: str
    event_end_utc: str
    received_at_utc: str
    environment: str = "HISTORICAL_REAL"


PUBLIC_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("binance_vision", "https://data.binance.vision/", "BTCUSDT", "1m", "https://www.binance.com/en/terms"),
    SourceSpec("dukascopy", "https://datafeed.dukascopy.com/", "XAUUSD", "tick", "https://www.dukascopy.com/swiss/english/legal/terms-and-conditions/"),
    SourceSpec("fred", "https://fred.stlouisfed.org/", "DTWEXBGS", "daily", "https://fred.stlouisfed.org/terms-of-use/"),
    SourceSpec("cftc", "https://publicreporting.cftc.gov/", "GOLD_COT", "weekly", "https://www.cftc.gov/Legal/index.htm"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_utc(frame: pd.DataFrame, timestamp_column: str, broker_utc_offset: int | None = None) -> pd.DataFrame:
    """Normalize timestamps to UTC without silently guessing broker offsets."""
    if timestamp_column not in frame:
        raise ValueError(f"missing timestamp column: {timestamp_column}")
    out = frame.copy()
    timestamps = pd.to_datetime(out.pop(timestamp_column), errors="raise")
    if timestamps.dt.tz is None:
        if broker_utc_offset is None:
            raise ValueError("naive timestamps require an explicit broker_utc_offset")
        timestamps = timestamps.dt.tz_localize("UTC") - pd.Timedelta(hours=broker_utc_offset)
    else:
        timestamps = timestamps.dt.tz_convert("UTC")
    out.index = pd.DatetimeIndex(timestamps, name="event_time_utc")
    return out.sort_index(kind="stable")


def audit_ohlcv(frame: pd.DataFrame, expected_interval: str | None = None) -> dict[str, Any]:
    required = {"open", "high", "low", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    invalid = ((frame[list(required)] <= 0).any(axis=1) | (frame.high < frame.low) |
               (frame.open < frame.low) | (frame.open > frame.high) |
               (frame.close < frame.low) | (frame.close > frame.high))
    gaps = 0
    if expected_interval:
        gaps = int((frame.index.to_series().diff().dropna() > pd.Timedelta(expected_interval)).sum())
    return {
        "rows": int(len(frame)),
        "duplicate_timestamps": int(frame.index.duplicated().sum()),
        "out_of_order": not frame.index.is_monotonic_increasing,
        "invalid_ohlc_rows": int(invalid.sum()),
        "missing_values": int(frame[list(required)].isna().sum().sum()),
        "gaps": gaps,
        "timezone": str(frame.index.tz),
        "environment": "HISTORICAL_REAL",
    }


def partition_parquet(frame: pd.DataFrame, root: Path, symbol: str, timeframe: str) -> list[ManifestRecord]:
    if frame.index.tz is None or str(frame.index.tz) != "UTC":
        raise ValueError("partition_parquet requires a UTC-aware index")
    records: list[ManifestRecord] = []
    for year, part in frame.groupby(frame.index.year):
        path = root / f"symbol={symbol}" / f"timeframe={timeframe}" / f"year={year}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.to_parquet(path, index=True)
        records.append(ManifestRecord(
            source="partitioned_store", symbol=symbol, timeframe=timeframe,
            path=str(path), sha256=sha256_file(path), rows=len(part),
            event_start_utc=part.index[0].isoformat(), event_end_utc=part.index[-1].isoformat(),
            received_at_utc=datetime.now(timezone.utc).isoformat(),
        ))
    return records


class ResumableDownloader:
    def __init__(self, session: requests.Session | None = None, retries: int = 3, sleep: Callable[[float], None] = time.sleep) -> None:
        self.session = session or requests.Session()
        self.retries = retries
        self.sleep = sleep
        self.session.headers.update({"User-Agent": "quant-lab-research/0.4 (contact: research-only)"})

    def download(self, url: str, destination: Path, expected_sha256: str | None = None) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        for attempt in range(self.retries + 1):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                response = self.session.get(url, headers=headers, stream=True, timeout=60)
                response.raise_for_status()
                mode = "ab" if offset and response.status_code == 206 else "wb"
                with partial.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                partial.replace(destination)
                if expected_sha256 and sha256_file(destination) != expected_sha256:
                    raise ValueError("download checksum mismatch")
                return destination
            except (requests.RequestException, OSError, ValueError):
                if attempt >= self.retries:
                    raise
                self.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


def write_manifest(path: Path, records: Iterable[ManifestRecord]) -> None:
    payload = [asdict(record) for record in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
