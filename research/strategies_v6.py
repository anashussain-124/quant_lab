"""The 24 frozen v6 H4 configurations and their causal signal plans.

Every rule is a literal transcription of docs/PRE_REGISTRATION_H4_V6.md.
Adding, removing, or changing a parameter is a protocol violation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.indicators_v5 import (
    rolling_max_prior,
    rolling_min_prior,
    rolling_percentile_rank,
    wilder_atr,
)
from research.strategies_v5 import ATR_PERIOD, Plan, StrategySpec

FAMILY = "h4_donchian"
MAX_HOLD_H4 = 120
VOL_LOOKBACK = 100
VOL_THRESHOLD = 0.50


def _spec(
    config_id: str,
    n: int,
    stop_atr: float,
    trail_atr: float,
    vol_filter: float | None,
) -> StrategySpec:
    return StrategySpec(
        config_id,
        FAMILY,
        {
            "n": n,
            "stop_atr": stop_atr,
            "trail_atr": trail_atr,
            "vol_filter": vol_filter,
        },
    )


SPECS: tuple[StrategySpec, ...] = tuple(
    _spec(f"h4d{index:02d}", n, stop, trail, None)
    for index, (n, stop, trail) in enumerate(
        (
            (20, 1.5, 2.0),
            (20, 1.5, 3.5),
            (20, 2.5, 2.0),
            (20, 2.5, 3.5),
            (50, 1.5, 2.0),
            (50, 1.5, 3.5),
            (50, 2.5, 2.0),
            (50, 2.5, 3.5),
            (100, 1.5, 2.0),
            (100, 1.5, 3.5),
            (100, 2.5, 2.0),
            (100, 2.5, 3.5),
        ),
        1,
    )
) + tuple(
    _spec(f"h4v{index:02d}", n, stop, trail, VOL_THRESHOLD)
    for index, (n, stop, trail) in enumerate(
        (
            (20, 1.5, 2.0),
            (20, 1.5, 3.5),
            (20, 2.5, 2.0),
            (20, 2.5, 3.5),
            (50, 1.5, 2.0),
            (50, 1.5, 3.5),
            (50, 2.5, 2.0),
            (50, 2.5, 3.5),
            (100, 1.5, 2.0),
            (100, 1.5, 3.5),
            (100, 2.5, 2.0),
            (100, 2.5, 3.5),
        ),
        1,
    )
)


def all_specs() -> tuple[StrategySpec, ...]:
    return SPECS


def build_plan_v6(frame: pd.DataFrame, spec: StrategySpec) -> Plan:
    if spec.family != FAMILY:
        raise ValueError(f"not a v6 specification: {spec.family}")
    if frame.index.tz is None or str(frame.index.tz) != "UTC":
        raise ValueError("build_plan_v6 requires a UTC-aware DatetimeIndex")
    n = len(frame)
    if n == 0:
        raise ValueError("empty frame")

    params: dict[str, Any] = spec.params
    lookback = int(params["n"])
    stop_atr = float(params["stop_atr"])
    trail_atr = float(params["trail_atr"])
    vol_filter = params.get("vol_filter")
    if lookback < 2:
        raise ValueError("Donchian lookback must be >= 2")
    if stop_atr <= 0 or trail_atr <= 0:
        raise ValueError("ATR multiples must be positive")
    if vol_filter is not None and not 0.0 < float(vol_filter) < 1.0:
        raise ValueError("vol filter must be in (0, 1) or None")

    close = frame["close"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    atr = wilder_atr(high, low, close, ATR_PERIOD)
    upper = rolling_max_prior(high, lookback)
    lower = rolling_min_prior(low, lookback)
    long_signal = close > upper
    short_signal = close < lower

    warmup = max(lookback, ATR_PERIOD) + 2
    if vol_filter is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            vol_ratio = np.divide(
                atr, close, out=np.full(n, np.nan), where=close > 0
            )
        rank = rolling_percentile_rank(vol_ratio, VOL_LOOKBACK)
        active = rank >= float(vol_filter)
        long_signal = long_signal & active
        short_signal = short_signal & active
        warmup = max(warmup, VOL_LOOKBACK + 2)

    zeros = np.zeros(n, dtype=bool)
    return Plan(
        spec=spec,
        long_signal=long_signal,
        short_signal=short_signal,
        exit_long=zeros.copy(),
        exit_short=zeros.copy(),
        atr=atr,
        stop_atr=stop_atr,
        target_r=None,
        trail_atr=trail_atr,
        max_hold=MAX_HOLD_H4,
        exit_hour=None,
        warmup=warmup,
    )
