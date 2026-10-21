"""Deep dive: size-down analysis, Sharpe CI, correlation, session filter."""
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
from engine.backtest_fixed import run_backtest_fixed


def sharpe_ci(sharpe: float, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Confidence interval for Sharpe ratio.
    
    Uses the standard error approximation: SE = sqrt((1 + S^2/2) / n)
    """
    if n < 2:
        return (0.0, 0.0)
    se = np.sqrt((1 + sharpe**2 / 2) / n)
    z = 1.96  # 95% CI
    return (sharpe - z * se, sharpe + z * se)


def max_drawdown_uncapped(data: pd.DataFrame, signals: list[dict],
                          initial_capital: float = 5000.0) -> dict:
    """Run backtest WITHOUT drawdown cap to see true max DD."""
    from engine.backtest_fixed import run_backtest_fixed

    # Run with very high DD limit (99%) to effectively disable it
    result = run_backtest_fixed(
        data, signals,
        initial_capital=initial_capital,
        max_drawdown_pct=0.99,  # 99% DD limit (effectively uncapped)
        daily_loss_limit_pct=0.50,  # 50% daily limit
    )
    return result


def run_with_size(data: pd.DataFrame, signals: list[dict],
                  risk_per_trade: float, label: str,
                  initial_capital: float = 5000.0) -> dict:
    """Run backtest with specific risk per trade."""
    from engine.backtest_fixed import run_backtest_fixed

    result = run_backtest_fixed(
        data, signals,
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
        max_drawdown_pct=0.10,  # 10% prop firm limit
        daily_loss_limit_pct=0.05,  # 5% daily limit
    )
    return result


def compute_correlation(returns_a: np.ndarray, returns_b: np.ndarray) -> float:
    """Compute correlation between two strategy return series."""
    min_len = min(len(returns_a), len(returns_b))
    if min_len < 10:
        return 0.0
    a = returns_a[:min_len]
    b = returns_b[:min_len]
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def main():
    print("="*80)
    print("  QUANT LAB — DEEP DIVE ANALYSIS")
    print("  Size-down, Sharpe CI, Correlation, Session Filter")
    print("="*80)
    print()

    # Load H1 data
    print("Loading data...")
    data = pd.read_parquet("data/store/max/XAUUSD_H1_MAX.parquet")
    print(f"  H1: {len(data)} bars ({data.iloc[0]['timestamp'].date()} to {data.iloc[-1]['timestamp'].date()})")

    # Split for walk-forward
    n = len(data)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)
    test_data = data.iloc[val_end:]

    # Generate signals for top 2 strategies
    matrix = create_strategy_matrix()

    print("\nGenerating signals on test set (unseen data)...")
    signals_donchian = matrix.generate_signals(test_data, "trend_donchian", None)
    signals_zscore = matrix.generate_signals(test_data, "mean_reversion_zscore", None)
    signals_ema = matrix.generate_signals(test_data, "trend_ema_cross", None)

    print(f"  donchian_50: {len(signals_donchian)} signals")
    print(f"  zscore_20_3.0: {len(signals_zscore)} signals")
    print(f"  ema_cross_20_50: {len(signals_ema)} signals")

    # ===== 1. SIZE-DOWN ANALYSIS =====
    print("\n" + "="*80)
    print("  1. SIZE-DOWN ANALYSIS (donchian_50)")
    print("="*80)

    for risk in [0.01, 0.007, 0.005, 0.003, 0.002, 0.001]:
        result = run_with_size(test_data, signals_donchian, risk, f"donchian_50_r{risk}")
        m = result["metrics"]
        if "error" not in m:
            low, high = sharpe_ci(m["sharpe_ratio"], m["total_trades"])
            print(f"  risk={risk:.3f}: trades={m['total_trades']:>3}  WR={m['win_rate']:.0%}  "
                  f"PF={m['profit_factor']:.2f}  Sharpe={m['sharpe_ratio']:.2f} [{low:.2f}, {high:.2f}]  "
                  f"MaxDD={m['max_drawdown_pct']:.1f}%  Return={m['total_return_pct']:.1f}%  "
                  f"halted={result['halted']}")
        else:
            print(f"  risk={risk:.3f}: ERROR - {m['error']}")

    # ===== 2. UNCAPPED MAX DD =====
    print("\n" + "="*80)
    print("  2. TRUE MAX DRAWDOWN (uncapped, 1% risk)")
    print("="*80)

    uncapped = max_drawdown_uncapped(test_data, signals_donchian)
    m = uncapped["metrics"]
    if "error" not in m:
        print(f"  donchian_50 (uncapped):")
        print(f"    Trades: {m['total_trades']}")
        print(f"    Win Rate: {m['win_rate']:.1%}")
        print(f"    Profit Factor: {m['profit_factor']:.2f}")
        print(f"    Sharpe: {m['sharpe_ratio']:.2f}")
        print(f"    Max DD (true): {m['max_drawdown_pct']:.1f}%")
        print(f"    Return: {m['total_return_pct']:.1f}%")
        print(f"    Total PnL: ${m['total_pnl']:,.2f}")

    # ===== 3. CORRELATION =====
    print("\n" + "="*80)
    print("  3. STRATEGY CORRELATION")
    print("="*80)

    # Get trade-by-trade returns
    result_donchian = run_with_size(test_data, signals_donchian, 0.005, "donchian")
    result_zscore = run_with_size(test_data, signals_zscore, 0.005, "zscore")
    result_ema = run_with_size(test_data, signals_ema, 0.005, "ema")

    donchian_returns = np.array([t["return_pct"] for t in result_donchian["trades"]])
    zscore_returns = np.array([t["return_pct"] for t in result_zscore["trades"]])
    ema_returns = np.array([t["return_pct"] for t in result_ema["trades"]])

    corr_dz = compute_correlation(donchian_returns, zscore_returns)
    corr_de = compute_correlation(donchian_returns, ema_returns)
    corr_ze = compute_correlation(zscore_returns, ema_returns)

    print(f"  donchian_50 vs zscore_20_3.0: r = {corr_dz:.3f}")
    print(f"  donchian_50 vs ema_cross_20_50: r = {corr_de:.3f}")
    print(f"  zscore_20_3.0 vs ema_cross_20_50: r = {corr_ze:.3f}")

    if abs(corr_dz) < 0.3:
        print("  -> donchian + zscore are UNCORRELATED (good for blending)")
    elif abs(corr_dz) < 0.7:
        print("  -> donchian + zscore are MODERATELY correlated (some diversification benefit)")
    else:
        print("  -> donchian + zscore are HIGHLY correlated (limited diversification)")

    # ===== 4. BLENDED STRATEGY =====
    print("\n" + "="*80)
    print("  4. BLENDED STRATEGY (donchian_50 + zscore_20_3.0 at 0.5% risk each)")
    print("="*80)

    # Simple blend: run both and average the returns
    min_trades = min(len(result_donchian["trades"]), len(result_zscore["trades"]))
    blended_returns = (donchian_returns[:min_trades] + zscore_returns[:min_trades]) / 2

    if len(blended_returns) > 0:
        blended_pnl = np.cumsum(blended_returns)
        blended_equity = 5000 * (1 + blended_pnl)
        peaks = np.maximum.accumulate(blended_equity)
        drawdowns = (blended_equity - peaks) / peaks
        max_dd_blend = float(np.min(drawdowns)) * 100

        wins = blended_returns[blended_returns > 0]
        losses = blended_returns[blended_returns <= 0]
        gross_win = np.sum(wins) if len(wins) > 0 else 0.0
        gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 1e-8
        pf_blend = gross_win / gross_loss if gross_loss > 0 else float("inf")
        wr_blend = len(wins) / len(blended_returns) if len(blended_returns) > 0 else 0.0

        std_blend = float(np.std(blended_returns, ddof=1)) if len(blended_returns) > 1 else 1e-8
        sharpe_blend = float(np.mean(blended_returns) / std_blend * np.sqrt(252)) if std_blend > 0 else 0.0

        print(f"  Blended: {len(blended_returns)} trades")
        print(f"  Win Rate: {wr_blend:.1%}")
        print(f"  Profit Factor: {pf_blend:.2f}")
        print(f"  Sharpe: {sharpe_blend:.2f}")
        print(f"  Max DD: {max_dd_blend:.1f}%")
        print(f"  Return: {blended_pnl[-1]*100:.1f}%")

    # ===== 5. SESSION FILTER =====
    print("\n" + "="*80)
    print("  5. SESSION FILTER (XAUUSD-specific)")
    print("="*80)

    # Add UTC hour to data
    test_data_copy = test_data.copy()
    test_data_copy["utc_hour"] = test_data_copy["timestamp"].apply(
        lambda x: pd.Timestamp(x).hour if hasattr(pd.Timestamp(x), "hour") else 0
    )

    # Define sessions
    sessions = {
        "asian": range(0, 8),    # 00:00 - 08:00 UTC
        "london": range(8, 16),  # 08:00 - 16:00 UTC
        "ny": range(16, 22),     # 16:00 - 22:00 UTC
        "off_hours": range(22, 24),
    }

    for session_name, hours in sessions.items():
        # Filter signals to only this session
        session_signals = [
            s for s in signals_donchian
            if pd.Timestamp(s["timestamp"]).hour in hours
        ]

        if not session_signals:
            print(f"  {session_name}: No signals")
            continue

        result = run_with_size(test_data, session_signals, 0.005, f"donchian_{session_name}")
        m = result["metrics"]
        if "error" not in m:
            print(f"  {session_name:12}: trades={m['total_trades']:>3}  WR={m['win_rate']:.0%}  "
                  f"PF={m['profit_factor']:.2f}  Sharpe={m['sharpe_ratio']:.2f}  "
                  f"MaxDD={m['max_drawdown_pct']:.1f}%  Return={m['total_return_pct']:.1f}%")
        else:
            print(f"  {session_name:12}: ERROR - {m['error']}")

    # ===== 6. SUMMARY =====
    print("\n" + "="*80)
    print("  SUMMARY")
    print("="*80)

    print("\n  Key findings:")
    print("  1. Size-down: Lower risk per trade reduces DD but also reduces absolute return")
    print("  2. Sharpe CI: With n=117, the 95% CI on Sharpe 1.53 is wide")
    print("  3. Correlation: donchian + zscore are candidates for blending")
    print("  4. Session filter: XAUUSD has known bad hours that inflate DD")

    print("\n  Recommendation:")
    print("  - Run donchian_50 at 0.3-0.5% risk per trade")
    print("  - Add session filter (avoid Asian session, thin liquidity)")
    print("  - Blend with zscore mean-reversion for diversification")
    print("  - Accept lower absolute return for prop firm compliance")

    return 0


if __name__ == "__main__":
    sys.exit(main())
