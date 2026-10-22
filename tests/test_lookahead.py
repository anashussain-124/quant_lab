"""Causality and execution-audit regression tests."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qt" / "fundingpips_research_v3"))

from fundingpips_research_v3 import (  # noqa: E402
    Backtester,
    Costs,
    Instrument,
    PropRules,
    RiskConfig,
    load_csv,
    prepare,
)
from strategies.real_strategies import MeanReversionZScore, MomentumBreakout  # noqa: E402


def _bars(n: int = 100) -> pd.DataFrame:
    close = np.linspace(100.0, 110.0, n)
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close},
        index=pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
    )


def test_real_strategy_signals_are_invariant_to_future_changes() -> None:
    data = _bars()
    changed = data.copy()
    changed.iloc[70:, :] = 10_000.0
    for strategy in (MomentumBreakout(lookback=10, exit_lookback=5, trend_period=20), MeanReversionZScore(lookback=20)):
        for end in (30, 50, 70):
            before = strategy.generate_signal(data.iloc[:end])
            after = strategy.generate_signal(changed.iloc[:end])
            assert after.action == before.action
            assert after.side == before.side


def test_csv_broker_offsets_are_explicit_across_dst_dates(tmp_path) -> None:
    for local_time, offset, expected in (
        ("2024-03-31 03:00", 2, "2024-03-31 01:00:00+00:00"),
        ("2024-10-27 03:00", 3, "2024-10-27 00:00:00+00:00"),
    ):
        path = tmp_path / f"{offset}.csv"
        times = pd.date_range(local_time, periods=500, freq="5min")
        pd.DataFrame({"time": times, "open": 2000, "high": 2001, "low": 1999, "close": 2000}).to_csv(path, index=False)
        got = load_csv(path, timestamp_mode="broker", server_utc_offset=offset)
        assert got.index[0] == pd.Timestamp(expected)


def test_target_exit_applies_adverse_slippage_and_both_commissions() -> None:
    idx = pd.date_range("2024-03-04 09:00Z", periods=3, freq="5min")
    df = pd.DataFrame(
        {"open": [2000, 2000, 2010], "high": [2000, 2010, 2010], "low": [2000, 1999, 2010], "close": [2000, 2010, 2010]},
        index=idx,
    )
    sig = pd.DataFrame(
        {"side": [1, 0, 0], "stop_dist": [5.0, np.nan, np.nan], "target": [2005.0, np.nan, np.nan], "exit_long": [False] * 3, "exit_short": [False] * 3},
        index=idx,
    )
    costs = Costs(commission_per_lot_side=2.5, slippage=0.05, fixed_spread_points=100)
    rules = PropRules(initial_balance=100_000, profit_target_pct=10, max_daily_loss_pct=0.5, max_total_loss_pct=0.9, min_trading_days=1, inactivity_days=0)
    risk = RiskConfig(risk_per_trade=0.005, soft_daily_stop_pct=0.5, soft_total_drawdown_pct=0.9, entry_start_london=8, entry_end_london=12)
    inst = Instrument(point=0.01, contract_size=100, volume_min=0.01, volume_step=0.01)
    result = Backtester(inst, costs, rules, risk).run(prepare(df, sig, inst, costs, rules), enforce_prop_rules=False, stop_on_result=False)
    trade = result.trades.iloc[0]
    volume = float(trade.volume)
    expected = ((2005.0 - 0.05) - (2000.0 + 1.0 + 0.05)) * volume * 100 - 2 * 2.5 * volume
    assert trade.reason == "TP"
    assert trade.pnl == pytest.approx(expected)


def test_same_bar_stop_and_target_is_pessimistically_a_stop() -> None:
    idx = pd.date_range("2024-03-04 09:00Z", periods=3, freq="5min")
    df = pd.DataFrame({"open": [2000, 2000, 2000], "high": [2000, 2010, 2000], "low": [2000, 1990, 2000], "close": [2000, 2005, 2000]}, index=idx)
    sig = pd.DataFrame({"side": [1, 0, 0], "stop_dist": [5.0, np.nan, np.nan], "target": [2005.0, np.nan, np.nan], "exit_long": [False] * 3, "exit_short": [False] * 3}, index=idx)
    costs = Costs(commission_per_lot_side=0, slippage=0, fixed_spread_points=0)
    rules = PropRules(initial_balance=100_000, profit_target_pct=10, max_daily_loss_pct=0.5, max_total_loss_pct=0.9, min_trading_days=1, inactivity_days=0)
    risk = RiskConfig(risk_per_trade=0.005, soft_daily_stop_pct=0.5, soft_total_drawdown_pct=0.9, entry_start_london=8, entry_end_london=12)
    inst = Instrument(point=0.01, contract_size=100)
    result = Backtester(inst, costs, rules, risk).run(prepare(df, sig, inst, costs, rules), enforce_prop_rules=False, stop_on_result=False)
    assert result.trades.iloc[0].reason == "SL"


def test_final_open_position_is_closed_with_exit_costs() -> None:
    idx = pd.date_range("2024-03-04 09:00Z", periods=3, freq="5min")
    df = pd.DataFrame({"open": [2000, 2000, 2010], "high": [2000, 2000, 2010], "low": [2000, 2000, 2010], "close": [2000, 2000, 2010]}, index=idx)
    sig = pd.DataFrame({"side": [1, 0, 0], "stop_dist": [5.0, np.nan, np.nan], "target": [np.nan, np.nan, np.nan], "exit_long": [False] * 3, "exit_short": [False] * 3}, index=idx)
    costs = Costs(commission_per_lot_side=2.5, slippage=0.05, fixed_spread_points=100)
    rules = PropRules(initial_balance=100_000, profit_target_pct=10, max_daily_loss_pct=0.5, max_total_loss_pct=0.9, min_trading_days=1, inactivity_days=0)
    risk = RiskConfig(risk_per_trade=0.005, soft_daily_stop_pct=0.5, soft_total_drawdown_pct=0.9, entry_start_london=8, entry_end_london=12)
    inst = Instrument(point=0.01, contract_size=100)
    result = Backtester(inst, costs, rules, risk).run(prepare(df, sig, inst, costs, rules), enforce_prop_rules=False, stop_on_result=False)
    trade = result.trades.iloc[0]
    volume = float(trade.volume)
    expected = ((2010.0 - 0.05) - (2000.0 + 1.0 + 0.05)) * volume * 100 - 2 * 2.5 * volume
    assert trade.reason == "END_OF_DATA"
    assert trade.pnl == pytest.approx(expected)
