"""The 50 frozen v5 configurations and their causal signal plans.

Every rule here is a literal transcription of docs/PRE_REGISTRATION_SWING_V5.md
(and Amendment 1). Adding, removing, or changing a parameter is a protocol
violation, not a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from research.indicators_v5 import (
    cross_down,
    cross_up,
    rolling_max_prior,
    rolling_min_prior,
    rolling_percentile_rank,
    rolling_std,
    sma,
    wilder_atr,
    wilder_ema,
)

ATR_PERIOD = 14


@dataclass
class StrategySpec:
    config_id: str
    family: str
    params: dict[str, Any] = field(default_factory=dict)

    def param_dict(self) -> dict[str, Any]:
        return {"family": self.family, **self.params}


@dataclass
class Plan:
    spec: StrategySpec
    long_signal: np.ndarray
    short_signal: np.ndarray
    exit_long: np.ndarray
    exit_short: np.ndarray
    atr: np.ndarray
    stop_atr: float
    target_r: float | None
    trail_atr: float | None
    max_hold: int
    exit_hour: int | None
    warmup: int

    def __post_init__(self) -> None:
        n = len(self.atr)
        for name in ("long_signal", "short_signal", "exit_long", "exit_short"):
            array = getattr(self, name)
            if len(array) != n:
                raise ValueError(f"{name} length {len(array)} != atr length {n}")
            if array.dtype != np.bool_:
                raise ValueError(f"{name} must be boolean")


def _false(n: int) -> np.ndarray:
    return np.zeros(n, dtype=bool)


def _dc(config_id: str, n: int, stop: float, trail: float, buffer: float) -> StrategySpec:
    return StrategySpec(config_id, "donchian_trail",
                        {"n": n, "stop_atr": stop, "trail_atr": trail, "buffer": buffer})


def _em(config_id: str, fast: int, slow: int, stop: float, trail: float, entry: str) -> StrategySpec:
    return StrategySpec(config_id, "ema_pullback",
                        {"fast": fast, "slow": slow, "stop_atr": stop, "trail_atr": trail, "entry": entry})


def _ar(config_id: str, buffer: float, stop: float, exit_hour: int,
        target_r: float | None, trail_atr: float | None) -> StrategySpec:
    return StrategySpec(config_id, "asian_range_breakout",
                        {"buffer": buffer, "stop_atr": stop, "exit_hour": exit_hour,
                         "target_r": target_r, "trail_atr": trail_atr})


def _mr(config_id: str, n: int, z_entry: float, stop: float, max_hold: int, vol_cap: float) -> StrategySpec:
    return StrategySpec(config_id, "lowvol_meanrev",
                        {"n": n, "z_entry": z_entry, "stop_atr": stop, "max_hold": max_hold, "vol_cap": vol_cap})


def _sq(config_id: str, n: int, lookback: int, pct: float, stop: float,
        target_r: float | None, trail_atr: float | None) -> StrategySpec:
    return StrategySpec(config_id, "squeeze_breakout",
                        {"n": n, "sq_lookback": lookback, "sq_pct": pct, "stop_atr": stop,
                         "target_r": target_r, "trail_atr": trail_atr})


SPECS: tuple[StrategySpec, ...] = (
    # F1 donchian_trail
    _dc("dc01", 50, 2.0, 3.0, 0.0),
    _dc("dc02", 50, 3.0, 3.0, 0.1),
    _dc("dc03", 100, 2.0, 3.0, 0.0),
    _dc("dc04", 100, 3.0, 4.0, 0.1),
    _dc("dc05", 200, 2.0, 4.0, 0.0),
    _dc("dc06", 200, 3.0, 4.0, 0.1),
    _dc("dc07", 100, 2.0, 2.0, 0.0),
    _dc("dc08", 100, 3.0, 3.0, 0.0),
    _dc("dc09", 50, 2.5, 4.0, 0.1),
    _dc("dc10", 200, 2.5, 3.0, 0.0),
    # F2 ema_pullback
    _em("em01", 20, 100, 2.0, 3.0, "pullback"),
    _em("em02", 50, 200, 2.5, 4.0, "pullback"),
    _em("em03", 50, 100, 2.0, 3.0, "pullback"),
    _em("em04", 20, 50, 1.5, 2.5, "pullback"),
    _em("em05", 100, 200, 3.0, 4.0, "pullback"),
    _em("em06", 20, 100, 3.0, 3.0, "pullback"),
    _em("em07", 50, 200, 2.0, 3.0, "crossover"),
    _em("em08", 50, 100, 2.5, 3.5, "crossover"),
    _em("em09", 20, 100, 2.0, 4.0, "crossover"),
    _em("em10", 100, 200, 2.0, 3.0, "crossover"),
    # F3 asian_range_breakout
    _ar("ar01", 0.0, 1.5, 16, 2.0, None),
    _ar("ar02", 0.0, 2.0, 20, 2.0, None),
    _ar("ar03", 0.1, 1.5, 16, 2.0, None),
    _ar("ar04", 0.1, 2.0, 20, 2.0, None),
    _ar("ar05", 0.0, 2.0, 16, 1.5, None),
    _ar("ar06", 0.1, 2.5, 20, 1.5, None),
    _ar("ar07", 0.0, 2.0, 20, None, 2.5),
    _ar("ar08", 0.1, 2.0, 20, None, 2.5),
    _ar("ar09", 0.0, 2.5, 20, 3.0, None),
    _ar("ar10", 0.1, 1.5, 16, None, 2.0),
    # F4 lowvol_meanrev
    _mr("mr01", 20, 2.0, 2.0, 24, 0.5),
    _mr("mr02", 20, 2.5, 2.0, 24, 0.5),
    _mr("mr03", 50, 2.0, 2.0, 48, 0.6),
    _mr("mr04", 50, 2.5, 2.5, 48, 0.6),
    _mr("mr05", 100, 2.0, 2.5, 48, 0.6),
    _mr("mr06", 100, 2.5, 3.0, 72, 0.6),
    _mr("mr07", 20, 2.0, 1.5, 24, 0.4),
    _mr("mr08", 50, 2.5, 2.0, 48, 0.4),
    _mr("mr09", 50, 2.0, 3.0, 72, 0.7),
    _mr("mr10", 100, 2.0, 2.0, 48, 0.5),
    # F5 squeeze_breakout
    _sq("sq01", 20, 100, 0.20, 1.5, 2.0, None),
    _sq("sq02", 20, 100, 0.20, 2.0, None, 2.5),
    _sq("sq03", 20, 200, 0.20, 1.5, 2.0, None),
    _sq("sq04", 50, 100, 0.20, 2.0, 2.0, None),
    _sq("sq05", 50, 200, 0.20, 2.0, None, 3.0),
    _sq("sq06", 20, 100, 0.30, 1.5, 2.0, None),
    _sq("sq07", 50, 100, 0.30, 2.0, 2.0, None),
    _sq("sq08", 20, 200, 0.30, 2.0, None, 2.5),
    _sq("sq09", 50, 200, 0.30, 2.5, 3.0, None),
    _sq("sq10", 20, 100, 0.20, 2.5, 3.0, None),
)


def all_specs() -> list[StrategySpec]:
    return [StrategySpec(spec.config_id, spec.family, dict(spec.params)) for spec in SPECS]


def spec_by_id(config_id: str) -> StrategySpec:
    for spec in SPECS:
        if spec.config_id == config_id:
            return StrategySpec(spec.config_id, spec.family, dict(spec.params))
    raise KeyError(config_id)


def _first_per_day(long_signal: np.ndarray, short_signal: np.ndarray,
                   days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    combined = long_signal | short_signal
    if not combined.any():
        return long_signal, short_signal
    indices = np.flatnonzero(combined)
    signal_days = days[indices]
    _, first_positions = np.unique(signal_days, return_index=True)
    keep = np.zeros(len(combined), dtype=bool)
    keep[indices[first_positions]] = True
    return long_signal & keep, short_signal & keep


def build_plan(frame: pd.DataFrame, spec: StrategySpec) -> Plan:
    if frame.index.tz is None or str(frame.index.tz) != "UTC":
        raise ValueError("build_plan requires a UTC-aware DatetimeIndex")
    n = len(frame)
    if n == 0:
        raise ValueError("empty frame")
    close = frame["close"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    atr = wilder_atr(high, low, close, ATR_PERIOD)
    zeros = _false(n)
    params = spec.params

    if spec.family == "donchian_trail":
        lookback = int(params["n"])
        buffer = float(params["buffer"])
        upper = rolling_max_prior(high, lookback)
        lower = rolling_min_prior(low, lookback)
        long_signal = close > upper + buffer * atr
        short_signal = close < lower - buffer * atr
        return Plan(spec, long_signal, short_signal, zeros.copy(), zeros.copy(), atr,
                    stop_atr=float(params["stop_atr"]), target_r=None,
                    trail_atr=float(params["trail_atr"]), max_hold=240, exit_hour=None,
                    warmup=max(lookback, ATR_PERIOD) + 2)

    if spec.family == "ema_pullback":
        fast_n, slow_n = int(params["fast"]), int(params["slow"])
        fast = wilder_ema(close, fast_n)
        slow = wilder_ema(close, slow_n)
        exit_long = cross_down(fast, slow)
        exit_short = cross_up(fast, slow)
        if params["entry"] == "pullback":
            long_signal = cross_up(close, fast) & (fast > slow)
            short_signal = cross_down(close, fast) & (fast < slow)
        elif params["entry"] == "crossover":
            long_signal = cross_up(fast, slow)
            short_signal = cross_down(fast, slow)
        else:
            raise ValueError(f"unknown ema entry mode: {params['entry']}")
        return Plan(spec, long_signal, short_signal, exit_long, exit_short, atr,
                    stop_atr=float(params["stop_atr"]), target_r=None,
                    trail_atr=float(params["trail_atr"]), max_hold=240, exit_hour=None,
                    warmup=max(slow_n, ATR_PERIOD) + 2)

    if spec.family == "asian_range_breakout":
        buffer = float(params["buffer"])
        day = frame.index.floor("D")
        hours = frame.index.hour.to_numpy()
        asian = (hours >= 1) & (hours <= 6)
        session = pd.DataFrame({"high": high, "low": low}, index=frame.index).loc[asian]
        if session.empty:
            raise ValueError("no Asian-session bars")
        daily = session.groupby(session.index.floor("D")).agg(range_high=("high", "max"),
                                                              range_low=("low", "min"))
        range_high = np.asarray(day.map(daily["range_high"]), dtype=float)
        range_low = np.asarray(day.map(daily["range_low"]), dtype=float)
        in_window = (hours >= 7) & (hours <= 12)
        long_signal = in_window & (close > range_high + buffer * atr)
        short_signal = in_window & (close < range_low - buffer * atr)
        long_signal, short_signal = _first_per_day(long_signal, short_signal, day.to_numpy())
        return Plan(spec, long_signal, short_signal, zeros.copy(), zeros.copy(), atr,
                    stop_atr=float(params["stop_atr"]),
                    target_r=None if params["target_r"] is None else float(params["target_r"]),
                    trail_atr=None if params["trail_atr"] is None else float(params["trail_atr"]),
                    max_hold=24, exit_hour=int(params["exit_hour"]), warmup=ATR_PERIOD + 2)

    if spec.family == "lowvol_meanrev":
        lookback = int(params["n"])
        z_entry = float(params["z_entry"])
        mean = sma(close, lookback)
        std = rolling_std(close, lookback)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.divide(close - mean, std, out=np.full(n, np.nan), where=std > 0)
        vol_ratio = np.divide(atr, close, out=np.full(n, np.nan), where=close > 0)
        vol_pct = rolling_percentile_rank(vol_ratio, 100)
        calm = vol_pct < float(params["vol_cap"])
        long_signal = (z < -z_entry) & calm
        short_signal = (z > z_entry) & calm
        exit_long = cross_up(z, np.zeros(n))
        exit_short = cross_down(z, np.zeros(n))
        return Plan(spec, long_signal, short_signal, exit_long, exit_short, atr,
                    stop_atr=float(params["stop_atr"]), target_r=None, trail_atr=None,
                    max_hold=int(params["max_hold"]), exit_hour=None,
                    warmup=max(lookback, 100, ATR_PERIOD) + 2)

    if spec.family == "squeeze_breakout":
        lookback = int(params["n"])
        sq_lookback = int(params["sq_lookback"])
        mean = sma(close, lookback)
        std = rolling_std(close, lookback)
        upper = mean + 2.0 * std
        lower = mean - 2.0 * std
        with np.errstate(divide="ignore", invalid="ignore"):
            bandwidth = np.divide(upper - lower, mean, out=np.full(n, np.nan), where=mean > 0)
        band_pct = rolling_percentile_rank(bandwidth, sq_lookback)
        squeeze = band_pct <= float(params["sq_pct"])
        squeeze_prev = np.roll(squeeze, 1)
        squeeze_prev[0] = False
        long_signal = cross_up(close, upper) & squeeze_prev
        short_signal = cross_down(close, lower) & squeeze_prev
        return Plan(spec, long_signal, short_signal, zeros.copy(), zeros.copy(), atr,
                    stop_atr=float(params["stop_atr"]),
                    target_r=None if params["target_r"] is None else float(params["target_r"]),
                    trail_atr=None if params["trail_atr"] is None else float(params["trail_atr"]),
                    max_hold=72, exit_hour=None,
                    warmup=max(lookback, sq_lookback, ATR_PERIOD) + 2)

    raise ValueError(f"unknown family: {spec.family}")
