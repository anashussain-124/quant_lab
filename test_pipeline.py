"""Test full pipeline end-to-end with real XAUUSD data."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timezone

# Phase 1: Backtester
from engine.backtester_v3 import EventDrivenBacktester, BacktestConfig, create_default_backtest_config

# Phase 2: Regime + Strategy Matrix
from engine.regime import RegimeDetector, StrategyMatrix, Regime, create_regime_detector, create_strategy_matrix

# Phase 3: Validation Gates
from engine.validation_gates import ValidationGates, create_validation_gates

# Data
from data.fetch_extended import fetch_extended_mt5


def main():
    print("="*70)
    print("  QUANT LAB — FULL PIPELINE TEST (REAL XAUUSD DATA)")
    print("="*70)
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    # 1. Fetch real XAUUSD data
    print("[1/6] Fetching real XAUUSD data from MT5...")
    data = fetch_extended_mt5("XAUUSD", "1h", 1000)
    if data.empty:
        print("  ERROR: No data returned")
        return 1

    print(f"  Data: {len(data)} candles")
    print(f"  Range: {data.iloc[0]['timestamp']} to {data.iloc[-1]['timestamp']}")
    print(f"  Last close: ${data.iloc[-1]['close']:,.2f}")
    print(f"  Quality: {len(data)} rows, {data.isnull().sum().sum()} nulls")

    # 2. Detect regime
    print("\n[2/6] Detecting market regime...")
    regime_detector = create_regime_detector()
    regime_state = regime_detector.detect(data)
    print(f"  Regime: {regime_state.regime.value}")
    print(f"  Trend strength: {regime_state.trend_strength:.2f}")
    print(f"  Volatility: {regime_state.volatility:.6f}")
    print(f"  ADX: {regime_state.adx:.2f}")

    # 3. Get strategies for regime
    print("\n[3/6] Getting strategies for regime...")
    strategy_matrix = create_strategy_matrix()
    strategies = strategy_matrix.get_strategies_for_regime(regime_state.regime)
    print(f"  Strategies for {regime_state.regime.value}: {strategies}")

    if not strategies:
        print("  No strategies for this regime. Using all.")
        strategies = list(strategy_matrix.strategies.keys())

    # 4. Run backtest for each strategy
    print("\n[4/6] Running backtests...")
    config = create_default_backtest_config()
    results = {}

    for strategy_name in strategies:
        print(f"\n  Testing: {strategy_name}")
        signals = strategy_matrix.generate_signals(data, strategy_name, regime_state.regime)

        if not signals:
            print(f"    No signals generated")
            continue

        print(f"    Signals: {len(signals)}")

        backtester = EventDrivenBacktester(config)
        result = backtester.run(data, signals)
        results[strategy_name] = result

        if result.trades:
            metrics = result.metrics
            print(f"    Trades: {metrics['total_trades']}")
            print(f"    Win rate: {metrics['win_rate']:.2%}")
            print(f"    P&L: ${metrics['total_pnl']:.2f}")
            print(f"    Return: {metrics['total_return_pct']:.2f}%")
            print(f"    Sharpe: {metrics['sharpe_ratio']:.3f}")
            print(f"    Max DD: {metrics['max_drawdown_pct']:.2f}%")
            print(f"    Profit factor: {metrics['profit_factor']:.3f}")
            print(f"    Avg bars held: {metrics['avg_bars_held']:.1f}")
            print(f"    Total costs: ${metrics['total_costs']:.2f}")
        else:
            print(f"    No trades executed")

    # 5. Run validation gates
    print("\n[5/6] Running validation gates...")
    validation = create_validation_gates()

    for strategy_name, result in results.items():
        print(f"\n  Validating: {strategy_name}")
        validation_result = validation.validate(result, data, strategy_name, regime_state.regime)

        print(f"    Passed: {validation_result.passed}")
        print(f"    Checks:")
        for check_name, check_passed in validation_result.checks.items():
            status = "✓" if check_passed else "✗"
            detail = validation_result.details.get(check_name, "")
            print(f"      {status} {check_name}: {detail}")

        if validation_result.reasons:
            print(f"    Reasons: {validation_result.reasons}")

    # 6. Summary
    print("\n[6/6] Summary")
    print(f"  Strategies tested: {len(results)}")
    survivors = [s for s, r in results.items() if r.trades and len(r.trades) >= 5]
    print(f"  Survivors (>5 trades): {len(survivors)}")
    for name in survivors:
        r = results[name]
        print(f"    - {name}: {r.metrics['total_trades']} trades, {r.metrics['win_rate']:.0%} win, {r.metrics['sharpe_ratio']:.2f} Sharpe")

    print("\n" + "="*70)
    print("  PIPELINE TEST COMPLETE")
    print("="*70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
