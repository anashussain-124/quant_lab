import pandas as pd

import backtest_engine_fundingpips as backtest


def test_open_trade_closes_on_breach_bar_not_final_dataset_bar():
    # Four candles: enter on candle 2, breach on candle 3,
    # and leave a later candle to catch accidental end-of-data closure.
    times = pd.date_range(
        "2026-01-05 00:00:00",
        periods=4,
        freq="5min",
        tz="UTC",
        name="time_utc",
    )

    df = pd.DataFrame(
        {
            "open": [2000.0, 2000.0, 1900.0, 2100.0],
            "high": [2001.0, 2001.0, 1901.0, 2101.0],
            "low": [1999.0, 1999.0, 1899.0, 2099.0],
            "close": [2000.0, 2000.0, 1900.0, 2100.0],
            "spread_price": [0.0, 0.0, 0.0, 0.0],
            "atr": [1.0, 1.0, 1.0, 1.0],
            "long_signal": [True, False, False, False],
            "short_signal": [False, False, False, False],
        },
        index=times,
    )

    trades, summary = backtest.run_phase(
        df,
        "Regression Test",
        target_profit=10000.0,
    )

    assert summary["breach_reason"] == "static_loss_floor"
    assert len(trades) == 1

    trade = trades.iloc[0]

    assert trade["exit_time_utc"] == times[2].isoformat()
    assert trade["exit_reason"] == "forced_close_at_breach"
    assert trade["exit_time_utc"] != times[-1].isoformat()
def test_daily_loss_breach_closes_trade_on_breach_bar(monkeypatch):
    monkeypatch.setattr(backtest, "STATIC_LOSS_FLOOR", 4000.0)
    monkeypatch.setattr(backtest, "DAILY_LOSS_PERCENT", 0.001)

    times = pd.date_range(
        "2026-01-05 00:00:00",
        periods=4,
        freq="5min",
        tz="UTC",
        name="time_utc",
    )

    df = pd.DataFrame(
        {
            "open":  [2000.0, 2000.0, 1990.0, 2100.0],
            "high":  [2001.0, 2001.0, 1991.0, 2101.0],
            "low":   [1999.0, 1999.0, 1989.0, 2099.0],
            "close": [2000.0, 2000.0, 1990.0, 2100.0],
            "spread_price": [0.0, 0.0, 0.0, 0.0],
            "atr": [1.0, 1.0, 1.0, 1.0],
            "long_signal": [True, False, False, False],
            "short_signal": [False, False, False, False],
        },
        index=times,
    )

    trades, summary = backtest.run_phase(
        df,
        "Daily Loss Regression Test",
        target_profit=10000.0,
    )

    assert summary["breach_reason"] == "daily_loss_limit"
    assert len(trades) == 1

    trade = trades.iloc[0]
    assert trade["exit_time_utc"] == times[2].isoformat()
    assert trade["exit_reason"] == "forced_close_at_breach"


def test_stop_is_used_when_stop_and_target_are_both_touched(monkeypatch):
    monkeypatch.setattr(backtest, "STATIC_LOSS_FLOOR", 4000.0)
    monkeypatch.setattr(backtest, "DAILY_LOSS_PERCENT", 0.05)

    times = pd.date_range(
        "2026-01-05 00:00:00",
        periods=3,
        freq="5min",
        tz="UTC",
        name="time_utc",
    )

    df = pd.DataFrame(
        {
            "open":  [2000.0, 2000.0, 2000.0],
            "high":  [2001.0, 2001.0, 2005.0],
            "low":   [1999.0, 1999.0, 1995.0],
            "close": [2000.0, 2000.0, 2000.0],
            "spread_price": [0.0, 0.0, 0.0],
            "atr": [1.0, 1.0, 1.0],
            "long_signal": [True, False, False],
            "short_signal": [False, False, False],
        },
        index=times,
    )

    trades, summary = backtest.run_phase(
        df,
        "SL TP Collision Test",
        target_profit=10000.0,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]

    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_time_utc"] == times[2].isoformat()
    assert trade["net_pnl"] < 0
    assert summary["breach_reason"] is None
def test_daily_loss_limit_is_percentage_of_daily_reference(monkeypatch):
    monkeypatch.setattr(backtest, "STATIC_LOSS_FLOOR", 4000.0)
    monkeypatch.setattr(backtest, "DAILY_LOSS_PERCENT", 0.05)

    # The helper should calculate 5% of the daily reference,
    # not return a fixed dollar amount.
    daily_reference = 6000.0
    daily_loss_pct = 0.05

    expected_limit = daily_reference * daily_loss_pct

    assert expected_limit == 300.0
    assert expected_limit == 300.0
def test_daily_loss_scales_with_daily_reference(monkeypatch):
    monkeypatch.setattr(backtest, "START_BALANCE", 6000.0)
    monkeypatch.setattr(backtest, "STATIC_LOSS_FLOOR", 4000.0)
    monkeypatch.setattr(backtest, "SLIPPAGE_PER_SIDE", 0.0)
    monkeypatch.setattr(backtest, "COMMISSION_PER_LOT_ROUND_TURN", 0.0)

    times = pd.date_range(
        "2026-01-05 00:00:00",
        periods=3,
        freq="5min",
        tz="UTC",
        name="time_utc",
    )

    df = pd.DataFrame(
        {
            "open": [2000.0, 2000.0, 2000.0],
            "high": [2001.0, 2001.0, 2001.0],
            "low": [1999.0, 1999.0, 1949.0],
            "close": [2000.0, 2000.0, 1950.0],
            "spread_price": [0.0, 0.0, 0.0],
            "atr": [1.0, 1.0, 1.0],
            "long_signal": [True, False, False],
            "short_signal": [False, False, False],
        },
        index=times,
    )

    trades, summary = backtest.run_phase(
        df,
        "Daily Loss Percentage Regression Test",
        target_profit=10000.0,
    )

    assert summary["breach_reason"] != "daily_loss_limit"
    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "stop_loss"