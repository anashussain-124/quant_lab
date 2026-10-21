"""Comprehensive Strategy Analyzer for XAUUSD + Prop Firm Compliance.

Tests all strategy families on maximum data, applies Funding Pips-style
compliance rules, and reports best survivors.
"""
from __future__ import annotations

import sys
import time
import json
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Data
from data.fetch_extended import fetch_extended_mt5
from engine.backtest_fixed import run_backtest_fixed


def load_all_data() -> dict[str, pd.DataFrame]:
    """Load maximum MT5 data from parquet or fetch fresh."""
    data = {}

    data_dir = Path("data/store/max")

    for tf_name in ["H1", "H4", "D1"]:
        filepath = data_dir / f"XAUUSD_{tf_name}_MAX.parquet"

        if filepath.exists():
            print(f"Loading {tf_name} from parquet...")
            df = pd.read_parquet(filepath)
            print(f"  Loaded {len(df)} bars ({df.iloc[0]['timestamp'].date()} to {df.iloc[-1]['timestamp'].date()})")
            data[tf_name] = df
        else:
            print(f"Fetching {tf_name}...")
            df = fetch_extended_mt5("XAUUSD", tf_name.lower(), 50000)
            if not df.empty:
                data[tf_name] = df
                print(f"  Got {len(df)} bars")

    return data


def build_comprehensive_strategies() -> dict[str, Any]:
    """Build all strategy configurations to test."""

    strategies = {}

    # ===== TREND FOLLOWING =====
    for lb in [10, 20, 30, 50]:
        strategies[f"donchian_{lb}"] = {
            "family": "trend",
            "function": "donchian",
            "params": {"lookback": lb},
            "description": f"Donchian breakout ({lb}-period)",
        }

    for fast in [5, 10, 20]:
        for slow in [30, 50, 100, 200]:
            if fast < slow:
                strategies[f"ema_cross_{fast}_{slow}"] = {
                    "family": "trend",
                    "function": "ema_cross",
                    "params": {"fast": fast, "slow": slow},
                    "description": f"EMA crossover ({fast}/{slow})",
                }

    # ===== MEAN REVERSION =====
    for lb in [20, 30, 50]:
        for z in [1.5, 2.0, 2.5, 3.0]:
            strategies[f"zscore_{lb}_{z}"] = {
                "family": "mean_reversion",
                "function": "zscore",
                "params": {"lookback": lb, "z_entry": z, "z_exit": z / 2},
                "description": f"Z-score mean reversion ({lb}-period, z={z})",
            }

    for lb in [20, 30, 50]:
        strategies[f"bollinger_{lb}"] = {
            "family": "mean_reversion",
            "function": "bollinger",
            "params": {"lookback": lb, "std_mult": 2.0},
            "description": f"Bollinger Band ({lb}-period, 2σ)",
        }

    # ===== VOLATILITY BREAKOUT =====
    for lb in [10, 20, 30]:
        for mult in [1.5, 2.0, 2.5, 3.0]:
            strategies[f"atr_breakout_{lb}_{mult}"] = {
                "family": "volatility",
                "function": "atr_breakout",
                "params": {"lookback": lb, "atr_mult": mult},
                "description": f"ATR breakout ({lb}-period, {mult}x ATR)",
            }

    # ===== MARKET STRUCTURE =====
    for lb in [20, 30, 50]:
        strategies[f"support_resistance_{lb}"] = {
            "family": "market_structure",
            "function": "support_resistance",
            "params": {"lookback": lb},
            "description": f"Support/Resistance ({lb}-period)",
        }

    return strategies


def generate_signals(strategy_config: dict[str, Any], data: pd.DataFrame) -> list[dict[str, Any]]:
    """Generate signals for a strategy on the given data."""
    func_name = strategy_config["function"]
    params = strategy_config["params"]

    if func_name == "donchian":
        return _donchian_signals(data, params["lookback"])
    elif func_name == "ema_cross":
        return _ema_cross_signals(data, params["fast"], params["slow"])
    elif func_name == "zscore":
        return _zscore_signals(data, params["lookback"], params["z_entry"], params["z_exit"])
    elif func_name == "bollinger":
        return _bollinger_signals(data, params["lookback"], params["std_mult"])
    elif func_name == "atr_breakout":
        return _atr_breakout_signals(data, params["lookback"], params["atr_mult"])
    elif func_name == "support_resistance":
        return _support_resistance_signals(data, params["lookback"])
    return []


def _donchian_signals(data: pd.DataFrame, lookback: int) -> list[dict[str, Any]]:
    """Generate Donchian breakout signals."""
    if len(data) < lookback + 10:
        return []

    close = data["close"].values
    high = data["high"].values
    low = data["low"].values
    timestamps = data["timestamp"].values
    signals = []

    for i in range(lookback, len(data)):
        h = float(np.max(high[i - lookback:i]))
        l = float(np.min(low[i - lookback:i]))

        if close[i] > h:
            signals.append({"timestamp": timestamps[i], "side": "BUY", "reason": "donchian_up"})
        elif close[i] < l:
            signals.append({"timestamp": timestamps[i], "side": "SELL", "reason": "donchian_down"})

    return signals


def _ema_cross_signals(data: pd.DataFrame, fast: int, slow: int) -> list[dict[str, Any]]:
    """Generate EMA crossover signals."""
    if len(data) < slow + 20:
        return []

    close = data["close"].values
    timestamps = data["timestamp"].values

    ema_fast = pd.Series(close).ewm(span=fast).mean().values
    ema_slow = pd.Series(close).ewm(span=slow).mean().values

    signals = []
    for i in range(slow, len(data)):
        if ema_fast[i] > ema_slow[i] and ema_fast[i - 1] <= ema_slow[i - 1]:
            signals.append({"timestamp": timestamps[i], "side": "BUY", "reason": "ema_cross_up"})
        elif ema_fast[i] < ema_slow[i] and ema_fast[i - 1] >= ema_slow[i - 1]:
            signals.append({"timestamp": timestamps[i], "side": "SELL", "reason": "ema_cross_down"})

    return signals


def _zscore_signals(data: pd.DataFrame, lookback: int, z_entry: float, z_exit: float) -> list[dict[str, Any]]:
    """Generate Z-score mean reversion signals."""
    if len(data) < lookback + 10:
        return []

    close = data["close"].values
    timestamps = data["timestamp"].values
    signals = []

    for i in range(lookback, len(data)):
        mean = float(np.mean(close[i - lookback:i]))
        std = float(np.std(close[i - lookback:i]))

        if std == 0:
            continue

        z = (close[i] - mean) / std

        if z > z_entry:
            signals.append({"timestamp": timestamps[i], "side": "SELL", "reason": "zscore_overbought"})
        elif z < -z_entry:
            signals.append({"timestamp": timestamps[i], "side": "BUY", "reason": "zscore_oversold"})

    return signals


def _bollinger_signals(data: pd.DataFrame, lookback: int, std_mult: float) -> list[dict[str, Any]]:
    """Generate Bollinger Band signals."""
    if len(data) < lookback + 10:
        return []

    close = data["close"].values
    timestamps = data["timestamp"].values
    signals = []

    for i in range(lookback, len(data)):
        mean = float(np.mean(close[i - lookback:i]))
        std = float(np.std(close[i - lookback:i]))

        upper = mean + std_mult * std
        lower = mean - std_mult * std

        if close[i] > upper:
            signals.append({"timestamp": timestamps[i], "side": "SELL", "reason": "bollinger_upper"})
        elif close[i] < lower:
            signals.append({"timestamp": timestamps[i], "side": "BUY", "reason": "bollinger_lower"})

    return signals


def _atr_breakout_signals(data: pd.DataFrame, lookback: int, atr_mult: float) -> list[dict[str, Any]]:
    """Generate ATR breakout signals."""
    if len(data) < lookback + 10:
        return []

    close = data["close"].values
    high = data["high"].values
    low = data["low"].values
    timestamps = data["timestamp"].values
    signals = []

    for i in range(lookback, len(data)):
        tr = np.maximum(high[i-lookback:i] - low[i-lookback:i],
                       np.abs(high[i-lookback:i] - close[i-lookback-1:i-1]))
        atr = float(np.mean(tr))

        upper = close[i - 1] + atr_mult * atr
        lower = close[i - 1] - atr_mult * atr

        if close[i] > upper:
            signals.append({"timestamp": timestamps[i], "side": "BUY", "reason": "atr_up"})
        elif close[i] < lower:
            signals.append({"timestamp": timestamps[i], "side": "SELL", "reason": "atr_down"})

    return signals


def _support_resistance_signals(data: pd.DataFrame, lookback: int) -> list[dict[str, Any]]:
    """Generate support/resistance signals."""
    if len(data) < lookback * 2:
        return []

    close = data["close"].values
    high = data["high"].values
    low = data["low"].values
    timestamps = data["timestamp"].values
    signals = []

    for i in range(lookback, len(data)):
        resistance = float(np.max(high[i - lookback:i]))
        support = float(np.min(low[i - lookback:i]))

        if close[i] >= resistance * 0.999 and close[i] <= resistance * 1.001:
            signals.append({"timestamp": timestamps[i], "side": "SELL", "reason": "resistance_reject"})
        elif close[i] <= support * 1.001 and close[i] >= support * 0.999:
            signals.append({"timestamp": timestamps[i], "side": "BUY", "reason": "support_bounce"})

    return signals


def compute_metrics(trades: list[dict], equity_curve: list, initial_capital: float) -> dict[str, float]:
    """Compute comprehensive performance metrics."""
    if not trades:
        return {"error": "No trades"}

    returns = np.array([t["return_pct"] for t in trades])
    pnls = np.array([t["pnl"] for t in trades])

    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    total_pnl = float(np.sum(pnls))
    total_return = (equity_curve[-1][1] / equity_curve[0][1] - 1) if equity_curve else 0.0

    win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0.0

    gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 1e-8
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    expectancy = float(np.mean(returns))

    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 1e-8
    sharpe = float(np.mean(returns) / std * np.sqrt(252)) if std > 0 else 0.0

    downside = returns[returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1e-8
    sortino = float(np.mean(returns) / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

    equities = np.array([e[1] for e in equity_curve])
    peaks = np.maximum.accumulate(equities)
    drawdowns = (equities - peaks) / peaks
    max_dd = float(np.min(drawdowns)) * 100 if len(drawdowns) > 0 else 0.0

    max_consec_loss = 0
    current_loss = 0
    for t in trades:
        if t["pnl"] < 0:
            current_loss += 1
            max_consec_loss = max(max_consec_loss, current_loss)
        else:
            current_loss = 0

    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

    avg_bars = float(np.mean([t["bars_held"] for t in trades])) if trades else 0.0

    # Days held
    total_days = sum(t["bars_held"] for t in trades) / 24.0  # H1 data

    return {
        "total_trades": len(trades),
        "total_pnl": total_pnl,
        "total_return_pct": total_return * 100,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": max_dd,
        "max_consecutive_losses": max_consec_loss,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_bars_held": avg_bars,
        "total_days_trading": total_days,
    }


def check_prop_firm_compliance(metrics: dict[str, float], firm: str = "funding_pips") -> tuple[bool, list[str]]:
    """Check if strategy passes prop firm compliance."""

    rules = {
        "funding_pips": {
            "min_trades": 10,
            "min_win_rate": 0.45,
            "min_profit_factor": 1.1,
            "max_drawdown_pct": 10.0,
            "min_sharpe": 0.3,
            "max_consec_losses": 8,
            "min_total_return_pct": 2.0,
        },
        "ftmo": {
            "min_trades": 10,
            "min_win_rate": 0.40,
            "min_profit_factor": 1.0,
            "max_drawdown_pct": 5.0,
            "min_sharpe": 0.2,
            "max_consec_losses": 6,
            "min_total_return_pct": 5.0,
        },
        "topstep": {
            "min_trades": 15,
            "min_win_rate": 0.45,
            "min_profit_factor": 1.2,
            "max_drawdown_pct": 8.0,
            "min_sharpe": 0.5,
            "max_consec_losses": 5,
            "min_total_return_pct": 3.0,
        },
    }

    rule = rules.get(firm, rules["funding_pips"])
    passes = []
    reasons = []

    if metrics["total_trades"] < rule["min_trades"]:
        reasons.append(f"Trades {metrics['total_trades']} < {rule['min_trades']}")
    else:
        passes.append("trade_count")

    if metrics["win_rate"] < rule["min_win_rate"]:
        reasons.append(f"Win rate {metrics['win_rate']:.1%} < {rule['min_win_rate']:.0%}")
    else:
        passes.append("win_rate")

    if metrics["profit_factor"] < rule["min_profit_factor"]:
        reasons.append(f"Profit factor {metrics['profit_factor']:.2f} < {rule['min_profit_factor']}")
    else:
        passes.append("profit_factor")

    if metrics["max_drawdown_pct"] > rule["max_drawdown_pct"]:
        reasons.append(f"Max DD {metrics['max_drawdown_pct']:.1f}% > {rule['max_drawdown_pct']}%")
    else:
        passes.append("max_drawdown")

    if metrics["sharpe_ratio"] < rule["min_sharpe"]:
        reasons.append(f"Sharpe {metrics['sharpe_ratio']:.2f} < {rule['min_sharpe']}")
    else:
        passes.append("sharpe")

    if metrics["max_consecutive_losses"] > rule["max_consec_losses"]:
        reasons.append(f"Max consec losses {metrics['max_consecutive_losses']} > {rule['max_consec_losses']}")
    else:
        passes.append("consec_losses")

    if metrics["total_return_pct"] < rule["min_total_return_pct"]:
        reasons.append(f"Total return {metrics['total_return_pct']:.1f}% < {rule['min_total_return_pct']}%")
    else:
        passes.append("total_return")

    return len(reasons) == 0, reasons


def main():
    print("="*80)
    print("  QUANT LAB — COMPREHENSIVE XAUUSD STRATEGY ANALYZER")
    print("  Target: Prop Firm Compliance (Funding Pips / FTMO / Topstep)")
    print("="*80)
    print()

    # 1. Load all data
    print("Loading data...")
    data_dict = load_all_data()
    if not data_dict:
        print("ERROR: No data loaded")
        return 1

    # Use H1 as primary (most bars for backtesting)
    h1_data = data_dict.get("H1")
    if h1_data is None or h1_data.empty:
        print("ERROR: No H1 data")
        return 1

    print(f"\nUsing H1 data: {len(h1_data)} bars ({h1_data.iloc[0]['timestamp'].date()} to {h1_data.iloc[-1]['timestamp'].date()})")

    # 2. Build strategies
    print("\nBuilding strategy matrix...")
    strategies = build_comprehensive_strategies()
    print(f"  Built {len(strategies)} strategy configurations")

    # 3. Split data for walk-forward
    n = len(h1_data)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)

    train_data = h1_data.iloc[:train_end]
    val_data = h1_data.iloc[train_end:val_end]
    test_data = h1_data.iloc[val_end:]

    print(f"\nWalk-forward split:")
    print(f"  Train: {len(train_data)} bars ({train_data.iloc[0]['timestamp'].date()} to {train_data.iloc[-1]['timestamp'].date()})")
    print(f"  Val: {len(val_data)} bars ({val_data.iloc[0]['timestamp'].date()} to {val_data.iloc[-1]['timestamp'].date()})")
    print(f"  Test: {len(test_data)} bars ({test_data.iloc[0]['timestamp'].date()} to {test_data.iloc[-1]['timestamp'].date()})")

    # 4. Test all strategies
    print("\nTesting all strategies...")
    results = []

    for i, (name, config) in enumerate(strategies.items()):
        if i % 20 == 0:
            print(f"  Testing {i+1}/{len(strategies)}...")

        try:
            # Generate signals for each split
            train_signals = generate_signals(config, train_data)
            val_signals = generate_signals(config, val_data)
            test_signals = generate_signals(config, test_data)

            # Run backtest on test set
            result = run_backtest_fixed(test_data, test_signals)

            if "error" not in result:
                metrics = result["metrics"]

                # Check prop firm compliance
                fp_pass, fp_reasons = check_prop_firm_compliance(metrics, "funding_pips")
                ftmo_pass, ftmo_reasons = check_prop_firm_compliance(metrics, "ftmo")
                top_pass, top_reasons = check_prop_firm_compliance(metrics, "topstep")

                results.append({
                    "name": name,
                    "family": config["family"],
                    "description": config["description"],
                    "metrics": metrics,
                    "fp_pass": fp_pass,
                    "ftmo_pass": ftmo_pass,
                    "top_pass": top_pass,
                    "train_signals": len(train_signals),
                    "val_signals": len(val_signals),
                    "test_signals": len(test_signals),
                })
        except Exception as e:
            pass  # Skip failed strategies

    # 5. Report results
    print(f"\n{'='*80}")
    print(f"  RESULTS")
    print(f"{'='*80}")

    # Sort by Sharpe ratio
    results.sort(key=lambda x: x["metrics"].get("sharpe_ratio", 0), reverse=True)

    # Top 10 by Sharpe
    print(f"\nTop 10 Strategies by Sharpe:")
    print(f"{'Strategy':<30} {'Family':<15} {'Trades':>6} {'WR':>6} {'PF':>6} {'Sharpe':>7} {'MaxDD':>7} {'Return':>7}")
    print("-" * 90)

    for r in results[:10]:
        m = r["metrics"]
        print(f"{r['name']:<30} {r['family']:<15} {m['total_trades']:>6} {m['win_rate']:>5.0%} {m['profit_factor']:>6.2f} {m['sharpe_ratio']:>7.2f} {m['max_drawdown_pct']:>6.1f}% {m['total_return_pct']:>6.1f}%")

    # Prop firm compliant
    print(f"\n\nProp Firm Compliant Strategies:")
    print("-" * 90)

    fp_survivors = [r for r in results if r["fp_pass"]]
    ftmo_survivors = [r for r in results if r["ftmo_pass"]]
    top_survivors = [r for r in results if r["top_pass"]]

    print(f"\nFunding Pips: {len(fp_survivors)} strategies pass")
    for r in fp_survivors:
        m = r["metrics"]
        print(f"  {r['name']:<30} Trades={m['total_trades']:>3} WR={m['win_rate']:.0%} PF={m['profit_factor']:.2f} Sharpe={m['sharpe_ratio']:.2f} DD={m['max_drawdown_pct']:.1f}% Return={m['total_return_pct']:.1f}%")

    print(f"\nFTMO: {len(ftmo_survivors)} strategies pass")
    for r in ftmo_survivors:
        m = r["metrics"]
        print(f"  {r['name']:<30} Trades={m['total_trades']:>3} WR={m['win_rate']:.0%} PF={m['profit_factor']:.2f} Sharpe={m['sharpe_ratio']:.2f} DD={m['max_drawdown_pct']:.1f}% Return={m['total_return_pct']:.1f}%")

    print(f"\nTopstep: {len(top_survivors)} strategies pass")
    for r in top_survivors:
        m = r["metrics"]
        print(f"  {r['name']:<30} Trades={m['total_trades']:>3} WR={m['win_rate']:.0%} PF={m['profit_factor']:.2f} Sharpe={m['sharpe_ratio']:.2f} DD={m['max_drawdown_pct']:.1f}% Return={m['total_return_pct']:.1f}%")

    # Save results
    output_path = Path("data/store/max/strategy_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
