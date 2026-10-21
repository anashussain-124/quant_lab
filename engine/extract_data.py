"""Extract useful data for strategy development."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.fetch_extended import fetch_extended_mt5
from engine.regime import create_strategy_matrix


def main():
    print("="*80)
    print("  QUANT LAB — STRATEGY DATA EXTRACTION")
    print("="*80)

    # Load H1 data
    data = pd.read_parquet("data/store/max/XAUUSD_H1_MAX.parquet")
    n = len(data)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)
    train_data = data.iloc[:train_end]
    val_data = data.iloc[train_end:val_end]
    test_data = data.iloc[val_end:]

    print(f"\nData: {len(data)} H1 bars ({data.iloc[0]['timestamp'].date()} to {data.iloc[-1]['timestamp'].date()})")
    print(f"Train: {len(train_data)} | Val: {len(val_data)} | Test: {len(test_data)}")

    # Add UTC hour
    data_copy = data.copy()
    data_copy["utc_hour"] = data_copy["timestamp"].apply(lambda x: pd.Timestamp(x).hour)
    data_copy["day_of_week"] = data_copy["timestamp"].apply(lambda x: pd.Timestamp(x).dayofweek)

    # ===== 1. SESSION ANALYSIS =====
    print("\n" + "="*80)
    print("  1. SESSION ANALYSIS")
    print("="*80)

    sessions = {
        "Asian (00-08)": (0, 8),
        "London (08-16)": (8, 16),
        "NY (16-22)": (16, 22),
        "Off (22-24)": (22, 24),
    }

    for name, (start, end) in sessions.items():
        mask = (data_copy["utc_hour"] >= start) & (data_copy["utc_hour"] < end)
        session_data = data_copy[mask]

        if len(session_data) < 100:
            continue

        returns = session_data["close"].pct_change().dropna()
        range_per_bar = (session_data["high"] - session_data["low"]).mean()

        print(f"\n  {name}:")
        print(f"    Bars: {len(session_data)}")
        print(f"    Avg range: ${range_per_bar:.2f}")
        print(f"    Avg return: {returns.mean()*100:.4f}%")
        print(f"    Std dev: {returns.std()*100:.4f}%")
        print(f"    Sharpe (annualized): {returns.mean()/returns.std()*np.sqrt(252*24):.3f}" if returns.std() > 0 else "")

    # ===== 2. DAY OF WEEK ANALYSIS =====
    print("\n" + "="*80)
    print("  2. DAY OF WEEK ANALYSIS")
    print("="*80)

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for day_idx, day_name in enumerate(days):
        day_data = data_copy[data_copy["day_of_week"] == day_idx]
        if len(day_data) < 100:
            continue

        returns = day_data["close"].pct_change().dropna()
        print(f"  {day_name}: {len(day_data)} bars, avg return {returns.mean()*100:.4f}%")

    # ===== 3. HOURLY DISTRIBUTION =====
    print("\n" + "="*80)
    print("  3. HOURLY VOLATILITY DISTRIBUTION")
    print("="*80)

    hourly_stats = []
    for hour in range(24):
        hour_data = data_copy[data_copy["utc_hour"] == hour]
        if len(hour_data) < 100:
            continue

        returns = hour_data["close"].pct_change().dropna()
        avg_range = (hour_data["high"] - hour_data["low"]).mean()

        hourly_stats.append({
            "hour": hour,
            "avg_range": avg_range,
            "std": returns.std() * 100,
            "count": len(hour_data),
        })

    print(f"\n  {'Hour':>4} | {'Range ($)':>10} | {'Std Dev %':>10} | {'Bars':>5}")
    print("  " + "-" * 40)
    for s in hourly_stats:
        print(f"  {s['hour']:>4} | {s['avg_range']:>10.2f} | {s['std']:>10.4f} | {s['count']:>5}")

    # ===== 4. DONCHIAN BREAKOUT DATA (LONDON) =====
    print("\n" + "="*80)
    print("  4. DONCHIAN BREAKOUT — LONDON SESSION TRADE LOG")
    print("="*80)

    matrix = create_strategy_matrix()
    signals = matrix.generate_signals(test_data, "trend_donchian", None)

    # Filter to London
    london_signals = [s for s in signals if 8 <= pd.Timestamp(s["timestamp"]).hour < 16]

    # Run backtest to get trades
    from engine.backtest_fixed import run_backtest_fixed
    result = run_backtest_fixed(
        test_data, london_signals,
        initial_capital=5000.0,
        risk_per_trade=0.01,
        max_drawdown_pct=0.99,
        daily_loss_limit_pct=0.50,
    )

    trades = result["trades"]
    print(f"\n  Total trades: {len(trades)}")
    print(f"\n  {'#':>3} | {'Side':>5} | {'Entry':>10} | {'Exit':>10} | {'PnL':>8} | {'Reason':>20}")
    print("  " + "-" * 65)
    for i, t in enumerate(trades[:30]):  # First 30 trades
        print(f"  {i+1:>3} | {t['side']:>5} | {t['entry_price']:>10.2f} | {t['exit_price']:>10.2f} | {t['pnl']:>8.2f} | {t['exit_reason']:>20}")

    if len(trades) > 30:
        print(f"  ... ({len(trades) - 30} more trades)")

    # ===== 5. EQUITY CURVE DATA =====
    print("\n" + "="*80)
    print("  5. EQUITY CURVE (London donchian_50)")
    print("="*80)

    equity_curve = result["equity_curve"]
    print(f"\n  Start: ${equity_curve[0][1]:,.2f}")
    print(f"  End: ${equity_curve[-1][1]:,.2f}")
    print(f"  Max: ${max(e[1] for e in equity_curve):,.2f}")
    print(f"  Min: ${min(e[1] for e in equity_curve):,.2f}")

    # Trade groups (every 20 trades)
    print(f"\n  Trade groups (20 trades each):")
    print(f"\n  {'Group':>7} | {'PnL':>10} | {'Cumulative':>10}")
    print("  " + "-" * 35)
    cumulative = 0.0
    for i in range(0, len(trades), 20):
        group_pnl = sum(t["pnl"] for t in trades[i:i+20])
        cumulative += group_pnl
        print(f"  {i//20:>7} | {group_pnl:>10.2f} | {cumulative:>10.2f}")

    # ===== 6. STATISTICAL SUMMARY =====
    print("\n" + "="*80)
    print("  6. STATISTICAL SUMMARY (London donchian_50)")
    print("="*80)

    returns = np.array([t["return_pct"] for t in trades])
    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    print(f"""
  Total trades: {len(trades)}
  Winning trades: {len(wins)} ({len(wins)/len(trades)*100:.1f}%)
  Losing trades: {len(losses)} ({len(losses)/len(trades)*100:.1f}%)

  Avg win: ${np.mean(wins)*5000:.2f} ({np.mean(wins)*100:.3f}%)
  Avg loss: ${np.mean(losses)*5000:.2f} ({np.mean(losses)*100:.3f}%)
  Largest win: ${np.max(wins)*5000:.2f} ({np.max(wins)*100:.3f}%)
  Largest loss: ${np.min(losses)*5000:.2f} ({np.min(losses)*100:.3f}%)

  Profit factor: {np.sum(wins)/abs(np.sum(losses)):.3f}
  Expectancy: {np.mean(returns)*5000:.2f} per trade
  Sharpe ratio: {np.mean(returns)/np.std(returns)*np.sqrt(252):.3f}

  Max consecutive wins: {max((len(list(g)) for k, g in __import__('itertools').groupby(returns, lambda x: x > 0) if k), default=0)}
  Max consecutive losses: {max((len(list(g)) for k, g in __import__('itertools').groupby(returns, lambda x: x <= 0) if k), default=0)}
    """)

    # ===== 7. CORRELATION WITH OTHER STRATEGIES =====
    print("\n" + "="*80)
    print("  7. STRATEGY CORRELATION MATRIX")
    print("="*80)

    signals_zscore = matrix.generate_signals(test_data, "mean_reversion_zscore", None)
    signals_ema = matrix.generate_signals(test_data, "trend_ema_cross", None)

    result_zscore = run_backtest_fixed(test_data, signals_zscore, risk_per_trade=0.01, max_drawdown_pct=0.99, daily_loss_limit_pct=0.50)
    result_ema = run_backtest_fixed(test_data, signals_ema, risk_per_trade=0.01, max_drawdown_pct=0.99, daily_loss_limit_pct=0.50)

    donchian_returns = np.array([t["return_pct"] for t in result["trades"]])
    zscore_returns = np.array([t["return_pct"] for t in result_zscore["trades"]])
    ema_returns = np.array([t["return_pct"] for t in result_ema["trades"]])

    min_len = min(len(donchian_returns), len(zscore_returns), len(ema_returns))
    if min_len > 10:
        corr_matrix = np.corrcoef([
            donchian_returns[:min_len],
            zscore_returns[:min_len],
            ema_returns[:min_len],
        ])

        print(f"\n  {'':>12} | {'donchian':>10} | {'zscore':>10} | {'ema_cross':>10}")
        print("  " + "-" * 55)
        names = ["donchian", "zscore", "ema_cross"]
        for i, name in enumerate(names):
            print(f"  {name:>12} | {corr_matrix[i][0]:>10.3f} | {corr_matrix[i][1]:>10.3f} | {corr_matrix[i][2]:>10.3f}")

    # ===== 8. VOLATILITY REGIMES =====
    print("\n" + "="*80)
    print("  8. VOLATILITY REGIMES")
    print("="*80)

    # Calculate rolling ATR
    df = data_copy.copy()
    df["prev_close"] = df["close"].shift(1)
    df["tr"] = np.maximum(df["high"] - df["low"], np.maximum((df["high"] - df["prev_close"]).abs(), (df["low"] - df["prev_close"]).abs()))
    df["atr"] = df["tr"].rolling(14).mean()
    df["atr_pct"] = df["atr"] / df["close"] * 100

    # Define regimes
    low_vol = df[df["atr_pct"] < df["atr_pct"].quantile(0.33)]
    mid_vol = df[(df["atr_pct"] >= df["atr_pct"].quantile(0.33)) & (df["atr_pct"] < df["atr_pct"].quantile(0.67))]
    high_vol = df[df["atr_pct"] >= df["atr_pct"].quantile(0.67)]

    print(f"\n  Low vol (<{df['atr_pct'].quantile(0.33):.3f}%): {len(low_vol)} bars, avg range ${(low_vol['high'] - low_vol['low']).mean():.2f}")
    print(f"  Mid vol: {len(mid_vol)} bars, avg range ${(mid_vol['high'] - mid_vol['low']).mean():.2f}")
    print(f"  High vol (>{df['atr_pct'].quantile(0.67):.3f}%): {len(high_vol)} bars, avg range ${(high_vol['high'] - high_vol['low']).mean():.2f}")

    # ===== 9. EXPORT DATA =====
    print("\n" + "="*80)
    print("  9. EXPORTING DATA FILES")
    print("="*80)

    output_dir = Path("data/store/max")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Export London session H1 data
    london_data = data_copy[(data_copy["utc_hour"] >= 8) & (data_copy["utc_hour"] < 16)]
    london_data.to_parquet(output_dir / "XAUUSD_H1_LONDON.parquet")
    print(f"  Exported: XAUUSD_H1_LONDON.parquet ({len(london_data)} bars)")

    # Export trade log
    trade_log = []
    for i, t in enumerate(trades):
        trade_log.append({
            "trade_id": i + 1,
            "side": t["side"],
            "entry_price": t["entry_price"],
            "exit_price": t["exit_price"],
            "entry_time": str(t.get("entry_time", "")),
            "exit_time": str(t.get("exit_time", "")),
            "pnl": t["pnl"],
            "return_pct": t["return_pct"],
            "bars_held": t["bars_held"],
            "exit_reason": t["exit_reason"],
        })

    with open(output_dir / "trade_log_london_donchian.json", "w") as f:
        json.dump(trade_log, f, indent=2)
    print(f"  Exported: trade_log_london_donchian.json ({len(trade_log)} trades)")

    # Export equity curve
    equity_data = [{"timestamp": e[0] if isinstance(e[0], str) else e[0].isoformat(), "equity": e[1]} for e in equity_curve]
    with open(output_dir / "equity_curve_london_donchian.json", "w") as f:
        json.dump(equity_data, f, indent=2)
    print(f"  Exported: equity_curve_london_donchian.json ({len(equity_data)} points)")

    # Export summary stats
    summary = {
        "strategy": "donchian_50",
        "session": "London (08:00-16:00 UTC)",
        "period": f"{test_data.iloc[0]['timestamp'].date()} to {test_data.iloc[-1]['timestamp'].date()}",
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) if len(trades) > 0 else 0,
        "profit_factor": float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 else 0,
        "sharpe_ratio": float(np.mean(returns) / np.std(returns) * np.sqrt(252)) if len(returns) > 1 else 0,
        "max_drawdown_pct": float(np.min((np.cumsum(returns) + 1) / np.maximum.accumulate(np.cumsum(returns) + 1) - 1)) * 100 if len(returns) > 0 else 0,
        "total_return_pct": float(np.sum(returns)) * 100,
        "avg_win": float(np.mean(wins)) if len(wins) > 0 else 0,
        "avg_loss": float(np.mean(losses)) if len(losses) > 0 else 0,
    }

    with open(output_dir / "summary_london_donchian.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Exported: summary_london_donchian.json")

    print("\n" + "="*80)
    print("  EXPORT COMPLETE")
    print("="*80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
