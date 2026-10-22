"""Five bounded, causal signal generators for dry-run research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from features.causal import causal_ema, causal_zscore, prior_donchian_high, prior_donchian_low
from hypotheses.registry import Hypothesis


@dataclass(frozen=True)
class SignalPlan:
    signals: dict[int, int]
    stop_distance: float
    target_distance: float
    max_hold: int
    min_history: int


def _base(frame: pd.DataFrame, minimum: int) -> None:
    if len(frame) < minimum:
        raise ValueError("insufficient history")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("signals require a timezone-aware index")


def opening_range_breakout(frame: pd.DataFrame, params: dict[str, float]) -> SignalPlan:
    minimum = 60
    _base(frame, minimum)
    range_bars = max(1, int(params["range_minutes"] / 5))
    prior_high = frame["high"].rolling(range_bars, min_periods=range_bars).max().shift(1)
    prior_low = frame["low"].rolling(range_bars, min_periods=range_bars).min().shift(1)
    buffer = (prior_high - prior_low) * float(params["buffer_r"])
    signal = np.where(frame["close"] > prior_high + buffer, 1,
                      np.where(frame["close"] < prior_low - buffer, -1, 0))
    return SignalPlan({i: int(v) for i, v in enumerate(signal) if v}, 1.0, 1.5, 24, minimum)


def prior_day_sweep(frame: pd.DataFrame, params: dict[str, float]) -> SignalPlan:
    minimum = 200
    _base(frame, minimum)
    day = frame.index.floor("D")
    lookback_days = max(1, int(params["lookback_days"]))
    reclaim_bars = max(1, int(params["reclaim_bars"]))
    daily = frame.groupby(day).agg(high=("high", "max"), low=("low", "min"))
    prior_high_daily = daily["high"].rolling(lookback_days, min_periods=lookback_days).max().shift(1)
    prior_low_daily = daily["low"].rolling(lookback_days, min_periods=lookback_days).min().shift(1)
    prior_high = day.map(prior_high_daily)
    prior_low = day.map(prior_low_daily)
    low_sweep = (frame["low"] < prior_low).astype(int)
    high_sweep = (frame["high"] > prior_high).astype(int)
    low_recent = low_sweep.rolling(reclaim_bars, min_periods=1).sum() > 0
    high_recent = high_sweep.rolling(reclaim_bars, min_periods=1).sum() > 0
    signal = np.where(low_recent & (frame["close"] > prior_low), 1,
                      np.where(high_recent & (frame["close"] < prior_high), -1, 0))
    return SignalPlan({i: int(v) for i, v in enumerate(signal) if v}, 1.0, 1.5, 48, minimum)


def volatility_trend(frame: pd.DataFrame, params: dict[str, float]) -> SignalPlan:
    minimum = 150
    _base(frame, minimum)
    fast = causal_ema(frame["close"], int(params["ema_fast"]))
    slow = causal_ema(frame["close"], int(params["ema_slow"]))
    signal = np.where(fast > slow, 1, np.where(fast < slow, -1, 0))
    return SignalPlan({i: int(v) for i, v in enumerate(signal) if v}, 1.0, 2.0, 72, minimum)


def zscore_mean_reversion(frame: pd.DataFrame, params: dict[str, float]) -> SignalPlan:
    minimum = 100
    _base(frame, minimum)
    z = causal_zscore(frame["close"], int(params["window"]))
    threshold = params["entry_z"]
    signal = np.where(z > threshold, -1, np.where(z < -threshold, 1, 0))
    return SignalPlan({i: int(v) for i, v in enumerate(signal) if v}, 1.0, 1.0, 24, minimum)


def session_drift(frame: pd.DataFrame, params: dict[str, float]) -> SignalPlan:
    minimum = 100
    _base(frame, minimum)
    hours = frame.index.hour
    active = (hours >= int(params["start_hour_utc"])) & (hours < int(params["end_hour_utc"]))
    direction = np.sign(frame["close"].diff().shift(1).fillna(0))
    signal = np.where(active, direction, 0)
    return SignalPlan({i: int(v) for i, v in enumerate(signal) if v}, 1.0, 1.0, 24, minimum)


GENERATORS: dict[str, Callable[[pd.DataFrame, dict[str, float]], SignalPlan]] = {
    "opening_range_breakout": opening_range_breakout,
    "prior_day_sweep": prior_day_sweep,
    "volatility_trend": volatility_trend,
    "zscore_mean_reversion": zscore_mean_reversion,
    "session_drift": session_drift,
}
