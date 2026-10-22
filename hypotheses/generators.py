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
    signal = np.where(frame["close"] > prior_high, 1, np.where(frame["close"] < prior_low, -1, 0))
    return SignalPlan({i: int(v) for i, v in enumerate(signal) if v}, 1.0, 1.5, 24, minimum)


def prior_day_sweep(frame: pd.DataFrame, params: dict[str, float]) -> SignalPlan:
    minimum = 200
    _base(frame, minimum)
    day = frame.index.floor("D")
    daily = frame.groupby(day).agg(high=("high", "max"), low=("low", "min")).shift(1)
    prior_high = day.map(daily["high"])
    prior_low = day.map(daily["low"])
    signal = np.where((frame["low"] < prior_low) & (frame["close"] > prior_low), 1,
                      np.where((frame["high"] > prior_high) & (frame["close"] < prior_high), -1, 0))
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
