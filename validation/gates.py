"""Ordered, auditable promotion gates for candidate trade results."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from validation.core import shifted_signal_percentile, t_statistic


@dataclass(frozen=True)
class GateResult:
    verdict: str
    reasons: tuple[str, ...]
    funnel: tuple[tuple[str, bool], ...]
    permutation_percentile: float
    t_stat: float


def evaluate_candidate(
    trades: list[float] | np.ndarray,
    folds: list[dict[str, float]],
    *,
    registry_trials: int,
    permutation_percentile: float | None = None,
    neighborhood: list[float] | None = None,
    stressed_trades: list[float] | np.ndarray | None = None,
    regimes: dict[str, list[float]] | None = None,
) -> GateResult:
    values = np.asarray(trades, dtype=float)
    reasons: list[str] = []
    funnel: list[tuple[str, bool]] = []
    mean = float(values.mean()) if values.size else float("nan")
    positive = bool(values.size and mean > 0)
    funnel.append(("positive net expectancy", positive))
    if not positive:
        reasons.append("net expectancy is not positive")
    n_ok = values.size >= 300
    funnel.append(("N >= 300", n_ok))
    if not n_ok:
        reasons.append(f"N={values.size} is below 300")
    stat = t_statistic(values)
    adjusted_threshold = 3.0 + math.sqrt(2.0 * math.log(max(2, registry_trials)))
    t_ok = bool((np.isfinite(stat.t_stat) or np.isinf(stat.t_stat)) and stat.t_stat > adjusted_threshold)
    funnel.append(("registry-adjusted t-stat", t_ok))
    if not t_ok:
        reasons.append(f"t-stat={stat.t_stat:.3f} does not exceed adjusted threshold {adjusted_threshold:.3f}")
    perm = 0.0 if permutation_percentile is None else float(permutation_percentile)
    perm_ok = perm >= 95.0
    funnel.append(("permutation >= 95", perm_ok))
    if not perm_ok:
        reasons.append(f"permutation percentile={perm:.3f} is below 95")
    positive_folds = sum(float(f.get("mean_r", 0.0)) > 0 for f in folds)
    total_profit = sum(float(f.get("profit", f.get("mean_r", 0.0) * f.get("trade_count", 0))) for f in folds)
    max_share = max((float(f.get("profit", f.get("mean_r", 0.0) * f.get("trade_count", 0))) / total_profit
                     for f in folds), default=1.0) if total_profit > 0 else 1.0
    wf_ok = bool(folds and positive_folds >= math.ceil(len(folds) * 2 / 3) and max_share <= 0.5)
    funnel.append(("walk-forward", wf_ok))
    if not wf_ok:
        reasons.append(f"walk-forward has {positive_folds}/{len(folds)} positive folds and max profit share {max_share:.3f}")
    neighbors = neighborhood or []
    neighborhood_ok = bool(neighbors) and sum(v > 0 for v in neighbors) / len(neighbors) >= 0.7
    funnel.append(("parameter neighborhood", neighborhood_ok))
    if not neighborhood_ok:
        reasons.append("fewer than 70% of parameter neighbors have positive expectancy")
    stress = np.asarray(stressed_trades if stressed_trades is not None else [], dtype=float)
    cost_ok = bool(stress.size and float(stress.mean()) > 0)
    funnel.append(("1.5x cost stress", cost_ok))
    if not cost_ok:
        reasons.append("net expectancy is not positive at 1.5x spread and slippage")
    if regimes is None or not regimes or any(np.asarray(v).size == 0 for v in regimes.values()):
        regime_ok = False
        reasons.append("regime breakdown is missing")
    else:
        regime_ok = all(float(np.mean(v)) > 0 for v in regimes.values())
        if not regime_ok:
            reasons.append("at least one volatility/year regime has non-positive expectancy")
    funnel.append(("regimes", regime_ok))
    all_pass = all(ok for _, ok in funnel)
    verdict = "PASS-CANDIDATE" if all_pass else ("FAIL" if not positive or not n_ok else "INCONCLUSIVE")
    return GateResult(verdict, tuple(reasons), tuple(funnel), perm, float(stat.t_stat))
