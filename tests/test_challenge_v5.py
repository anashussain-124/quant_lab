"""Unit tests for the frozen-v5 FundingPips challenge simulator.

These tests use synthetic H1 bars and synthetic trade paths. They verify rule
mechanics, not strategy performance. All records are BACKTEST.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.challenge_v5 import (
    Bars,
    ChallengeRules,
    CostCase,
    Scenario,
    TradePath,
    _net_r,
    _size_lots,
    fp_day_id,
    next_reset_after,
    simulate_attempt,
)

UTC = "UTC"
RULES = ChallengeRules(
    initial_balance=5_000.0,
    phase_1_target_pct=0.08,
    phase_2_target_pct=0.05,
    overall_loss_pct=0.10,
    inactivity_days=30,
    daily_reset_hour_utc=21,
    phase_2_resets_to=5_000.0,
)
FP7 = CostCase("fp7", 7.0, 1.0)


def make_bars(rows: list[dict]) -> Bars:
    index = pd.DatetimeIndex([row["ts"] for row in rows])
    if index.tz is None:
        index = index.tz_localize(UTC)
    return Bars(
        index=index,
        open=np.array([row.get("open", 100.0) for row in rows], dtype=float),
        high=np.array([row.get("high", 100.0) for row in rows], dtype=float),
        low=np.array([row.get("low", 100.0) for row in rows], dtype=float),
        close=np.array([row.get("close", 100.0) for row in rows], dtype=float),
        spread_price=np.array([row.get("spread", 0.0) for row in rows], dtype=float),
    )


def make_trade(
    bars: Bars,
    ts: str,
    *,
    config_id: str = "t1",
    direction: int = 1,
    entry_price: float = 100.0,
    exit_price: float = 100.0,
    stop_distance: float = 10.0,
    gross_r: float = 0.0,
    cost_r: float = 0.0,
    swap_r: float = 0.0,
    reason: str = "signal",
) -> TradePath:
    stamp = pd.Timestamp(ts)
    if stamp.tz is None:
        stamp = stamp.tz_localize(UTC)
    idx = int(bars.index.get_loc(stamp))
    return TradePath(
        config_id=config_id,
        entry_idx=idx,
        exit_idx=idx,
        entry_time=stamp,
        exit_time=stamp,
        direction=direction,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_distance=stop_distance,
        gross_r=gross_r,
        cost_r=cost_r,
        swap_r=swap_r,
        reason=reason,
        bars_held=1,
    )


def win_bar(ts: str) -> dict:
    return {"ts": ts, "open": 100.0, "high": 180.0, "low": 99.5, "close": 180.0}


def loss_bar(ts: str) -> dict:
    return {"ts": ts, "open": 100.0, "high": 100.5, "low": 90.0, "close": 90.0}


def gap_loss_bar(ts: str, open_price: float = 70.0) -> dict:
    return {
        "ts": ts,
        "open": open_price,
        "high": open_price + 0.5,
        "low": open_price,
        "close": open_price,
    }


def run(
    bars_rows: list[dict],
    paths: list[TradePath],
    *,
    scenario: Scenario,
    risk: float = 0.015,
    horizon: str = "2020-01-10T00:00:00+00:00",
    cost: CostCase = FP7,
    rules: ChallengeRules = RULES,
):
    bars = make_bars(bars_rows)
    return simulate_attempt(
        paths,
        0,
        bars,
        bars.index[0] - pd.Timedelta(hours=2),
        pd.Timestamp(horizon),
        risk,
        scenario,
        cost,
        rules,
        config_id="t1",
    )


PRIMARY = Scenario(
    name="primary",
    daily_loss_pct=0.05,
    min_trading_days=3,
    carry_phase_2=False,
    internal_soft_guards=True,
)


class TestDayBoundaries:
    def test_fp_day_label_shifts_at_2100_utc(self):
        before = pd.Timestamp("2026-01-10 20:59", tz=UTC)
        after = pd.Timestamp("2026-01-10 21:01", tz=UTC)
        assert fp_day_id(before) == "2026-01-09"
        assert fp_day_id(after) == "2026-01-10"

    def test_next_reset_strictly_after(self):
        assert next_reset_after(
            pd.Timestamp("2026-01-10 20:59", tz=UTC)
        ) == pd.Timestamp("2026-01-10 21:00", tz=UTC)
        assert next_reset_after(
            pd.Timestamp("2026-01-10 21:00", tz=UTC)
        ) == pd.Timestamp("2026-01-11 21:00", tz=UTC)


class TestSizing:
    def test_volume_is_floored_not_rounded(self):
        lots, risk, reason = _size_lots(5_000.0, 0.005, 7.3, 1_700.0)
        assert reason is None
        assert lots == 0.03
        assert risk == pytest.approx(21.9)

    def test_minimum_lot_can_exceed_risk_budget(self):
        lots, risk, reason = _size_lots(5_000.0, 0.001, 20.0, 1_700.0)
        assert lots == 0.0
        assert reason == "MIN_LOT_EXCEEDS_BUDGET"

    def test_remaining_room_caps_size(self):
        lots, risk, reason = _size_lots(
            5_000.0, 0.005, 10.0, 1_700.0, daily_room=2.0, overall_room=500.0
        )
        assert lots == 0.0
        assert reason == "ROOM_BELOW_MIN_LOT"

    def test_margin_cap_reduces_volume(self):
        lots, risk, reason = _size_lots(5_000.0, 0.5, 1.0, 2_000.0)
        assert reason is None
        assert lots == 0.75
        assert risk == pytest.approx(75.0)


class TestNetR:
    def test_fp10_commission_replaces_v5_commission(self):
        path = TradePath(
            "t", 0, 0, pd.Timestamp("2020-01-01", tz=UTC),
            pd.Timestamp("2020-01-01", tz=UTC), 1, 100.0, 120.0, 10.0,
            2.0, 0.1, -0.02, "target", 1,
        )
        value = _net_r(path, CostCase("fp10", 10.0, 1.0))
        assert value == pytest.approx(2.0 - 0.1 - 0.02 - 3.0 / (100.0 * 10.0))

    def test_stress_applies_to_all_cost_components(self):
        path = TradePath(
            "t", 0, 0, pd.Timestamp("2020-01-01", tz=UTC),
            pd.Timestamp("2020-01-01", tz=UTC), 1, 100.0, 120.0, 10.0,
            2.0, 0.1, -0.02, "target", 1,
        )
        value = _net_r(path, CostCase("fp10x", 10.0, 1.5))
        expected = 2.0 - 0.15 - 0.03 - 3.0 * 1.5 / (100.0 * 10.0)
        assert value == pytest.approx(expected)


class TestChallengeOutcomes:
    def test_daily_limit_breach_on_floating_loss(self):
        rows = [loss_bar("2020-01-01T10:00:00+00:00")]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=-1.0, reason="stop", exit_price=90.0,
        )
        result = run(rows, [path], scenario=PRIMARY, risk=0.05)
        assert result.outcome == "FAIL_DAILY"
        assert result.breach_daily
        assert not result.breach_overall

    def test_overall_limit_breach_on_gap(self):
        rows = [gap_loss_bar("2020-01-01T10:00:00+00:00", 70.0)]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=-3.0, reason="stop", exit_price=70.0,
        )
        scenario = Scenario(
            name="wide_daily", daily_loss_pct=0.99, min_trading_days=0,
            carry_phase_2=False, internal_soft_guards=False,
        )
        result = run(rows, [path], scenario=scenario, risk=0.05)
        assert result.outcome == "FAIL_OVERALL"
        assert result.breach_overall
        assert not result.breach_daily

    def test_internal_daily_soft_guard(self):
        rows = [loss_bar("2020-01-01T10:00:00+00:00")]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=-1.0, reason="stop", exit_price=90.0,
        )
        result = run(rows, [path], scenario=PRIMARY, risk=0.02)
        assert result.outcome == "SOFT_GUARD_DAILY"
        assert result.soft_guard
        assert not result.breach_daily

    def test_near_overall_and_overall_soft_guard(self):
        rows = [gap_loss_bar("2020-01-01T10:00:00+00:00", 81.0)]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=-1.9, reason="stop", exit_price=81.0,
        )
        scenario = Scenario(
            name="wide_daily", daily_loss_pct=0.99, min_trading_days=0,
            carry_phase_2=False, internal_soft_guards=True,
            daily_soft_guard_pct=0.99,
            overall_soft_guard_pct=0.04,
        )
        result = run(rows, [path], scenario=scenario, risk=0.05)
        assert result.outcome == "SOFT_GUARD_OVERALL"
        assert result.near_overall
        assert not result.breach_overall

    def test_near_overall_without_soft_guard(self):
        rows = [gap_loss_bar("2020-01-01T10:00:00+00:00", 81.0)]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=-1.9, reason="stop", exit_price=81.0,
        )
        scenario = Scenario(
            name="wide_daily", daily_loss_pct=0.99, min_trading_days=0,
            carry_phase_2=False, internal_soft_guards=False,
        )
        result = run(rows, [path], scenario=scenario, risk=0.05,
                     horizon="2020-01-02T00:00:00+00:00")
        assert result.outcome == "INCOMPLETE"
        assert result.near_overall
        assert not result.breach_overall

    def test_minimum_trading_days_delay_phase_one(self):
        rows = [win_bar("2020-01-01T10:00:00+00:00")]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=8.0, reason="target", exit_price=180.0,
        )
        result = run(rows, [path], scenario=PRIMARY, horizon="2020-01-03T00:00:00+00:00")
        assert result.outcome == "INCOMPLETE"
        assert not result.phase1_passed
        assert result.trades_taken == 1

    def test_phase_one_passes_after_three_days(self):
        stamps = [
            "2020-01-01T10:00:00+00:00",
            "2020-01-02T10:00:00+00:00",
            "2020-01-03T10:00:00+00:00",
        ]
        rows = [win_bar(ts) for ts in stamps]
        bars = make_bars(rows)
        paths = [
            make_trade(bars, ts, gross_r=8.0, reason="target", exit_price=180.0)
            for ts in stamps
        ]
        result = simulate_attempt(
            paths, 0, bars, pd.Timestamp("2020-01-01T00:00:00+00:00"),
            pd.Timestamp("2020-01-10T00:00:00+00:00"), 0.015, PRIMARY, FP7,
            RULES, config_id="t1",
        )
        assert result.phase1_passed
        assert result.phase_reached == 2
        assert result.phase2_start_balance == 5_000.0
        assert not result.phase2_passed
        assert result.outcome == "INCOMPLETE"

    def test_full_two_phase_pass_and_reset_balance(self):
        stamps = [
            "2020-01-01T10:00:00+00:00",
            "2020-01-02T10:00:00+00:00",
            "2020-01-03T10:00:00+00:00",
            "2020-01-06T10:00:00+00:00",
            "2020-01-07T10:00:00+00:00",
            "2020-01-08T10:00:00+00:00",
        ]
        rows = [win_bar(ts) for ts in stamps]
        bars = make_bars(rows)
        paths = [
            make_trade(bars, ts, gross_r=8.0, reason="target", exit_price=180.0)
            for ts in stamps
        ]
        result = simulate_attempt(
            paths, 0, bars, pd.Timestamp("2020-01-01T00:00:00+00:00"),
            pd.Timestamp("2020-01-10T00:00:00+00:00"), 0.015, PRIMARY, FP7,
            RULES, config_id="t1",
        )
        assert result.outcome == "PASS"
        assert result.phase1_passed and result.phase2_passed
        assert result.phase2_start_balance == 5_000.0
        assert result.days_to_pass == pytest.approx(7.4166667, abs=1e-6)

    def test_carry_phase_two_uses_phase_one_balance(self):
        stamps = [
            "2020-01-01T10:00:00+00:00",
            "2020-01-02T10:00:00+00:00",
            "2020-01-03T10:00:00+00:00",
            "2020-01-06T10:00:00+00:00",
            "2020-01-07T10:00:00+00:00",
            "2020-01-08T10:00:00+00:00",
        ]
        rows = [win_bar(ts) for ts in stamps]
        bars = make_bars(rows)
        paths = [
            make_trade(bars, ts, gross_r=8.0, reason="target", exit_price=180.0)
            for ts in stamps
        ]
        scenario = Scenario(
            name="carry", daily_loss_pct=0.05, min_trading_days=3,
            carry_phase_2=True, internal_soft_guards=True,
        )
        result = simulate_attempt(
            paths, 0, bars, pd.Timestamp("2020-01-01T00:00:00+00:00"),
            pd.Timestamp("2020-01-10T00:00:00+00:00"), 0.015, scenario, FP7,
            RULES, config_id="t1",
        )
        assert result.outcome == "PASS"
        assert result.phase2_start_balance is not None
        assert result.phase2_start_balance > 5_000.0

    def test_phase_one_drawdown_survives_phase_two(self):
        rows = [
            loss_bar("2020-01-01T10:00:00+00:00"),
            win_bar("2020-01-02T10:00:00+00:00"),
            win_bar("2020-01-03T10:00:00+00:00"),
            win_bar("2020-01-06T10:00:00+00:00"),
            win_bar("2020-01-07T10:00:00+00:00"),
            win_bar("2020-01-08T10:00:00+00:00"),
            win_bar("2020-01-09T10:00:00+00:00"),
        ]
        bars = make_bars(rows)
        paths = [
            make_trade(
                bars, "2020-01-01T10:00:00+00:00",
                gross_r=-1.0, reason="stop", exit_price=90.0,
            )
        ] + [
            make_trade(bars, ts, gross_r=8.0, reason="target", exit_price=180.0)
            for ts in [
                "2020-01-02T10:00:00+00:00",
                "2020-01-03T10:00:00+00:00",
                "2020-01-06T10:00:00+00:00",
                "2020-01-07T10:00:00+00:00",
                "2020-01-08T10:00:00+00:00",
                "2020-01-09T10:00:00+00:00",
            ]
        ]
        result = simulate_attempt(
            paths, 0, bars, pd.Timestamp("2020-01-01T00:00:00+00:00"),
            pd.Timestamp("2020-01-10T00:00:00+00:00"), 0.009, PRIMARY, FP7,
            RULES, config_id="t1",
        )
        assert result.outcome == "PASS"
        assert result.max_dd_usd >= 39.9

    def test_min_lot_skip_is_reported(self):
        rows = [win_bar("2020-01-01T10:00:00+00:00")]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=8.0, reason="target", exit_price=180.0,
            stop_distance=20.0,
        )
        result = run(
            rows, [path], scenario=PRIMARY, risk=0.001,
            horizon="2020-01-10T00:00:00+00:00",
        )
        assert result.outcome == "INCOMPLETE"
        assert result.trades_taken == 0
        assert result.skipped_min_lot == 1

    def test_inactivity_failure(self):
        rows = [win_bar("2020-01-01T10:00:00+00:00")]
        path = make_trade(
            make_bars(rows), "2020-01-01T10:00:00+00:00",
            gross_r=1.0, reason="target", exit_price=110.0,
        )
        result = run(
            rows, [path], scenario=PRIMARY, risk=0.015,
            horizon="2020-03-01T00:00:00+00:00",
        )
        assert result.outcome == "FAIL_INACTIVITY"
        assert result.inactivity
        assert result.end_utc is not None
        assert "2020-01-31" in result.end_utc
