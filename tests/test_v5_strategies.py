"""Pre-registration and causality tests for the v5 strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.backtest_v5 import run_backtest, summarize
from research.costs_v5 import CostModel
from research.indicators_v5 import wilder_atr
from research.strategies_v5 import SPECS, Plan, StrategySpec, all_specs, build_plan


def random_walk_frame(n: int = 1200, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    close = 1500 + np.cumsum(rng.normal(0, 1.0, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.2, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.2, n)
    frame = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)
    frame["tick_volume"] = 100
    frame["spread"] = 10.0
    frame["real_volume"] = 0
    return frame


def test_exactly_fifty_unique_pre_registered_specs():
    specs = all_specs()
    assert len(specs) == 50
    assert len({spec.config_id for spec in specs}) == 50
    families = {spec.family for spec in specs}
    assert families == {"donchian_trail", "ema_pullback", "asian_range_breakout",
                        "lowvol_meanrev", "squeeze_breakout"}
    for family in families:
        assert sum(1 for spec in specs if spec.family == family) == 10
    assert [spec.config_id for spec in SPECS] == [f"dc{i:02d}" for i in range(1, 11)] + \
        [f"em{i:02d}" for i in range(1, 11)] + [f"ar{i:02d}" for i in range(1, 11)] + \
        [f"mr{i:02d}" for i in range(1, 11)] + [f"sq{i:02d}" for i in range(1, 11)]


def test_plans_have_valid_shapes_and_family_invariants():
    frame = random_walk_frame()
    for spec in all_specs():
        plan = build_plan(frame, spec)
        assert len(plan.atr) == len(frame)
        assert plan.long_signal.dtype == np.bool_
        assert plan.short_signal.dtype == np.bool_
        assert plan.max_hold > 0
        assert np.isnan(plan.atr[:13]).all()
        assert 0 < plan.warmup < len(frame)
        if spec.family in {"asian_range_breakout", "squeeze_breakout"}:
            assert (plan.target_r is None) != (plan.trail_atr is None)
        if spec.family == "lowvol_meanrev":
            assert plan.target_r is None and plan.trail_atr is None
        if spec.family == "asian_range_breakout":
            assert plan.exit_hour in {16, 20}
        assert not (plan.long_signal & plan.short_signal).any()


def test_signals_do_not_change_when_future_data_is_truncated():
    frame = random_walk_frame()
    cutoff = 800
    short_frame = frame.iloc[:cutoff]
    check = 780
    for spec in all_specs():
        full = build_plan(frame, spec)
        truncated = build_plan(short_frame, spec)
        for name in ("long_signal", "short_signal", "exit_long", "exit_short"):
            np.testing.assert_array_equal(
                getattr(full, name)[:check], getattr(truncated, name)[:check],
                err_msg=f"{spec.config_id} {name} depends on future bars",
            )
        np.testing.assert_allclose(full.atr[:check], truncated.atr[:check], equal_nan=True,
                                   err_msg=f"{spec.config_id} ATR depends on future bars")


def test_signals_do_not_change_when_future_prices_shock():
    frame = random_walk_frame()
    cutoff = 600
    check = 590
    shocked = frame.copy()
    for column in ("open", "high", "low", "close"):
        shocked.loc[shocked.index[cutoff:], column] *= 1.5
    for family in {spec.family for spec in SPECS}:
        spec = next(s for s in SPECS if s.family == family)
        base = build_plan(frame, spec)
        altered = build_plan(shocked, spec)
        for name in ("long_signal", "short_signal", "exit_long", "exit_short"):
            np.testing.assert_array_equal(
                getattr(base, name)[:check], getattr(altered, name)[:check],
                err_msg=f"{family} {name} leaks future prices",
            )


def _oracle_plan(frame: pd.DataFrame, *, shuffled: bool) -> Plan:
    """Deliberately leaked 24-bar direction; used only as a CI canary."""
    n = len(frame)
    close = frame["close"].to_numpy(dtype=float)
    atr = wilder_atr(frame["high"].to_numpy(float), frame["low"].to_numpy(float), close, 14)
    future = np.full(n, np.nan)
    horizon = 24
    future[:-horizon] = close[horizon:] - close[:-horizon]
    direction = np.sign(future)
    if shuffled:
        rng = np.random.default_rng(99)
        known = np.isfinite(direction)
        values = direction[known]
        rng.shuffle(values)
        direction = np.full(n, np.nan)
        direction[known] = values
    long_signal = direction > 0
    short_signal = direction < 0
    return Plan(
        spec=StrategySpec("oracle", "canary", {}),
        long_signal=long_signal,
        short_signal=short_signal,
        exit_long=np.zeros(n, dtype=bool),
        exit_short=np.zeros(n, dtype=bool),
        atr=atr,
        stop_atr=5.0,
        target_r=None,
        trail_atr=None,
        max_hold=horizon,
        exit_hour=None,
        warmup=20,
    )


def test_leakage_canary_absurd_result_and_shuffled_null():
    frame = random_walk_frame(n=3000, seed=3)
    leaked = run_backtest(frame, _oracle_plan(frame, shuffled=False), CostModel())
    shuffled = run_backtest(frame, _oracle_plan(frame, shuffled=True), CostModel())
    leaked_summary = summarize(leaked)
    shuffled_summary = summarize(shuffled)
    assert leaked_summary["n"] >= 100
    assert leaked_summary["mean_net_r"] > 0.10
    assert leaked_summary["win_rate"] > 0.80
    assert abs(shuffled_summary["mean_net_r"]) < 0.05


def test_plan_rejects_naive_frame():
    frame = random_walk_frame(100)
    frame.index = frame.index.tz_localize(None)
    with pytest.raises(ValueError):
        build_plan(frame, all_specs()[0])
