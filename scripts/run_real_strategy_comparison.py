"""Compare legacy and causal execution for a registered research strategy.

This is a historical/backtest-only script. It never connects to MT5 and never
places orders. The legacy path is preserved for reproducibility; the causal
path generates a signal at bar close and fills at the next bar open.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.research_runner import ExperimentConfig, ResearchRunner  # noqa: E402
from strategies import get_strategy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--strategy", default="momentum_breakout_real")
    parser.add_argument("--out", type=Path, default=Path("reports/real_strategy_comparison.json"))
    parser.add_argument("--taker-fee", type=float, default=0.0004)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--spread-bps", type=float, default=1.0)
    parser.add_argument("--max-bars", type=int, default=0, help="Optional deterministic tail limit for smoke runs")
    args = parser.parse_args()

    data = pd.read_parquet(args.parquet).copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp", kind="stable").drop_duplicates("timestamp")
    if args.max_bars:
        data = data.tail(args.max_bars).reset_index(drop=True)
    if "symbol" not in data.columns:
        data["symbol"] = "XAUUSD"
    if not {"open", "high", "low", "close", "timestamp"}.issubset(data.columns):
        raise SystemExit("input must contain timestamp, open, high, low, close")

    start = data["timestamp"].min()
    end = data["timestamp"].max()
    span = end - start
    train_end = start + span * 0.60
    val_end = start + span * 0.80
    base = {
        "experiment_id": "real_strategy_execution_comparison",
        "hypothesis": "Causal next-open execution removes same-bar fill optimism",
        "strategy_name": args.strategy,
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "train_start": start.isoformat(),
        "train_end": train_end.isoformat(),
        "val_start": train_end.isoformat(),
        "val_end": val_end.isoformat(),
        "test_start": val_end.isoformat(),
        "test_end": (end + pd.Timedelta(minutes=5)).isoformat(),
        "cost_model": {
            "taker_fee": args.taker_fee,
            "slippage_bps": args.slippage_bps,
            "spread_bps": args.spread_bps,
        },
    }
    runner = ResearchRunner(data)
    strategy = get_strategy(args.strategy)
    if strategy is None:
        raise SystemExit(f"unknown strategy: {args.strategy}")

    outputs: dict[str, object] = {
        "environment": "BACKTEST",
        "dataset": str(args.parquet),
        "strategy": args.strategy,
        "costs": base["cost_model"],
        "warning": "Generic ResearchRunner is not wired to the FundingPips V3 challenge evaluator.",
    }
    for mode in ("legacy_same_close", "causal_next_open"):
        config = ExperimentConfig(**base, execution_mode=mode)
        result = runner.run_experiment(config)
        outputs[mode] = result.to_dict()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(outputs, indent=2, default=str), encoding="utf-8")
    print(json.dumps(outputs, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
