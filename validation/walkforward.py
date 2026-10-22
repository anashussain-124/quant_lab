"""Frozen-parameter walk-forward and deflated-Sharpe helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class FoldResult:
    fold: int
    test_start: int
    test_end: int
    mean_r: float
    profit_factor: float
    trade_count: int
    params: dict[str, float]


def purged_walk_forward(values: np.ndarray, params: dict[str, float], *, folds: int = 6,
                        embargo: int = 1) -> list[FoldResult]:
    """Evaluate fixed params on sequential test folds with an embargo gap."""
    if folds < 2 or embargo < 0:
        raise ValueError("invalid folds, embargo, or sample size")
    chunks = np.array_split(np.asarray(values, dtype=float), folds)
    results: list[FoldResult] = []
    start = 0
    for fold, chunk in enumerate(chunks):
        test_start = start + (embargo if fold > 0 else 0)
        test = np.asarray(values[test_start:start + len(chunk)], dtype=float)
        mean_r = float(test.mean()) if len(test) else 0.0
        gains = float(test[test > 0].sum())
        losses = float(-test[test < 0].sum())
        results.append(FoldResult(fold, test_start, start + len(chunk), mean_r,
                                  gains / losses if losses else float("inf"), len(test), dict(params)))
        start += len(chunk)
    return results


def deflated_sharpe(values: np.ndarray, trials: int) -> float:
    """Approximate DSR as PSR against the expected maximum Sharpe of trials."""
    array = np.asarray(values, dtype=float)
    if len(array) < 2 or trials < 1:
        return float("nan")
    std = float(array.std(ddof=1))
    if std == 0:
        return 1.0 if array.mean() > 0 else 0.0
    sharpe = float(array.mean() / std * np.sqrt(len(array)))
    benchmark = float(norm.ppf(1.0 - 1.0 / max(trials, 2)))
    return float(norm.cdf(sharpe - benchmark))
