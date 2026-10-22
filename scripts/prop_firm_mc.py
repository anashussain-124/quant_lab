"""Bootstrap Monte Carlo for prop-firm survival; no execution is performed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def _block_path(samples: np.ndarray, length: int, block_size: int, rng: np.random.Generator) -> np.ndarray:
    blocks = []
    while sum(len(block) for block in blocks) < length:
        start = int(rng.integers(0, len(samples)))
        blocks.append(np.take(samples, (start + np.arange(block_size)) % len(samples)))
    return np.concatenate(blocks)[:length]


def simulate(
    samples: np.ndarray,
    risk: float,
    target: float,
    max_dd: float,
    daily_limit: float,
    trades_day: int,
    min_days: int,
    rules: bool,
    block: bool = False,
    runs: int = 10_000,
    seed: int = 11,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    passed = daily_breach = dd_breach = 0
    days_to_pass: list[int] = []
    for _ in range(runs):
        path = (
            _block_path(samples, 365 * trades_day, 5, rng)
            if block
            else rng.choice(samples, size=365 * trades_day, replace=True)
        )
        cursor = 0
        equity = 1.0
        peak = 1.0
        trading_days = 0
        daily_loss = 0.0
        for day in range(1, 366):
            if rules and daily_loss >= daily_limit * 0.5:
                daily_loss = 0.0
                continue
            daily_loss = 0.0
            day_trades = min(trades_day, 2 if rules else trades_day)
            for value in path[cursor : cursor + day_trades]:
                cursor += 1
                realized = float(value)
                if rules:
                    realized = max(realized, -1.0)  # tightened stop cannot loosen beyond 1R
                pnl = risk * realized
                equity += pnl
                daily_loss = max(daily_loss, -pnl)
                peak = max(peak, equity)
                if daily_loss >= daily_limit:
                    daily_breach += 1
                    break
                if equity <= 1.0 - max_dd:
                    dd_breach += 1
                    break
            trading_days += 1
            if equity >= 1.0 + target and trading_days >= min_days:
                passed += 1
                days_to_pass.append(day)
                break
            if daily_loss >= daily_limit or equity <= 1.0 - max_dd:
                break
    return {
        "pass_probability": passed / runs,
        "daily_loss_breach_probability": daily_breach / runs,
        "max_drawdown_breach_probability": dd_breach / runs,
        "median_days_to_pass": float(np.median(days_to_pass)) if days_to_pass else float("nan"),
    }


def analytical(mu: float, std: float, risk: float, target: float, max_dd: float) -> float:
    if std <= 0:
        return 1.0 if mu > 0 else 0.0
    a, b = max_dd / risk, target / risk
    if abs(mu) < 1e-12:
        return b / (a + b)
    return float((1 - np.exp(-2 * mu * b / (std * std))) / (1 - np.exp(-2 * mu * (a + b) / (std * std))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--risk", type=float, default=0.0025)
    parser.add_argument("--target", type=float, default=0.08)
    parser.add_argument("--max-drawdown", type=float, default=0.10)
    parser.add_argument("--daily-loss", type=float, default=0.05)
    parser.add_argument("--trades-per-day", type=int, default=3)
    parser.add_argument("--min-trading-days", type=int, default=3)
    args = parser.parse_args()
    import pandas as pd

    values = pd.read_csv(args.trades)["r"].dropna().to_numpy(float)
    if len(values) < 2:
        raise SystemExit("Need at least two realized R observations")
    print(
        json.dumps(
            {
                "assumptions": {
                    "rules_source": "docs/FINAL_BACKTEST_PROTOCOL.md",
                    "target": args.target,
                    "max_drawdown": args.max_drawdown,
                    "daily_loss": args.daily_loss,
                    "block_size": 5,
                },
                "iid": {
                    "without_rules": simulate(
                        values,
                        args.risk,
                        args.target,
                        args.max_drawdown,
                        args.daily_loss,
                        args.trades_per_day,
                        args.min_trading_days,
                        False,
                    ),
                    "with_rules": simulate(
                        values,
                        args.risk,
                        args.target,
                        args.max_drawdown,
                        args.daily_loss,
                        args.trades_per_day,
                        args.min_trading_days,
                        True,
                    ),
                },
                "block_bootstrap": {
                    "without_rules": simulate(
                        values,
                        args.risk,
                        args.target,
                        args.max_drawdown,
                        args.daily_loss,
                        args.trades_per_day,
                        args.min_trading_days,
                        False,
                        True,
                    ),
                    "with_rules": simulate(
                        values,
                        args.risk,
                        args.target,
                        args.max_drawdown,
                        args.daily_loss,
                        args.trades_per_day,
                        args.min_trading_days,
                        True,
                        True,
                    ),
                },
                "analytical_benchmark": analytical(
                    float(values.mean()) * args.risk,
                    float(values.std(ddof=1)) * args.risk,
                    args.risk,
                    args.target,
                    args.max_drawdown,
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
