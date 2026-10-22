"""Execution-semantics tests for the v5 simulator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.backtest_v5 import make_market, run_backtest, simulate_trade, summarize
from research.costs_v5 import CostModel
from research.strategies_v5 import Plan, StrategySpec


def frame_from_bars(bars, *, start="2020-01-01 00:00", spread=10.0) -> pd.DataFrame:
    index = pd.date_range(start, periods=len(bars), freq="h", tz="UTC")
    frame = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=index)
    frame["tick_volume"] = 100
    frame["spread"] = spread
    frame["real_volume"] = 0
    return frame


def flat_bars(n: int, price: float = 100.0) -> list[tuple[float, float, float, float]]:
    return [(price, price, price, price)] * n


def market(frame: pd.DataFrame, atr: float = 1.0):
    return make_market(frame, np.full(len(frame), atr, dtype=float), CostModel())


def test_stop_wins_when_stop_and_target_touch_same_bar():
    bars = flat_bars(6)
    bars[1] = (100.0, 102.0, 98.0, 100.0)
    md = market(frame_from_bars(bars))
    trade = simulate_trade(md, 1, 1, 1.0, target_r=1.0, trail_atr=None, max_hold=5,
                           exit_hour=None, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.reason == "stop"
    assert trade.exit_price == pytest.approx(99.0)
    assert trade.gross_r == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open():
    bars = flat_bars(6)
    bars[1] = (100.0, 100.0, 100.0, 100.0)
    bars[2] = (97.0, 98.0, 96.0, 97.0)
    md = market(frame_from_bars(bars))
    trade = simulate_trade(md, 1, 1, 1.0, target_r=None, trail_atr=None, max_hold=5,
                           exit_hour=None, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.reason == "stop"
    assert trade.exit_index == 2
    assert trade.exit_price == pytest.approx(97.0)
    assert trade.gross_r == pytest.approx(-3.0)
    assert trade.open_fill is True


def test_trailing_stop_only_tightens():
    bars = flat_bars(7)
    bars[1] = (100.0, 101.0, 99.5, 100.5)
    bars[2] = (100.5, 105.0, 100.2, 104.0)
    bars[3] = (104.0, 110.0, 104.5, 109.0)
    bars[4] = (109.0, 109.5, 105.0, 106.0)
    md = market(frame_from_bars(bars))
    trade = simulate_trade(md, 1, 1, 5.0, target_r=None, trail_atr=1.0, max_hold=6,
                           exit_hour=None, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.reason == "stop"
    assert trade.exit_index == 4
    assert trade.exit_price == pytest.approx(108.0)
    assert trade.gross_r == pytest.approx(8.0 / 5.0)


def test_session_exit_uses_configured_hour_open():
    bars = flat_bars(10)
    frame = frame_from_bars(bars, start="2020-01-01 12:00")
    md = market(frame)
    trade = simulate_trade(md, 1, 1, 2.0, target_r=None, trail_atr=None, max_hold=24,
                           exit_hour=16, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.reason == "session"
    assert trade.exit_index == 4
    assert trade.exit_time.hour == 16


def test_time_exit_fills_at_next_open():
    bars = flat_bars(10)
    md = market(frame_from_bars(bars))
    trade = simulate_trade(md, 1, 1, 2.0, target_r=None, trail_atr=None, max_hold=3,
                           exit_hour=None, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.reason == "time"
    assert trade.exit_index == 4
    assert trade.bars_held == 3


def test_cost_formula_on_flat_trade():
    bars = flat_bars(8)
    md = market(frame_from_bars(bars, spread=20.0))
    trade = simulate_trade(md, 1, 1, 2.0, target_r=None, trail_atr=None, max_hold=2,
                           exit_hour=None, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.gross_r == pytest.approx(0.0)
    assert trade.cost_r == pytest.approx(0.14)
    assert trade.swap_r == pytest.approx(0.0)
    assert trade.net_r == pytest.approx(-0.14)


def test_swap_triples_on_wednesday_rollover():
    wednesday = flat_bars(40)
    md_wed = market(frame_from_bars(wednesday, start="2020-01-01 00:00"))
    wed = simulate_trade(md_wed, 20, 1, 2.0, target_r=None, trail_atr=None, max_hold=5,
                         exit_hour=None, exit_long=None, exit_short=None,
                         costs=CostModel(), config_id="t")
    tuesday = flat_bars(40)
    md_tue = market(frame_from_bars(tuesday, start="2019-12-31 00:00"))
    tue = simulate_trade(md_tue, 20, 1, 2.0, target_r=None, trail_atr=None, max_hold=5,
                         exit_hour=None, exit_long=None, exit_short=None,
                         costs=CostModel(), config_id="t")
    assert wed is not None and tue is not None
    assert wed.swap_r == pytest.approx(3.0 * tue.swap_r)
    assert wed.swap_r == pytest.approx(-12.6 * 0.01 * 3.0 / 2.0)


def test_short_stop_and_target_symmetry():
    bars = flat_bars(6)
    bars[1] = (100.0, 102.0, 98.0, 100.0)
    md = market(frame_from_bars(bars))
    trade = simulate_trade(md, 1, -1, 1.0, target_r=1.0, trail_atr=None, max_hold=5,
                           exit_hour=None, exit_long=None, exit_short=None,
                           costs=CostModel(), config_id="t")
    assert trade is not None
    assert trade.reason == "stop"
    assert trade.exit_price == pytest.approx(101.0)
    assert trade.gross_r == pytest.approx(-1.0)


def _simple_plan(frame: pd.DataFrame, signal_index: int) -> Plan:
    n = len(frame)
    long_signal = np.zeros(n, dtype=bool)
    long_signal[signal_index] = True
    return Plan(
        spec=StrategySpec("test", "test", {}),
        long_signal=long_signal,
        short_signal=np.zeros(n, dtype=bool),
        exit_long=np.zeros(n, dtype=bool),
        exit_short=np.zeros(n, dtype=bool),
        atr=np.ones(n),
        stop_atr=1.0,
        target_r=None,
        trail_atr=None,
        max_hold=2,
        exit_hour=None,
        warmup=1,
    )


def test_entry_is_next_bar_open_and_trades_do_not_overlap():
    bars = flat_bars(30)
    frame = frame_from_bars(bars)
    plan = _simple_plan(frame, 5)
    trades = run_backtest(frame, plan, CostModel(), start_time=pd.Timestamp("2020-01-01", tz="UTC"))
    assert len(trades) == 1
    assert trades[0].entry_index == 6
    assert trades[0].entry_price == pytest.approx(frame["open"].iloc[6])

    index = pd.date_range("2020-01-01", periods=60, freq="h", tz="UTC")
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 0.5, len(index)))
    frame = pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5, "close": closes,
        "tick_volume": 1, "spread": 10.0, "real_volume": 0,
    }, index=index)
    n = len(frame)
    signals = np.zeros(n, dtype=bool)
    signals[3::4] = True
    plan = Plan(StrategySpec("test", "test", {}), signals, np.zeros(n, bool),
                np.zeros(n, bool), np.zeros(n, bool), np.full(n, 1.0), 1.0, None, None, 3, None, 2)
    trades = run_backtest(frame, plan, CostModel())
    assert len(trades) >= 3
    for previous, following in zip(trades, trades[1:], strict=False):
        assert following.entry_index >= previous.exit_index


def test_summarize_drawdown_and_yearly():
    trades = []
    for i, r in enumerate([1.0, -0.5, -2.0, 3.0]):
        trades.append(type("T", (), {
            "net_r": r, "gross_r": r, "cost_r": 0.0, "swap_r": 0.0,
            "entry_time": pd.Timestamp(f"2020-0{i + 1}-01", tz="UTC"),
            "bars_held": 2, "atr_entry": 1.0,
        })())
    summary = summarize(trades, window_bars=100)
    assert summary["max_drawdown_r"] == pytest.approx(2.5)
    assert summary["total_net_r"] == pytest.approx(1.5)
    assert summary["yearly"][2020]["n"] == 4
