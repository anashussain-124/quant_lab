"""Multiple-testing, robustness, and null-model statistics for v5.

All functions are deterministic given a seed. None of them see the sealed
holdout.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy import stats

from research.backtest_v5 import MarketData, Trade, simulate_trade
from research.costs_v5 import CostModel
from research.strategies_v5 import StrategySpec


def bootstrap_mean_ci(values, *, resamples: int = 10_000, seed: int = 0,
                      alpha: float = 0.05) -> tuple[float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size < 2:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    chunk = 1000
    for start in range(0, resamples, chunk):
        stop = min(start + chunk, resamples)
        draws = rng.integers(0, array.size, size=(stop - start, array.size))
        means[start:stop] = array[draws].mean(axis=1)
    low, high = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(low), float(high)


def deflated_sharpe_ratio(net_r, trial_sharpes) -> float:
    """Bailey-Lopez de Prado DSR using the cross-sectional trial variance.

    `net_r` are the candidate's per-trade R outcomes and `trial_sharpes` are
    the per-trade Sharpes of every pre-registered trial.
    """
    array = np.asarray(net_r, dtype=float)
    array = array[np.isfinite(array)]
    sharpes = np.asarray([s for s in trial_sharpes if np.isfinite(s)], dtype=float)
    n = array.size
    if n < 2 or sharpes.size < 2:
        return float("nan")
    std = float(array.std(ddof=1))
    if std == 0.0:
        return 1.0 if array.mean() > 0 else 0.0
    sr = float(array.mean() / std)
    variance = float(sharpes.var(ddof=1))
    if variance <= 0.0:
        benchmark = 0.0
    else:
        gamma = 0.5772156649015329
        trials = sharpes.size
        benchmark = math.sqrt(variance) * (
            (1.0 - gamma) * stats.norm.ppf(1.0 - 1.0 / trials)
            + gamma * stats.norm.ppf(1.0 - 1.0 / (trials * math.e))
        )
    skew = float(stats.skew(array, bias=False))
    kurt = float(stats.kurtosis(array, fisher=False, bias=False))
    denominator_sq = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denominator_sq <= 0.0:
        return float("nan")
    z = (sr - benchmark) * math.sqrt(n - 1) / math.sqrt(denominator_sq)
    return float(stats.norm.cdf(z))


def cscv_pbo(fold_matrix: np.ndarray) -> dict:
    """CSCV probability of backtest overfitting.

    Rows are folds, columns are configurations, values are per-fold mean net R.
    Missing values are treated as zero P&L (no evidence, neutral rank).
    """
    matrix = np.asarray(fold_matrix, dtype=float)
    matrix = np.nan_to_num(matrix, nan=0.0)
    n_folds, n_configs = matrix.shape
    if n_folds < 4 or n_folds % 2 or n_configs < 2:
        raise ValueError("CSCV requires an even fold count >= 4 and >= 2 configs")
    combinations = list(itertools.combinations(range(n_folds), n_folds // 2))
    omegas = []
    for in_sample in combinations:
        out_sample = [i for i in range(n_folds) if i not in in_sample]
        is_perf = matrix[list(in_sample)].mean(axis=0)
        oos_perf = matrix[out_sample].mean(axis=0)
        best = int(np.argmax(is_perf))
        better = int(np.count_nonzero(oos_perf < oos_perf[best]))
        equal = int(np.count_nonzero(oos_perf == oos_perf[best])) - 1
        omega = (better + 0.5 * equal + 1.0) / (n_configs + 1.0)
        omegas.append(omega)
    omegas_array = np.asarray(omegas)
    pbo = float(np.mean(omegas_array <= 0.5))
    return {"pbo": pbo, "splits": len(combinations), "mean_oos_rank": float(omegas_array.mean()),
            "median_oos_rank": float(np.median(omegas_array))}


def random_entry_null(
    md: MarketData,
    trades: list[Trade],
    plan,
    costs: CostModel,
    *,
    window_start: object,
    window_end: object,
    simulations: int = 500,
    seed: int = 0,
) -> dict:
    """Random-entry null matched on count, direction, stop distance, holding time.

    Signal-based exits and session exits are disabled so the sampled holding
    period is the binding horizon; stops, targets, and trailing stops still apply.
    """
    eligible = [t for t in trades if window_start <= t.entry_time <= window_end]
    if not eligible:
        return {"simulations": 0, "candidate_mean_r": float("nan"),
                "null_p95": float("nan"), "percentile": float("nan"), "passed": False}
    candidate_mean = float(np.mean([t.net_r for t in eligible]))
    ratios = np.array([t.stop_distance / t.atr_entry for t in eligible
                       if np.isfinite(t.atr_entry) and t.atr_entry > 0], dtype=float)
    holds = np.array([max(1, t.bars_held) for t in eligible], dtype=int)
    long_share = float(np.mean([t.direction == 1 for t in eligible]))
    if ratios.size == 0:
        return {"simulations": 0, "candidate_mean_r": candidate_mean,
                "null_p95": float("nan"), "percentile": float("nan"), "passed": False}

    n = len(md.close)
    first = max(2, int(md.index.searchsorted(window_start)))
    last = min(n - 2, int(md.index.searchsorted(window_end, side="right")) - 1)
    if last <= first:
        return {"simulations": 0, "candidate_mean_r": candidate_mean,
                "null_p95": float("nan"), "percentile": float("nan"), "passed": False}
    pool = np.arange(first, last + 1, dtype=int)
    rng = np.random.default_rng(seed)
    null_means = np.empty(simulations, dtype=float)
    for simulation in range(simulations):
        entries = np.sort(rng.choice(pool, size=len(eligible), replace=True))
        directions = np.where(rng.random(len(eligible)) < long_share, 1, -1)
        stop_ratios = rng.choice(ratios, size=len(eligible), replace=True)
        holding = rng.choice(holds, size=len(eligible), replace=True)
        trade_rs = []
        for k, entry in enumerate(entries):
            entry = int(entry)
            if entry <= 0 or entry >= n:
                continue
            atr_value = float(md.atr[entry - 1])
            if not np.isfinite(atr_value) or atr_value <= 0:
                continue
            trade = simulate_trade(
                md, entry, int(directions[k]), float(stop_ratios[k] * atr_value),
                target_r=plan.target_r, trail_atr=plan.trail_atr,
                max_hold=int(holding[k]), exit_hour=None,
                exit_long=None, exit_short=None, costs=costs, config_id="NULL",
            )
            if trade is not None:
                trade_rs.append(trade.net_r)
        null_means[simulation] = float(np.mean(trade_rs)) if trade_rs else float("nan")

    valid = null_means[np.isfinite(null_means)]
    if valid.size == 0:
        return {"simulations": 0, "candidate_mean_r": candidate_mean,
                "null_p95": float("nan"), "percentile": float("nan"), "passed": False}
    p95 = float(np.quantile(valid, 0.95))
    percentile = float((np.count_nonzero(valid <= candidate_mean) + 1) / (valid.size + 1) * 100.0)
    return {
        "simulations": int(valid.size),
        "candidate_mean_r": candidate_mean,
        "null_mean_r": float(valid.mean()),
        "null_p95": p95,
        "percentile": percentile,
        "passed": bool(candidate_mean > p95),
    }


def perturb_spec(spec: StrategySpec, factor: float = 0.20) -> list[tuple[str, StrategySpec]]:
    """Create +/-factor variants for every non-categorical numeric parameter."""
    variants: list[tuple[str, StrategySpec]] = []
    seen: set[tuple[tuple[str, object], ...]] = set()
    for key, value in spec.params.items():
        if isinstance(value, (bool, str)) or value is None:
            continue
        if isinstance(value, (int, float)) and float(value) == 0.0:
            continue
        for sign, label in ((1.0, "plus"), (-1.0, "minus")):
            scaled = float(value) * (1.0 + sign * factor)
            if key == "exit_hour":
                new_value: object = int(round(scaled)) % 24
            elif isinstance(value, int):
                new_value = int(round(scaled))
                if new_value < 1:
                    continue
            else:
                new_value = round(scaled, 6)
            params = dict(spec.params)
            params[key] = new_value
            signature = tuple(sorted((k, v) for k, v in params.items()))
            if signature in seen or new_value == value:
                continue
            seen.add(signature)
            variants.append((f"{key}_{label}_{new_value}", StrategySpec(spec.config_id, spec.family, params)))
    return variants


def evaluate_core_gates(
    oos: dict,
    folds: dict,
    *,
    dsr: float,
    pbo: float,
    stress: dict,
    perturbation: dict | None,
    null: dict | None,
    min_trades: int = 100,
    min_positive_folds: int = 4,
    min_dsr: float = 0.95,
    max_pbo: float = 0.30,
    max_dd_r: float = 25.0,
    min_positive_years: int = 3,
) -> dict:
    terciles = oos.get("atr_tercile_positive_share") or []
    max_tercile = max(terciles) if terciles else float("nan")
    positive_years = sum(1 for y in oos.get("yearly", {}).values() if y["total_net_r"] > 0)
    gates = {
        "g1_trades": bool(oos["n"] >= min_trades),
        "g2_mean_ci": bool(oos["mean_net_r"] > 0 and oos.get("ci_low", float("-inf")) > 0),
        "g3_folds": bool(sum(1 for f in folds.values() if f["total_net_r"] > 0) >= min_positive_folds),
        "g4_dsr": bool(np.isfinite(dsr) and dsr >= min_dsr),
        "g5_pbo": bool(np.isfinite(pbo) and pbo <= max_pbo),
        "g6_cost_stress": bool(stress["mean_net_r"] > 0),
        "g7_perturbation": bool(perturbation is not None and perturbation.get("passed", False)),
        "g8_regime": bool(terciles and np.isfinite(max_tercile) and max_tercile <= 0.60
                          and positive_years >= min_positive_years),
        "g9_null": bool(null is not None and null.get("passed", False)),
        "g10_drawdown": bool(oos["max_drawdown_r"] <= max_dd_r),
    }
    gates["all"] = all(gates.values())
    gates["max_tercile_share"] = max_tercile
    gates["positive_years"] = positive_years
    return gates
