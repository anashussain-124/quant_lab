"""Source-specific historical collectors used by the dry-run pipeline."""

from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


def binance_epoch_to_datetime(values: pd.Series) -> pd.Series:
    """Decode Binance millisecond or microsecond epochs by magnitude."""
    numeric = pd.to_numeric(values)
    unit = "us" if int(numeric.max()) >= 10**13 else "ms"
    return pd.to_datetime(numeric, unit=unit, utc=True)


def collect_binance_month(symbol: str, year: int, month: int, out: Path) -> Path:
    """Download one Binance Vision spot-kline month and save hashed Parquet."""
    ym = f"{year:04d}-{month:02d}"
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
    response = requests.get(url, timeout=60, headers={"User-Agent": "quant_lab-research/4.0"})
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
               "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    frame = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])), header=None, names=columns)
    raw_open_time = frame.pop("open_time")
    # Binance Vision changed some archives from millisecond to microsecond
    # epoch values. Detect the unit from magnitude and never silently shift it.
    frame["timestamp"] = binance_epoch_to_datetime(raw_open_time)
    frame = frame[["timestamp", "open", "high", "low", "close", "volume", "trades"]]
    payload = frame.to_parquet(index=False)
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"binance_{symbol}_1m_{ym}_{digest[:16]}.parquet"
    path.write_bytes(payload)
    return path


def collect_mt5_m5(symbol: str, start: datetime, end: datetime, out: Path) -> Path:
    """Fetch MT5 M5 bars only after verifying the connected account is demo."""
    import MetaTrader5 as mt5
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("MT5 collection requires timezone-aware UTC bounds")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        info = mt5.account_info()
        if info is None or info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError(f"MT5 demo verification failed: {info}")
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol selection failed: {symbol}")
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 returned no M5 data: {mt5.last_error()}")
        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame.pop("time"), unit="s", utc=True)
        payload = frame.to_parquet(index=False)
        import hashlib
        digest = hashlib.sha256(payload).hexdigest()
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"mt5_{symbol}_M5_{digest[:16]}.parquet"
        path.write_bytes(payload)
        return path
    finally:
        mt5.shutdown()


def collect_dukascopy_candles(symbol: str, day: datetime.date, out: Path, *,
                              retries: int = 4, min_delay_seconds: float = 2.0,
                              request_state: dict[str, int] | None = None,
                              daily_request_cap: int = 500) -> Path:
    """Fetch one public Dukascopy file with bounded retry and resumable output."""
    url = (f"https://datafeed.dukascopy.com/datafeed/{symbol}/{day.year}/"
           f"{day.month - 1:02d}/{day.day:02d}/BID_candles_min_1.bi5")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dukascopy_{symbol}_{day.isoformat()}_BID_candles_min_1.bi5"
    if path.exists() and path.stat().st_size > 0:
        return path
    state = request_state if request_state is not None else {"count": 0}
    if state.get("count", 0) >= daily_request_cap:
        raise RuntimeError(f"Dukascopy daily request cap reached: {daily_request_cap}")
    headers = {"User-Agent": "quant_lab-research/4.0"}
    last = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(max(min_delay_seconds, min_delay_seconds * (2 ** (attempt - 1))))
        state["count"] = state.get("count", 0) + 1
        response = requests.get(url, timeout=60, headers=headers)
        last = response
        if response.ok:
            path.write_bytes(response.content)
            return path
        if response.status_code not in {429, 500, 502, 503, 504}:
            response.raise_for_status()
    assert last is not None
    raise RuntimeError(
        f"Dukascopy request failed after {retries + 1} attempts: status={last.status_code}; "
        f"body={last.text!r}; headers={dict(last.headers)!r}"
    )
