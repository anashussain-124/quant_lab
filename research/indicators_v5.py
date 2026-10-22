"""Causal indicators for the v5 swing research harness."""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    if n == 0:
        return np.empty(0)
    tr = np.empty(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.full(n, np.nan)
    if n < period:
        return atr
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def ema(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values, dtype=float).ewm(span=period, adjust=False).mean().to_numpy()


def wilder_ema(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder smoothing (alpha = 1/period), as specified for the v5 EMA family."""
    return pd.Series(values, dtype=float).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def sma(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(period).mean().to_numpy()


def rolling_std(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(period).std(ddof=0).to_numpy()


def rolling_max_prior(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(period).max().shift(1).to_numpy()


def rolling_min_prior(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(period).min().shift(1).to_numpy()


def rolling_mean(values: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(period).mean().to_numpy()


def rolling_percentile_rank(values: np.ndarray, window: int) -> np.ndarray:
    """Causal percentile rank of the current value against the prior `window` values."""
    array = np.asarray(values, dtype=float)
    out = np.full(len(array), np.nan)
    if len(array) <= window:
        return out
    windows = sliding_window_view(array, window + 1)
    valid = ~np.isnan(windows).any(axis=1)
    ranks = np.full(len(windows), np.nan)
    ranks[valid] = (windows[valid, :-1] <= windows[valid, -1:]).mean(axis=1)
    out[window:] = ranks
    return out


def cross_up(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    left_prev = np.roll(left, 1)
    right_prev = np.roll(right, 1)
    left_prev[0] = np.nan
    right_prev[0] = np.nan
    with np.errstate(invalid="ignore"):
        return (left > right) & (left_prev <= right_prev)


def cross_down(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    left_prev = np.roll(left, 1)
    right_prev = np.roll(right, 1)
    left_prev[0] = np.nan
    right_prev[0] = np.nan
    with np.errstate(invalid="ignore"):
        return (left < right) & (left_prev >= right_prev)
