"""Source-specific historical collectors used by the dry-run pipeline."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


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
    frame["timestamp"] = pd.to_datetime(frame.pop("open_time"), unit="ms", utc=True)
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


def collect_dukascopy_candles(symbol: str, day: datetime.date, out: Path) -> Path:
    """Fetch one public Dukascopy M1 candle file; endpoint errors propagate."""
    url = (f"https://datafeed.dukascopy.com/datafeed/{symbol}/{day.year}/"
           f"{day.month - 1:02d}/{day.day:02d}/BID_candles_min_1.bi5")
    response = requests.get(url, timeout=60, headers={"User-Agent": "quant_lab-research/4.0"})
    response.raise_for_status()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dukascopy_{symbol}_{day.isoformat()}_BID_candles_min_1.bi5"
    path.write_bytes(response.content)
    return path
