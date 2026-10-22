"""Statistical primitives shared by reports and search; no promotion logic."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class TStatistic:
    n: int
    mean: float
    t_stat: float
    p_value: float
    insufficient_sample: bool


@dataclass(frozen=True)
class PermutationReport:
    gross_percentile: float
    net_percentile: float
    null_mean_net: float
    null_p95_net: float


def t_statistic(values: list[float] | np.ndarray) -> TStatistic:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    n = int(array.size)
    if n == 0:
        return TStatistic(0, float("nan"), float("nan"), float("nan"), True)
    mean = float(array.mean())
    if n < 2 or float(array.std(ddof=1)) == 0.0:
        t_value = float("inf") if mean > 0 else float("-inf") if mean < 0 else 0.0
        p_value = 0.0 if mean != 0 else 1.0
    else:
        t_value, p_value = stats.ttest_1samp(array, 0.0)
        t_value, p_value = float(t_value), float(p_value)
    return TStatistic(n, mean, t_value, p_value, n < 300)


def shifted_signal_percentile(
    signals: np.ndarray,
    outcomes: np.ndarray,
    *,
    iterations: int = 1000,
    seed: int = 0,
) -> float:
    """Percentile of the real sum against random circular signal offsets."""
    if signals.shape != outcomes.shape or signals.ndim != 1:
        raise ValueError("signals and outcomes must be one-dimensional and equal length")
    if iterations < 1:
        raise ValueError("iterations must be positive")
    real = float(np.dot(signals, outcomes))
    rng = np.random.default_rng(seed)
    null = np.array([float(np.dot(np.roll(signals, int(rng.integers(len(signals))), 0), outcomes))
                     for _ in range(iterations)])
    return float((np.count_nonzero(null <= real) + 1) / (iterations + 1) * 100.0)


def permutation_report(
    signals: np.ndarray,
    gross: np.ndarray,
    net: np.ndarray,
    *,
    iterations: int = 1000,
    seed: int = 0,
) -> PermutationReport:
    """Compare gross and net outcomes to the same circular-shift null.

    The null baseline keeps the candidate's observed trade count and return
    magnitudes while destroying temporal alignment. It is a diagnostic, not a
    claim that random signals are executable.
    """
    signals = np.asarray(signals, dtype=float)
    gross = np.asarray(gross, dtype=float)
    net = np.asarray(net, dtype=float)
    if signals.ndim != 1 or signals.shape != gross.shape or signals.shape != net.shape:
        raise ValueError("signals, gross, and net must be one-dimensional and equal length")
    if not len(net) or iterations < 1:
        return PermutationReport(0.0, 0.0, float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    gross_null = np.empty(iterations)
    net_null = np.empty(iterations)
    for i in range(iterations):
        shifted = np.roll(signals, int(rng.integers(len(signals))))
        gross_null[i] = float(np.dot(shifted, gross))
        net_null[i] = float(np.dot(shifted, net))
    gross_real = float(np.dot(signals, gross))
    net_real = float(np.dot(signals, net))
    return PermutationReport(
        float((np.count_nonzero(gross_null <= gross_real) + 1) / (iterations + 1) * 100.0),
        float((np.count_nonzero(net_null <= net_real) + 1) / (iterations + 1) * 100.0),
        float(net_null.mean()), float(np.quantile(net_null, 0.95)),
    )
