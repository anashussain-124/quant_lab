"""Phase 3B: Quant Math Library — E1-E11 as pure typed functions.

Per AGENTS.md A4 & Part E:
- Pure function, typed, unit-tested
- Causal by default (no future data)
- Parameters fitted on training data, tested on test data
- Never use full-history stats
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Any


@dataclass
class MathResult:
    """Result container for math operations."""
    value: float | None = None
    values: list[float] | None = None
    is_causal: bool = True
    params: dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = {}


def realized_volatility(
    returns: list[float] | np.ndarray,
    periods_per_year: int = 252,
) -> MathResult:
    """E1: Realized volatility.

    Pure function, causal (uses only provided returns).
    """
    returns = np.array(returns)
    if len(returns) == 0:
        return MathResult(value=0.0, is_causal=True)

    daily_vol = np.std(returns, ddof=1)
    annualized_vol = daily_vol * np.sqrt(periods_per_year)

    return MathResult(
        value=float(annualized_vol),
        is_causal=True,
        params={"periods_per_year": periods_per_year},
    )


def ewma_volatility(
    returns: list[float] | np.ndarray,
    lambda_: float = 0.94,
) -> MathResult:
    """EWMA volatility.

    Pure function, causal (decay-based weighting).
    """
    returns = np.array(returns)
    if len(returns) == 0:
        return MathResult(value=0.0, is_causal=True)

    weights = np.array([(1 - lambda_) * lambda_**i for i in range(len(returns))])
    weights = weights[::-1]  # Most recent has highest weight
    variance = np.sum(weights * returns**2) / np.sum(weights)
    ewma_vol = np.sqrt(variance)

    return MathResult(
        value=float(ewma_vol),
        is_causal=True,
        params={"lambda": lambda_},
    )


def ema(
    series: list[float] | np.ndarray,
    n: int = 20,
) -> list[float]:
    """Exponential Moving Average.

    Pure function, causal (only uses past data).
    """
    series = np.array(series)
    if len(series) < n:
        return [float(np.mean(series))] * len(series)

    k = 2 / (n + 1)
    ema_values = [series[0]]
    for i in range(1, len(series)):
        ema_values.append(series[i] * k + ema_values[-1] * (1 - k))

    return [float(v) for v in ema_values]


def trend_score(
    fast_ma: list[float],
    slow_ma: list[float],
    vol: float,
) -> MathResult:
    """Trend score based on MA separation and volatility.

    Causal by construction (uses only past MAs).
    """
    if len(fast_ma) == 0 or len(slow_ma) == 0:
        return MathResult(value=0.0, is_causal=True)

    # Use latest values
    fast_val = fast_ma[-1]
    slow_val = slow_ma[-1]

    separation = fast_val - slow_val
    normalized = separation / max(abs(vol), 1e-8)

    return MathResult(
        value=float(normalized),
        is_causal=True,
        params={"separation": float(separation)},
    )


def z_score(
    values: list[float] | np.ndarray,
    window: int = 20,
) -> list[float]:
    """Rolling z-score.

    Causal: only uses data up to each point.
    """
    values = np.array(values)
    result = []

    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_data = values[start : i + 1]
        if len(window_data) < 2:
            result.append(0.0)
        else:
            mean = np.mean(window_data)
            std = np.std(window_data, ddof=1)
            z = (values[i] - mean) / max(std, 1e-8)
            result.append(float(z))

    return result


def sharpe_ratio(
    returns: list[float] | np.ndarray,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Annualized Sharpe ratio.

    Pure function, causal (uses only provided returns).
    """
    returns = np.array(returns)
    if len(returns) == 0:
        return 0.0

    excess = returns - risk_free_rate / periods_per_year
    mean_excess = np.mean(excess)
    std_excess = np.std(excess, ddof=1)

    if std_excess == 0:
        return 0.0

    sharpe = (mean_excess / std_excess) * np.sqrt(periods_per_year)
    return float(sharpe)


def sortino_ratio(
    returns: list[float] | np.ndarray,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Sortino ratio (downside deviation instead of total std).

    Pure function, causal.
    """
    returns = np.array(returns)
    if len(returns) == 0:
        return 0.0

    excess = returns - risk_free_rate / periods_per_year
    mean_excess = np.mean(excess)

    # Downside deviation
    downside = excess[excess < 0]
    downside_std = np.std(downside, ddof=1) if len(downside) > 0 else 1e-8

    if downside_std == 0:
        return 0.0

    sortino = (mean_excess / downside_std) * np.sqrt(periods_per_year)
    return float(sortino)


def position_size(
    equity: float,
    risk_fraction: float,
    stop_dist: float,
    tick_size: float,
    tick_value: float,
) -> float:
    """Position sizing.

    Causal: uses only current equity, risk, and stop distance.
    """
    if stop_dist <= 0 or tick_size <= 0 or tick_value <= 0:
        return 0.0

    risk_amount = equity * risk_fraction
    risk_ticks = stop_dist / tick_size
    lots = risk_amount / (risk_ticks * tick_value)

    return max(0.0, lots)


def expected_value(
    p: float,
    win_avg: float,
    loss_avg: float,
    cost: float,
) -> float:
    """Expected value per trade.

    Pure function, causal.
    """
    ev = p * win_avg - (1 - p) * loss_avg - cost
    return float(ev)


def kelly_criterion(
    p: float,
    b: float,
    f: float = 1.0,
) -> float:
    """Full Kelly fraction.

    f: fraction of Kelly (1.0 = full, 0.25 = quarter-Kelly)
    """
    if b <= 0:
        return 0.0

    kelly = (p * b - (1 - p)) / b
    return float(max(0.0, min(1.0, kelly * f)))


def fractional_kelly(
    kelly: float,
    fraction: float = 0.25,
) -> float:
    """Apply fractional Kelly."""
    return float(max(0.0, min(1.0, kelly * fraction)))


def hurst_exponent(
    returns: list[float] | np.ndarray,
    max_lag: int = 20,
) -> float:
    """Hurst exponent estimation.

    Causal: uses only provided returns.
    """
    returns = np.array(returns)
    if len(returns) < max_lag:
        return 0.5

    lags = range(2, min(max_lag + 1, len(returns)))
    tau = []

    for lag in lags:
        # Compute range of differences
        diffs = np.diff(returns, n=1)
        if len(diffs) < lag:
            continue
        ranges = []
        for i in range(0, len(diffs) - lag, lag):
            chunk = diffs[i : i + lag]
            if len(chunk) > 0:
                ranges.append(np.max(chunk) - np.min(chunk))
        if ranges:
            avg_range = np.mean(ranges)
            tau.append(np.log(avg_range))

    if len(tau) < 2:
        return 0.5

    # Linear regression on log-log
    lags_log = np.log(range(2, 2 + len(tau)))
    tau_log = np.array(tau)
    slope, _ = np.polyfit(lags_log, tau_log, 1)

    hurst = slope / 2.0
    return float(max(0.0, min(1.0, hurst)))


def hurst_to_regime(hurst: float) -> str:
    """Convert Hurst to market regime."""
    hurst = float(hurst)
    if hurst > 0.6:
        return "TRENDING"
    elif hurst < 0.4:
        return "MEAN_REVERTING"
    else:
        return "RANDOM_WALK"


def annualize_sharpe(
    periods_per_year: int,
    sharpe: float,
) -> float:
    """Annualize Sharpe ratio."""
    return float(sharpe * np.sqrt(periods_per_year))


def information_ratio(
    returns: list[float] | np.ndarray,
    benchmark_returns: list[float] | np.ndarray,
    periods_per_year: int = 252,
) -> float:
    """Information ratio (excess return / tracking error)."""
    if len(returns) != len(benchmark_returns):
        return 0.0

    excess = np.array(returns) - np.array(benchmark_returns)
    if len(excess) == 0:
        return 0.0

    tracking_error = np.std(excess, ddof=1)
    if tracking_error == 0:
        return 0.0

    mean_excess = np.mean(excess)
    ir = (mean_excess * periods_per_year) / (tracking_error * np.sqrt(periods_per_year))
    return float(ir)


def calmar_ratio(
    returns: list[float] | np.ndarray,
    periods_per_year: int = 252,
) -> float:
    """Calmar ratio (annualized return / max drawdown)."""
    returns = np.array(returns)
    if len(returns) == 0:
        return 0.0

    annual_return = np.mean(returns) * periods_per_year

    cumulative = np.cumsum(returns)
    peak = np.maximum.accumulate(cumulative)
    drawdown = cumulative - peak
    max_dd = abs(min(drawdown)) if len(drawdown) > 0 else 1.0

    if max_dd == 0:
        return float('inf') if annual_return > 0 else 0.0

    return float(annual_return / max_dd)


def omega_ratio(
    returns: list[float] | np.ndarray,
    threshold: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """Omega ratio (probability-weighted ratio of gains/losses)."""
    returns = np.array(returns)
    if len(returns) == 0:
        return 0.0

    gains = returns[returns > threshold]
    losses = returns[returns <= threshold]

    if len(losses) == 0:
        return float('inf') if len(gains) > 0 else 0.0

    return float(np.sum(gains) / abs(np.sum(losses)))
