"""Small, causal feature primitives for the v4 research pipeline.

Every feature is computed from the current row and earlier rows only.  A
caller that uses a feature to decide at the close of bar ``t`` must shift it
by one bar when the feature describes a level to be compared with bar ``t``.
"""

from __future__ import annotations

import pandas as pd


def _require_utc(index: pd.Index) -> None:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("feature inputs require a timezone-aware DatetimeIndex")


def causal_ema(values: pd.Series, span: int) -> pd.Series:
    """Return an EMA with no centered or future-looking observations."""
    if span < 1:
        raise ValueError("span must be positive")
    _require_utc(values.index)
    return values.astype(float).ewm(span=span, adjust=False, min_periods=span).mean()


def causal_zscore(values: pd.Series, window: int) -> pd.Series:
    """Return a rolling z-score using observations through the current row."""
    if window < 2:
        raise ValueError("window must be at least two")
    _require_utc(values.index)
    rolling = values.astype(float).rolling(window=window, min_periods=window)
    return (values - rolling.mean()) / rolling.std(ddof=1)


def prior_donchian_high(high: pd.Series, window: int) -> pd.Series:
    """Highest high in the prior ``window`` bars, aligned to the current bar."""
    if window < 1:
        raise ValueError("window must be positive")
    _require_utc(high.index)
    return high.astype(float).rolling(window=window, min_periods=window).max().shift(1)


def prior_donchian_low(low: pd.Series, window: int) -> pd.Series:
    """Lowest low in the prior ``window`` bars, aligned to the current bar."""
    if window < 1:
        raise ValueError("window must be positive")
    _require_utc(low.index)
    return low.astype(float).rolling(window=window, min_periods=window).min().shift(1)


def causal_atr(frame: pd.DataFrame, window: int) -> pd.Series:
    """Average true range through the current bar, without centered windows."""
    required = {"high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("ATR requires high, low, and close columns")
    _require_utc(frame.index)
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [frame["high"] - frame["low"],
         (frame["high"] - previous_close).abs(),
         (frame["low"] - previous_close).abs()], axis=1).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def session_flags(index: pd.DatetimeIndex, start_hour_utc: int, end_hour_utc: int) -> pd.Series:
    """Mark a UTC hour interval, including start and excluding end."""
    _require_utc(index)
    if not 0 <= start_hour_utc <= 23 or not 0 <= end_hour_utc <= 23:
        raise ValueError("session hours must be in [0, 23]")
    hours = pd.Series(index.hour, index=index)
    if start_hour_utc <= end_hour_utc:
        return (hours >= start_hour_utc) & (hours < end_hour_utc)
    return (hours >= start_hour_utc) | (hours < end_hour_utc)
