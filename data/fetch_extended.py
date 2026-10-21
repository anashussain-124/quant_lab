"""Fetch extended historical data for meaningful backtests."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import numpy as np

from adapters.crypto import CCXTAdapter
from adapters.mt5 import MetaTrader5Adapter


def fetch_extended_crypto(
    exchange: str = "binance",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    target_rows: int = 2000,
) -> pd.DataFrame:
    """Fetch extended historical data using CCXT pagination."""
    import ccxt

    exchange_class = getattr(ccxt, exchange, None)
    if exchange_class is None:
        raise ValueError(f"Unsupported exchange: {exchange}")

    adapter = exchange_class({"enableRateLimit": True})

    all_data: list[list] = []
    since = None
    remaining = target_rows

    while remaining > 0:
        try:
            limit = min(remaining, 1000)
            batch = adapter.fetch_ohlcv(symbol, timeframe, limit=limit, since=since)

            if not batch or len(batch) == 0:
                break

            all_data.extend(batch)

            # Move to next batch (use last timestamp + 1ms to avoid duplicates)
            last_ts = batch[-1][0]
            since = last_ts + 1
            remaining -= len(batch)

            # Rate limit
            time.sleep(adapter.rateLimit / 1000)

        except Exception as e:
            print(f"Error fetching batch: {e}")
            break

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(
        all_data,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def fetch_extended_mt5(
    symbol: str = "XAUUSD",
    timeframe: str = "1h",
    target_rows: int = 2000,
) -> pd.DataFrame:
    """Fetch extended MT5 historical data."""
    import MetaTrader5 as MT5

    initialized = MT5.initialize()
    if not initialized:
        return pd.DataFrame()

    # Verify demo
    info = MT5.account_info()
    if info is None or info.trade_mode != 0:
        MT5.shutdown()
        return pd.DataFrame()

    # Select symbol
    MT5.symbol_select(symbol, True)

    tf_map = {
        "1m": MT5.TIMEFRAME_M1,
        "5m": MT5.TIMEFRAME_M5,
        "15m": MT5.TIMEFRAME_M15,
        "30m": MT5.TIMEFRAME_M30,
        "1h": MT5.TIMEFRAME_H1,
        "4h": MT5.TIMEFRAME_H4,
        "1d": MT5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe, MT5.TIMEFRAME_H1)

    # Fetch all at once
    rates = MT5.copy_rates_from_pos(symbol, tf, 0, target_rows)

    MT5.shutdown()

    if rates is None or len(rates) == 0:
        return pd.DataFrame()

    # MT5 returns structured numpy array - convert properly
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(rates["time"], unit="s", utc=True),
        "open": rates["open"],
        "high": rates["high"],
        "low": rates["low"],
        "close": rates["close"],
        "volume": rates["tick_volume"],
    })
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df
