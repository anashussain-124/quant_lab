"""Scrape maximum XAUUSD data from MT5 demo.

Uses multiple timeframes to get maximum historical coverage.
"""
from __future__ import annotations

import time
import sys
from typing import Any

import pandas as pd
import numpy as np


def get_max_mt5_data(
    symbol: str = "XAUUSD",
    max_bars_per_request: int = 10000,
) -> dict[str, pd.DataFrame]:
    """Get maximum historical data for XAUUSD across multiple timeframes."""
    import MetaTrader5 as MT5

    initialized = MT5.initialize()
    if not initialized:
        print("Failed to initialize MT5")
        return {}

    # Verify demo
    info = MT5.account_info()
    if info is None or info.trade_mode != 0:
        MT5.shutdown()
        return {}

    # Select symbol
    MT5.symbol_select(symbol, True)

    timeframes = {
        "M1": MT5.TIMEFRAME_M1,
        "M5": MT5.TIMEFRAME_M5,
        "M15": MT5.TIMEFRAME_M15,
        "M30": MT5.TIMEFRAME_M30,
        "H1": MT5.TIMEFRAME_H1,
        "H4": MT5.TIMEFRAME_H4,
        "D1": MT5.TIMEFRAME_D1,
        "W1": MT5.TIMEFRAME_W1,
        "MN1": MT5.TIMEFRAME_MN1,
    }

    results = {}

    for name, tf in timeframes.items():
        print(f"Fetching {symbol} {name}...")

        # Try to get maximum bars
        bars = MT5.copy_rates_from_pos(symbol, tf, 0, max_bars_per_request)

        if bars is None or len(bars) == 0:
            print(f"  No data for {name}")
            continue

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(bars["time"], unit="s", utc=True),
            "open": bars["open"],
            "high": bars["high"],
            "low": bars["low"],
            "close": bars["close"],
            "volume": bars["tick_volume"],
        })
        df = df.drop_duplicates(subset=["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        results[name] = df
        print(f"  Got {len(df)} bars ({df.iloc[0]['timestamp'].date()} to {df.iloc[-1]['timestamp'].date()})")

        # Rate limit
        time.sleep(0.2)

    MT5.shutdown()
    return results


def get_all_data_as_1h(symbol: str = "XAUUSD") -> pd.DataFrame:
    """Get maximum data as 1H candles (best for strategy testing)."""
    import MetaTrader5 as MT5

    initialized = MT5.initialize()
    if not initialized:
        return pd.DataFrame()

    info = MT5.account_info()
    if info is None or info.trade_mode != 0:
        MT5.shutdown()
        return pd.DataFrame()

    MT5.symbol_select(symbol, True)

    # Try to get max H1 bars
    bars = MT5.copy_rates_from_pos(symbol, MT5.TIMEFRAME_H1, 0, 50000)

    MT5.shutdown()

    if bars is None or len(bars) == 0:
        return pd.DataFrame()

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(bars["time"], unit="s", utc=True),
        "open": bars["open"],
        "high": bars["high"],
        "low": bars["low"],
        "close": bars["close"],
        "volume": bars["tick_volume"],
    })
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def get_data_summary(data: pd.DataFrame) -> dict[str, Any]:
    """Get summary of data."""
    if data.empty:
        return {"error": "No data"}

    return {
        "total_bars": len(data),
        "start_date": str(data.iloc[0]["timestamp"]),
        "end_date": str(data.iloc[-1]["timestamp"]),
        "years": (data.iloc[-1]["timestamp"] - data.iloc[0]["timestamp"]).days / 365.25,
        "highest": float(data["high"].max()),
        "lowest": float(data["low"].min()),
        "current": float(data["close"].iloc[-1]),
        "nulls": int(data.isnull().sum().sum()),
        "duplicates": int(data.duplicated(subset=["timestamp"]).sum()),
    }


if __name__ == "__main__":
    print("="*60)
    print("  XAUUSD DATA SCRAPER — MAXIMUM HISTORICAL")
    print("="*60)

    # Get all timeframes
    all_data = get_max_mt5_data("XAUUSD")

    print("\n" + "="*60)
    print("  DATA SUMMARY")
    print("="*60)

    for tf, df in all_data.items():
        if not df.empty:
            summary = get_data_summary(df)
            print(f"\n{tf}: {summary['total_bars']} bars, {summary['years']:.1f} years")
            print(f"  Range: {summary['start_date'][:10]} to {summary['end_date'][:10]}")
            print(f"  Price range: ${summary['lowest']:,.2f} - ${summary['highest']:,.2f}")
            print(f"  Current: ${summary['current']:,.2f}")
