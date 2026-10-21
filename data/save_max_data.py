"""Save maximum MT5 data using the working fetch function."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd


def save_max_data():
    """Save maximum XAUUSD data using working fetch function."""
    from data.fetch_extended import fetch_extended_mt5

    output_dir = Path("data/store/max")
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # Fetch H1 data (max 5000 bars per call)
    print("Fetching H1 data...")
    h1_data = fetch_extended_mt5("XAUUSD", "1h", 5000)
    if not h1_data.empty:
        filepath = output_dir / "XAUUSD_H1_MAX.parquet"
        h1_data.to_parquet(filepath)
        results["H1"] = {
            "bars": len(h1_data),
            "file": str(filepath),
            "start": str(h1_data.iloc[0]["timestamp"]),
            "end": str(h1_data.iloc[-1]["timestamp"]),
        }
        print(f"  Saved {len(h1_data)} H1 bars")

    # Fetch H4 data
    print("Fetching H4 data...")
    h4_data = fetch_extended_mt5("XAUUSD", "4h", 5000)
    if not h4_data.empty:
        filepath = output_dir / "XAUUSD_H4_MAX.parquet"
        h4_data.to_parquet(filepath)
        results["H4"] = {
            "bars": len(h4_data),
            "file": str(filepath),
            "start": str(h4_data.iloc[0]["timestamp"]),
            "end": str(h4_data.iloc[-1]["timestamp"]),
        }
        print(f"  Saved {len(h4_data)} H4 bars")

    # Fetch D1 data
    print("Fetching D1 data...")
    d1_data = fetch_extended_mt5("XAUUSD", "1d", 5000)
    if not d1_data.empty:
        filepath = output_dir / "XAUUSD_D1_MAX.parquet"
        d1_data.to_parquet(filepath)
        results["D1"] = {
            "bars": len(d1_data),
            "file": str(filepath),
            "start": str(d1_data.iloc[0]["timestamp"]),
            "end": str(d1_data.iloc[-1]["timestamp"]),
        }
        print(f"  Saved {len(d1_data)} D1 bars")

    return results


if __name__ == "__main__":
    results = save_max_data()
    print("\nSaved data:")
    for tf, info in results.items():
        print(f"  {tf}: {info['bars']} bars ({info['start'][:10]} to {info['end'][:10]})")
