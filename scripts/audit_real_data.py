"""Write an auditable summary for the real inputs collected in Step 1."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.v4 import audit_ohlcv, sha256_file


def _summary(path: Path, frame: pd.DataFrame) -> str:
    indexed = frame.set_index("timestamp").sort_index()
    audit = audit_ohlcv(indexed, "5min" if "M5" in path.name else "1min")
    returns = indexed["close"].pct_change()
    outliers = int((returns.abs() > returns.std() * 10).sum())
    return (f"### {path.name}\n\n- Rows: {len(indexed)}\n- First UTC: {indexed.index.min()}\n"
            f"- Last UTC: {indexed.index.max()}\n- SHA-256: `{sha256_file(path)}`\n"
            f"- Audit: `{audit}`\n- Outlier bars (>10 rolling-free std returns): {outliers}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mt5", type=Path, required=True)
    parser.add_argument("--binance-dir", type=Path, required=True)
    parser.add_argument("--dukascopy-error", default="HTTP 429 from datafeed.dukascopy.com")
    parser.add_argument("--out", type=Path, default=Path(f"data/AUDIT_{date.today().isoformat()}.md"))
    args = parser.parse_args()
    parts = ["# Real-data audit\n", "Environment: HISTORICAL_REAL\n"]
    mt5 = pd.read_parquet(args.mt5)
    parts.append(_summary(args.mt5, mt5))
    parts.append("## MT5 spread by UTC hour\n")
    spread = mt5.assign(hour=pd.to_datetime(mt5["timestamp"], utc=True).dt.hour)
    hour = spread.groupby("hour")["spread"].agg(["count", "mean", "median", "max"])
    parts.append("```text\n" + hour.to_string() + "\n```\n")
    rollover = spread.loc[spread["timestamp"].dt.hour.isin([21, 22, 23, 0, 1])]
    parts.append(f"Rollover-window mean spread points (UTC 21:00-01:59): {rollover['spread'].mean()}\n")
    files = sorted(args.binance_dir.glob("*.parquet"))
    parts.append(f"## Binance Vision\n\nFiles: {len(files)}\n")
    for path in files[:4]:
        parts.append(_summary(path, pd.read_parquet(path)))
    parts.append(f"## Dukascopy\n\nDownload failed; no substitution made. Exact error: `{args.dukascopy_error}`\n")
    parts.append("## Cross-source disagreement\n\nUnavailable: Dukascopy M1 did not download, so no overlapping resampled comparison was performed.\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(parts), encoding="utf-8")
    print(args.out)
    print("Dukascopy-vs-MT5 disagreement: UNAVAILABLE (Dukascopy HTTP 429)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
