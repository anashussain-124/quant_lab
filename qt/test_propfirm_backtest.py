import numpy as np
import pandas as pd
import pytest

from propfirm_backtest import (Backtester, Costs, EmaZMeanReversion, Instrument,
                               PRESETS, PropRules, RiskConfig, prepare, synthetic_data)

ZERO = Costs(commission_per_lot_side=0.0, slippage=0.0, fixed_spread_points=0.0)
RISK = RiskConfig(entry_hours_utc=(0, 24), flatten_hour_utc=None,
                  friday_flatten_hour_utc=None, soft_daily_stop_pct=0.5,
                  risk_per_trade=0.005)
RULES = PropRules(max_calendar_days=None, min_trading_days=1)


def make(bars, sig_rows):
    idx = pd.date_range("2024-03-04 10:00", periods=len(bars), freq="5min", tz="UTC")
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    sig = pd.DataFrame({"side": 0, "stop_dist": np.nan, "target": np.nan,
                        "exit_long": False, "exit_short": False}, index=idx)
    for i, vals in sig_rows.items():
        for k, v in vals.items():
            sig.iloc[i, sig.columns.get_loc(k)] = v
    sig["side"] = sig["side"].astype(np.int8)
    return df, sig


def run(df, sig, rules=RULES, costs=ZERO):
    bt = Backtester(Instrument(), costs, rules, RISK)
    return bt.run(prepare(df, sig, bt.inst, costs, rules), stop_on_result=False)


def test_tp_accounting_exact():
    # risk 0.5% of 100k = 500; stop 5 * 100 oz = 500/lot -> 1.0 lot; TP +10 -> +1000
    bars = [[2000, 2001, 1999, 2000], [2000, 2011, 1999, 2010], [2010, 2010, 2009, 2010]]
    df, sig = make(bars, {0: {"side": 1, "stop_dist": 5.0, "target": 2010.0}})
    r = run(df, sig)
    assert len(r.trades) == 1
    assert r.trades.iloc[0].volume == pytest.approx(1.0)
    assert r.trades.iloc[0].pnl == pytest.approx(1000.0)
    assert r.trades.iloc[0].reason == "TP"


def test_sl_before_tp_when_both_touch():
    bars = [[2000, 2001, 1999, 2000], [2000, 2020, 1990, 2005], [2005, 2005, 2005, 2005]]
    df, sig = make(bars, {0: {"side": 1, "stop_dist": 5.0, "target": 2010.0}})
    r = run(df, sig)
    assert r.trades.iloc[0].reason == "SL"
    assert r.trades.iloc[0].pnl == pytest.approx(-500.0)


def test_gap_through_stop_fills_at_open():
    bars = [[2000, 2001, 1999, 2000], [2000, 2001, 1999, 2000], [1990, 1991, 1989, 1990],
            [1990, 1990, 1990, 1990]]
    df, sig = make(bars, {0: {"side": 1, "stop_dist": 5.0}})
    r = run(df, sig)
    assert r.trades.iloc[0].exit == pytest.approx(1990.0)


def test_short_pays_spread():
    costs = Costs(0.0, 0.0, 100.0)  # 100 pts = 1.00 spread
    bars = [[2000, 2000, 2000, 2000], [2000, 2000, 1989, 1990], [1990, 1990, 1990, 1990]]
    df, sig = make(bars, {0: {"side": -1, "stop_dist": 5.0, "target": 1990.0}})
    r = run(df, sig, costs=costs)
    t = r.trades.iloc[0]
    assert t.entry == pytest.approx(2000.0)      # shorts sell at bid
    assert t.exit == pytest.approx(1990.0)       # TP on ask side: low + spread = 1990


def test_daily_loss_breach_fails_challenge():
    from dataclasses import replace
    rules = PropRules(max_calendar_days=None, max_daily_loss_pct=0.004,
                      min_trading_days=1)
    bars = [[2000, 2001, 1999, 2000], [2000, 2001, 1999, 2000], [2000, 2001, 1975, 1976]]
    df, sig = make(bars, {0: {"side": 1, "stop_dist": 20.0}})
    bt = Backtester(Instrument(), ZERO, rules, replace(RISK, cap_risk_to_daily_room=False))
    r = bt.run(prepare(df, sig, bt.inst, ZERO, rules), stop_on_result=False)
    assert r.outcome == "FAIL_DAILY_LOSS"


def test_daily_room_cap_prevents_breach():
    rules = PropRules(max_calendar_days=None, max_daily_loss_pct=0.004,
                      min_trading_days=1)
    bars = [[2000, 2001, 1999, 2000], [2000, 2001, 1999, 2000], [2000, 2001, 1975, 1976]]
    df, sig = make(bars, {0: {"side": 1, "stop_dist": 20.0}})
    r = run(df, sig, rules=rules)
    assert r.outcome != "FAIL_DAILY_LOSS"
    assert r.trades.iloc[0].pnl >= -0.8 * 400 - 1e-6


def test_no_lookahead_in_signals():
    df = synthetic_data(days=20)
    s = EmaZMeanReversion()
    full = s.generate(df)
    cut = 1500
    part = s.generate(df.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part, check_exact=False)


def test_deterministic():
    df = synthetic_data(days=30)
    sig = EmaZMeanReversion().generate(df)
    a, b = run(df, sig, costs=Costs()), run(df, sig, costs=Costs())
    assert a.final_balance == b.final_balance


def test_fundingpips_standard_presets():
    p1 = PRESETS["fundingpips_2step_p1"]
    p2 = PRESETS["fundingpips_2step_p2"]
    p1_3pct = PRESETS["fundingpips_2step_p1_3pct_daily"]
    p2_3pct = PRESETS["fundingpips_2step_p2_3pct_daily"]
    for rules in (p1, p2, p1_3pct, p2_3pct):
        assert rules.initial_balance == 5_000.0
        assert rules.max_total_loss_pct == pytest.approx(0.10)
        assert rules.trailing_total is False
        assert rules.max_calendar_days is None
        assert rules.reset_tz == "UTC"
        assert rules.reset_hour == 21
    assert p1.max_daily_loss_pct == pytest.approx(0.05)
    assert p2.max_daily_loss_pct == pytest.approx(0.05)
    assert p1.min_trading_days == 3
    assert p2.min_trading_days == 3
    assert p1.profit_target_pct == pytest.approx(0.08)
    assert p2.profit_target_pct == pytest.approx(0.05)
    assert p1_3pct.max_daily_loss_pct == pytest.approx(0.03)
    assert p2_3pct.max_daily_loss_pct == pytest.approx(0.03)
    assert p1_3pct.min_trading_days == 0
    assert p2_3pct.min_trading_days == 0


def test_daily_loss_uses_higher_of_opening_balance_or_equity():
    # Day 1 gains $500, raising balance to $5,500. On day 2, a $274.4
    # loss must remain inside the 5% daily limit ($275), rather than being
    # incorrectly rejected against 5% of the original $5,000 ($250).
    from dataclasses import replace

    idx = pd.to_datetime([
        "2024-03-04 10:00Z",
        "2024-03-04 10:05Z",
        "2024-03-04 10:10Z",
        "2024-03-05 10:00Z",
        "2024-03-05 10:05Z",
        "2024-03-05 10:10Z",
    ])
    bars = [
        [2000, 2001, 1999, 2000],
        [2000, 2011, 1999, 2010],
        [2010, 2010, 2009, 2010],
        [2000, 2001, 1999, 2000],
        [2000, 2001, 1994, 1995],
        [1995, 1995, 1995, 1995],
    ]
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    sig = pd.DataFrame({"side": 0, "stop_dist": np.nan, "target": np.nan,
                        "exit_long": False, "exit_short": False}, index=idx)
    sig.iloc[0, sig.columns.get_loc("side")] = 1
    sig.iloc[0, sig.columns.get_loc("stop_dist")] = 5.0
    sig.iloc[0, sig.columns.get_loc("target")] = 2010.0
    sig.iloc[3, sig.columns.get_loc("side")] = 1
    sig.iloc[3, sig.columns.get_loc("stop_dist")] = 4.9
    sig["side"] = sig["side"].astype(np.int8)

    rules = PropRules(initial_balance=5_000, profit_target_pct=0.50,
                      max_daily_loss_pct=0.05, max_total_loss_pct=0.50,
                      min_trading_days=1, max_calendar_days=None,
                      reset_tz="UTC", reset_hour=21)
    costs = ZERO
    risk = replace(RISK, risk_per_trade=0.05, cap_risk_to_daily_room=False)
    bt = Backtester(Instrument(), costs, rules, risk)
    result = bt.run(prepare(df, sig, bt.inst, costs, rules), stop_on_result=False)

    assert result.trades.iloc[0].pnl == pytest.approx(500.0)
    assert result.trades.iloc[1].pnl == pytest.approx(-274.4)
    assert result.outcome != "FAIL_DAILY_LOSS"


def test_donchian_strategy_is_causal_and_aligned():
    from propfirm_backtest import DonchianTrendBreakout
    df = synthetic_data(days=25)
    strategy = DonchianTrendBreakout()
    full = strategy.generate(df)
    cut = 2000
    partial = strategy.generate(df.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], partial, check_exact=False)
    assert full.index.equals(df.index)
    assert set(full.side.unique()).issubset({-1, 0, 1})


def test_donchian_strategy_only_signals_in_london_window():
    from propfirm_backtest import DonchianTrendBreakout
    df = synthetic_data(days=25)
    sig = DonchianTrendBreakout().generate(df)
    local = sig.index.tz_convert("Europe/London")
    outside = (local.hour < 8) | (local.hour >= 12)
    assert (sig.loc[outside, "side"] == 0).all()


def test_donchian_parameters_reject_invalid_ema_order():
    from propfirm_backtest import DonchianTrendBreakout, DonchianTrendParams
    with pytest.raises(ValueError):
        DonchianTrendBreakout(DonchianTrendParams(ema_fast=200, ema_slow=50))


def test_rolling_challenge_rejects_impossible_fast_pass(monkeypatch):
    from propfirm_backtest import BacktestResult, rolling_challenges
    df = synthetic_data(days=40)
    sig = EmaZMeanReversion().generate(df)
    rules = PropRules(initial_balance=5000, min_trading_days=3, max_calendar_days=None)
    bt = Backtester(rules=rules)
    prepared = prepare(df, sig, bt.inst, bt.costs, rules)
    fake_trades = pd.DataFrame(columns=["pnl", "reason"])
    fake_equity = pd.Series([5000.0], index=[df.index[0]])
    def fake_run(P, i0=0, i1=None, stop_on_result=True):
        return BacktestResult("PASS", P.index[i0], P.index[i0] + pd.Timedelta(hours=2),
                              2/24, 3, 5250, 5.0, 0.0, 0.0, fake_trades, fake_equity)
    monkeypatch.setattr(bt, "run", fake_run)
    attempts = rolling_challenges(bt, prepared, step_days=7, warmup_bars=300)
    assert not attempts.empty
    assert (attempts.outcome == "INVALID_SIMULATION").all()
