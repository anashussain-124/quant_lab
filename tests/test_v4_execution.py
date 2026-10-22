from decimal import Decimal

import pandas as pd

from execution.causal_sim import CostConfig, simulate_next_open


def _bars(rows):
    return pd.DataFrame(rows, index=pd.date_range("2024-01-01", periods=len(rows), freq="h", tz="UTC"))


def test_signal_fills_next_open_and_both_cost_sides_are_applied():
    bars = _bars([
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": 102, "high": 103, "low": 101, "close": 102},
        {"open": 104, "high": 105, "low": 103, "close": 104},
    ])
    trade = simulate_next_open(
        bars, {0: 1}, costs=CostConfig(Decimal("100"), Decimal("0"), Decimal("10"))
    )[0]
    assert trade.entry_index == 1
    assert trade.entry_price == Decimal("102.51")
    assert trade.exit_price == Decimal("103.480")
    assert trade.costs == Decimal("0.20599")
    assert trade.net_pnl == Decimal("0.76401")


def test_stop_is_first_when_stop_and_target_are_both_touched():
    bars = _bars([
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": 100, "high": 102, "low": 98, "close": 100},
    ])
    trade = simulate_next_open(bars, {0: 1}, stop_distance=Decimal("1"), target_distance=Decimal("1"))[0]
    assert trade.exit_reason == "stop"
    assert trade.exit_price == Decimal("99")


def test_open_position_is_closed_at_last_bar():
    bars = _bars([
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": 101, "high": 102, "low": 100, "close": 101},
    ])
    trade = simulate_next_open(bars, {0: 1})[0]
    assert trade.exit_reason == "final"
    assert trade.exit_index == 1
