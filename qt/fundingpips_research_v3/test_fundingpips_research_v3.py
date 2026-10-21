from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from fundingpips_research_v3 import (
    AsianRangeBreakout, Backtester, Costs, DonchianBaseline, FailedBreakoutReversal,
    Instrument, PropRules, RiskConfig, TrendPullbackContinuation,
    _normalize_frame, apply_news_blackout, load_csv, prepare, sequential_challenge,
    synthetic_data, walk_forward_evaluation,
)

ZERO = Costs(commission_per_lot_side=0.0, slippage=0.0, fixed_spread_points=0.0)
RISK = RiskConfig(risk_per_trade=0.005, soft_daily_stop_pct=0.50,
                  soft_total_drawdown_pct=0.90, cap_risk_to_daily_room=False,
                  entry_start_london=8, entry_end_london=12,
                  flatten_at_london_end=False, max_hold_bars=None)
RULES = PropRules(initial_balance=100_000, profit_target_pct=0.50,
                  max_daily_loss_pct=0.05, max_total_loss_pct=0.50,
                  min_trading_days=1, inactivity_days=30, max_calendar_days=None)


def bars_frame(bars, start="2024-03-04 09:00:00+00:00", spread=None):
    idx = pd.date_range(start, periods=len(bars), freq="5min", tz="UTC")
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    if spread is not None:
        df["spread"] = spread
    return df


def signal_frame(df, entries):
    sig = pd.DataFrame({"side": np.zeros(len(df), dtype=np.int8),
                        "stop_dist": np.full(len(df), np.nan),
                        "target": np.full(len(df), np.nan),
                        "exit_long": np.zeros(len(df), dtype=bool),
                        "exit_short": np.zeros(len(df), dtype=bool)}, index=df.index)
    for i, values in entries.items():
        for col, value in values.items():
            sig.iloc[i, sig.columns.get_loc(col)] = value
    sig["side"] = sig.side.astype(np.int8)
    return sig


def run_one(df, sig, rules=RULES, costs=ZERO, risk=RISK, inst=Instrument()):
    bt = Backtester(inst, costs, rules, risk)
    p = prepare(df, sig, inst, costs, rules)
    return bt.run(p, enforce_prop_rules=True, stop_on_result=False)


def test_mt5_style_naive_utc_csv_is_not_shifted(tmp_path):
    idx = pd.date_range("2024-01-02 00:00", periods=501, freq="5min")
    csv = tmp_path / "bars.csv"
    frame = pd.DataFrame({"time": idx, "open": 2000, "high": 2001,
                          "low": 1999, "close": 2000, "spread": 20})
    frame.to_csv(csv, index=False)
    got = load_csv(csv, timestamp_mode="utc")
    assert got.index[0] == pd.Timestamp("2024-01-02 00:00:00", tz="UTC")


def test_broker_time_csv_applies_only_explicit_offset(tmp_path):
    idx = pd.date_range("2024-01-02 03:00", periods=501, freq="5min")
    csv = tmp_path / "bars.csv"
    pd.DataFrame({"time": idx, "open": 2000, "high": 2001,
                  "low": 1999, "close": 2000}).to_csv(csv, index=False)
    got = load_csv(csv, timestamp_mode="broker", server_utc_offset=3)
    assert got.index[0] == pd.Timestamp("2024-01-02 00:00:00", tz="UTC")


def test_bad_ohlc_is_rejected():
    idx = pd.date_range("2024-01-01", periods=501, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": 2000., "high": 1999., "low": 1998., "close": 1998.5}, index=idx)
    with pytest.raises(ValueError, match="high >= low|outside high-low"):
        _normalize_frame(df)


def test_tp_accounting_exact():
    df = bars_frame([[2000, 2001, 1999, 2000], [2000, 2011, 1999, 2010], [2010, 2010, 2009, 2010]])
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 5.0, "target": 2010.0}})
    r = run_one(df, sig)
    assert len(r.trades) == 1
    assert r.trades.iloc[0].volume == pytest.approx(1.0)
    assert r.trades.iloc[0].pnl == pytest.approx(1000.0)
    assert r.trades.iloc[0].reason == "TP"


def test_stop_wins_if_stop_and_target_touch_same_bar():
    df = bars_frame([[2000, 2001, 1999, 2000], [2000, 2020, 1990, 2005], [2005, 2005, 2005, 2005]])
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 5.0, "target": 2010.0}})
    r = run_one(df, sig)
    assert r.trades.iloc[0].reason == "SL"
    assert r.trades.iloc[0].exit == pytest.approx(1995.0)
    assert r.trades.iloc[0].pnl == pytest.approx(-500.0)


def test_gap_through_stop_fills_at_open_not_stop():
    df = bars_frame([[2000, 2001, 1999, 2000], [2000, 2001, 1999, 2000],
                     [1990, 1991, 1989, 1990], [1990, 1990, 1990, 1990]])
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 5.0}})
    r = run_one(df, sig)
    assert r.trades.iloc[0].reason == "SL_GAP"
    assert r.trades.iloc[0].exit == pytest.approx(1990.0)


def test_short_spread_is_paid_on_entry_and_exit_quote():
    costs = Costs(commission_per_lot_side=0, slippage=0, fixed_spread_points=100)
    df = bars_frame([[2000, 2000, 2000, 2000], [2000, 2000, 1989, 1990], [1990, 1990, 1990, 1990]], spread=[100, 100, 100])
    sig = signal_frame(df, {0: {"side": -1, "stop_dist": 5.0, "target": 1990.0}})
    inst = Instrument(point=0.01)
    r = run_one(df, sig, costs=costs, inst=inst)
    t = r.trades.iloc[0]
    assert t.entry == pytest.approx(2000.0)
    assert t.exit == pytest.approx(1990.0)
    assert t.pnl == pytest.approx(1000.0)


def test_session_flat_closes_position_exactly_once():
    df = bars_frame([[2000, 2001, 1999, 2000], [2000, 2001, 1999, 2000], [2000, 2001, 1999, 2000]], start="2024-03-04 11:50:00+00:00")
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 5.0, "target": 2020.0}})
    risk = replace(RISK, flatten_at_london_end=True, entry_start_london=8, entry_end_london=12)
    r = run_one(df, sig, risk=risk)
    assert len(r.trades) == 1
    assert r.trades.iloc[0].reason == "SESSION_FLAT"


def test_intrabar_daily_limit_breach_is_reported():
    df = bars_frame([[2000, 2001, 1999, 2000], [2000, 2001, 1970, 1971], [1971, 1971, 1971, 1971]])
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 20.0}})
    rules = replace(RULES, max_daily_loss_pct=0.004)
    r = run_one(df, sig, rules=rules, risk=replace(RISK, cap_risk_to_daily_room=False))
    assert r.outcome == "FAIL_DAILY_LOSS"
    assert r.hard_breach


def test_position_size_respects_cash_risk_budget():
    bt = Backtester(Instrument(), Costs(2.5, 0.05, 30), RULES, RISK)
    vol, actual_risk = bt._volume_for_cash(12.5, 5.0)
    worst_case_per_lot = (5.0 + 0.05) * 100 + 5.0
    assert vol >= 0
    assert vol * worst_case_per_lot <= 12.5 + 1e-8
    assert actual_risk <= 12.5


@pytest.mark.parametrize("strategy", [AsianRangeBreakout(), FailedBreakoutReversal(), TrendPullbackContinuation(), DonchianBaseline()])
def test_all_strategies_are_causal_and_aligned(strategy):
    df = synthetic_data(days=40, seed=13)
    full = strategy.generate(df)
    cut = 7000
    partial = strategy.generate(df.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], partial, check_exact=False)
    assert full.index.equals(df.index)
    assert set(full.side.unique()).issubset({-1, 0, 1})
    assert {"side", "stop_dist", "target", "exit_long", "exit_short"}.issubset(full.columns)


def test_donchian_only_signals_during_london_window():
    df = synthetic_data(days=40, seed=17)
    sig = DonchianBaseline().generate(df)
    close_local = (sig.index + pd.Timedelta(minutes=5)).tz_convert("Europe/London")
    outside = (close_local.hour < 8) | (close_local.hour >= 12)
    assert (sig.loc[outside, "side"] == 0).all()


def test_fundingpips_daily_reset_bucket_is_utc_plus_3_midnight():
    idx = pd.to_datetime(["2024-01-01 20:55Z", "2024-01-01 21:00Z", "2024-01-01 21:05Z"])
    df = pd.DataFrame({"open": 2000, "high": 2001, "low": 1999, "close": 2000}, index=idx)
    sig = signal_frame(df, {})
    p = prepare(df, sig, Instrument(), ZERO, PropRules(daily_reset_utc_hour=21))
    assert p.day_id[0] != p.day_id[1]
    assert p.day_id[1] == p.day_id[2]


def test_sequential_challenge_phase_2_starts_after_phase_1_pass():
    # Construct deterministic winner data: one $20 win on each weekday.
    rows, signals = [], {}
    timestamps = []
    days = pd.bdate_range("2024-03-04", periods=40)
    for day in days:
        base = pd.Timestamp(day.date(), tz="UTC") + pd.Timedelta(hours=9)
        i = len(rows)
        timestamps.extend([base, base + pd.Timedelta(minutes=5), base + pd.Timedelta(minutes=10)])
        rows.extend([[2000, 2001, 1999, 2000], [2000, 2012, 1999, 2010], [2010, 2011, 2009, 2010]])
        signals[i] = {"side": 1, "stop_dist": 5.0, "target": 2010.0}
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(timestamps))
    sig = signal_frame(df, signals)
    inst = Instrument()
    costs = ZERO
    risk = replace(RISK, risk_per_trade=0.0025, soft_daily_stop_pct=0.50,
                   soft_total_drawdown_pct=0.90, max_entries_per_day=1,
                   flatten_at_london_end=False, max_hold_bars=None)
    p1 = replace(RULES, initial_balance=5000, profit_target_pct=0.08, max_daily_loss_pct=0.05,
                 max_total_loss_pct=0.10, min_trading_days=3, inactivity_days=30)
    p2 = replace(p1, profit_target_pct=0.05)
    prep = prepare(df, sig, inst, costs, p1)
    out = sequential_challenge(prep, inst, costs, risk, rules_p1=p1, rules_p2=p2)
    assert out["p1"].outcome == "PASS"
    assert out["p2"] is not None
    assert out["p2"].outcome == "PASS"
    assert out["outcome"] == "PASS_BOTH"
    assert out["p2"].start_index > out["p1"].end_index


def test_intrabar_daily_breach_takes_precedence_over_same_bar_target():
    # The low stays above SL but crosses daily loss; the high also touches TP.
    # OHLC cannot resolve which came first, so the engine conservatively records breach.
    df = bars_frame([[2000, 2001, 1999, 2000],
                     [2000, 2051, 1955, 2045],
                     [2045, 2046, 2044, 2045]])
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 50.0, "target": 2050.0}})
    rules = replace(RULES, max_daily_loss_pct=0.004)
    risk = replace(RISK, risk_per_trade=0.005, cap_risk_to_daily_room=False)
    r = run_one(df, sig, rules=rules, risk=risk)
    assert r.outcome == "FAIL_DAILY_LOSS"
    assert r.trades.iloc[0].reason == "FAIL_DAILY_LOSS"


def test_no_completed_trade_for_30_days_triggers_inactivity_breach():
    # Bars around the London session with no signals, spanning over 30 calendar days.
    ts = []
    for day in pd.bdate_range("2024-03-04", periods=35):
        base = pd.Timestamp(day.date(), tz="UTC") + pd.Timedelta(hours=9)
        ts.append(base)
    idx = pd.DatetimeIndex(ts)
    df = pd.DataFrame({"open": 2000., "high": 2001., "low": 1999., "close": 2000.}, index=idx)
    sig = signal_frame(df, {})
    # Prepare accepts sparse testing rows; daily bars are sufficient to test date clocks.
    rules = replace(RULES, inactivity_days=30)
    r = run_one(df, sig, rules=rules)
    assert r.outcome == "FAIL_INACTIVITY"
    assert r.hard_breach


def test_news_blackout_suppresses_entries_but_preserves_exit_signals(tmp_path):
    df = bars_frame([[2000, 2001, 1999, 2000] for _ in range(8)], start="2024-03-04 09:00:00+00:00")
    sig = signal_frame(df, {1: {"side": 1, "stop_dist": 2.0, "target": 2004.0,
                               "exit_long": True}})
    news = tmp_path / "news.csv"
    pd.DataFrame({"time_utc": ["2024-03-04T09:10:00Z"]}).to_csv(news, index=False)
    masked, count = apply_news_blackout(df, sig, news, before_minutes=5, after_minutes=5)
    assert count == 1
    assert masked.side.iloc[1] == 0
    assert masked.exit_long.iloc[1] is True or bool(masked.exit_long.iloc[1]) is True


def test_daily_loss_uses_higher_of_opening_balance_or_equity():
    idx = pd.to_datetime(["2024-03-04 09:00Z", "2024-03-04 09:05Z", "2024-03-04 09:10Z",
                          "2024-03-05 09:00Z", "2024-03-05 09:05Z", "2024-03-05 09:10Z"])
    bars = [[2000, 2001, 1999, 2000], [2000, 2011, 1999, 2010], [2010, 2010, 2009, 2010],
            [2000, 2001, 1999, 2000], [2000, 2001, 1994, 1995], [1995, 1995, 1995, 1995]]
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    sig = signal_frame(df, {0: {"side": 1, "stop_dist": 5.0, "target": 2010.0},
                            3: {"side": 1, "stop_dist": 4.9}})
    rules = replace(RULES, initial_balance=5000, profit_target_pct=0.50,
                    max_daily_loss_pct=0.05, max_total_loss_pct=0.50,
                    min_trading_days=1, daily_reset_utc_hour=21)
    risk = replace(RISK, risk_per_trade=0.05, cap_risk_to_daily_room=False)
    r = run_one(df, sig, rules=rules, risk=risk)
    assert r.trades.iloc[0].pnl == pytest.approx(500.0)
    assert r.trades.iloc[1].pnl == pytest.approx(-274.4)
    assert r.outcome != "FAIL_DAILY_LOSS"


def test_walk_forward_evaluation_uses_chronological_test_windows():
    df = synthetic_data(days=100, seed=21)
    sig = DonchianBaseline().generate(df)
    inst, costs, rules = Instrument(), ZERO, RULES
    prep = prepare(df, sig, inst, costs, rules)
    bt = Backtester(inst, costs, rules, replace(RISK, flatten_at_london_end=False))
    wf = walk_forward_evaluation(prep, bt, train_days=20, test_days=15, step_days=15)
    assert not wf.empty
    assert wf.test_start.is_monotonic_increasing
    assert (pd.to_datetime(wf.test_end) > pd.to_datetime(wf.test_start)).all()
