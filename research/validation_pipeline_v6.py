"""Chronological H4 v6 fold and gate orchestration primitives."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from research.backtest_v6 import (
    V6BacktestResult,
    V6PerturbationBacktest,
    V6Trade,
)
from research.constants_v6 import V6Environment
from research.strategies_v5 import StrategySpec
from research.strategies_v6 import all_specs
from research.validation_v6 import (
    DsrEvaluation,
    EvaluationStatus,
    GateEvidence,
    GateReport,
    PboEvaluation,
    PerturbationEvaluation,
    RandomNullEvidence,
    bootstrap_mean_ci_v6,
    build_v6_perturbations,
    evaluate_v6_core_gates,
    evaluate_v6_dsr,
    evaluate_v6_pbo,
    evaluate_v6_perturbation,
)

V6_OOS_START = pd.Timestamp(datetime(2019, 1, 1, tzinfo=UTC))
V6_OOS_END_EXCLUSIVE = pd.Timestamp(datetime(2023, 1, 1, tzinfo=UTC))
V6_FOLD_YEARS = tuple(range(2017, 2023))
V6_FOLD_EMBARGO_BARS = 24


@dataclass(frozen=True)
class V6FoldWindow:
    year: int
    start: pd.Timestamp
    end_exclusive: pd.Timestamp
    first_index: int
    first_eligible_entry_index: int
    last_index: int


@dataclass(frozen=True)
class V6FoldSummary:
    year: int
    trade_count: int
    total_net_r: float
    mean_net_r: float | None


@dataclass(frozen=True)
class V6ValidationReport:
    environment: V6Environment
    config_id: str
    oos_trade_count: int
    oos_mean_net_r: float | None
    oos_total_net_r: float
    oos_max_drawdown_r: float | None
    bootstrap_ci_lower: float | None
    dsr: DsrEvaluation
    pbo: PboEvaluation
    folds: tuple[V6FoldSummary, ...]
    perturbation: PerturbationEvaluation
    atr_tercile_positive_shares: tuple[float, ...] | None
    positive_oos_years: int | None
    core_gates: GateReport


def build_v6_fold_windows(
    frame_index: pd.DatetimeIndex,
) -> tuple[V6FoldWindow, ...]:
    if frame_index.tz is None or str(frame_index.tz) != "UTC":
        raise ValueError("fold construction requires a timezone-aware UTC index")
    if frame_index.has_duplicates or not frame_index.is_monotonic_increasing:
        raise ValueError("fold timestamps must be unique and increasing")
    if len(frame_index) == 0:
        raise ValueError("fold construction requires non-empty data")

    windows: list[V6FoldWindow] = []
    for year in V6_FOLD_YEARS:
        start = pd.Timestamp(datetime(year, 1, 1, tzinfo=UTC))
        end = pd.Timestamp(datetime(year + 1, 1, 1, tzinfo=UTC))
        first = int(frame_index.searchsorted(start, side="left"))
        last = int(frame_index.searchsorted(end, side="left")) - 1
        if first >= len(frame_index) or last < first:
            raise ValueError(f"required stability fold {year} is absent")
        first_eligible = first + V6_FOLD_EMBARGO_BARS
        windows.append(
            V6FoldWindow(
                year,
                start,
                end,
                first,
                first_eligible,
                last,
            )
        )
    return tuple(windows)


def summarize_v6_folds(
    trades: Sequence[V6Trade],
    windows: Sequence[V6FoldWindow],
) -> tuple[V6FoldSummary, ...]:
    summaries: list[V6FoldSummary] = []
    for window in windows:
        fold_trades = [
            trade
            for trade in trades
            if window.start <= trade.entry_time < window.end_exclusive
            and trade.entry_index >= window.first_eligible_entry_index
        ]
        total = float(sum(trade.net_r for trade in fold_trades))
        if not math.isfinite(total):
            raise ValueError(f"fold {window.year} contains non-finite net R")
        summaries.append(
            V6FoldSummary(
                window.year,
                len(fold_trades),
                total,
                total / len(fold_trades) if fold_trades else None,
            )
        )
    return tuple(summaries)


def build_v6_pbo_fold_matrix(
    configuration_results: Sequence[V6BacktestResult],
    frame_index: pd.DatetimeIndex,
) -> np.ndarray | None:
    """Return the exact 6x24 frozen fold matrix or None when evidence is missing."""
    specs = all_specs()
    expected_ids = tuple(spec.config_id for spec in specs)
    if len(configuration_results) != len(expected_ids):
        return None
    if tuple(result.config_id for result in configuration_results) != expected_ids:
        raise ValueError("PBO results must be in exact frozen configuration order")
    if any(
        result.environment is not V6Environment.BACKTEST
        or result.cost_stress_multiplier != 1.0
        or any(
            trade.config_id != result.config_id
            or trade.environment is not V6Environment.BACKTEST
            or trade.cost_stress_multiplier != 1.0
            for trade in result.trades
        )
        for result in configuration_results
    ):
        raise ValueError("PBO requires base-cost BACKTEST results for all 24 configs")

    windows = build_v6_fold_windows(frame_index)
    matrix = np.full((len(windows), len(expected_ids)), np.nan, dtype=float)
    for column, result in enumerate(configuration_results):
        folds = summarize_v6_folds(result.trades, windows)
        for row, fold in enumerate(folds):
            if fold.mean_net_r is not None:
                matrix[row, column] = fold.mean_net_r
    return matrix


def build_v6_dsr_trial_sharpes(
    v5_trial_sharpes: Sequence[float | None],
    configuration_results: Sequence[V6BacktestResult],
) -> tuple[float | None, ...]:
    """Combine exactly the 50 prior and 24 frozen trial Sharpes without imputation."""
    expected_ids = tuple(spec.config_id for spec in all_specs())
    if len(configuration_results) != len(expected_ids):
        return tuple(v5_trial_sharpes)
    if tuple(result.config_id for result in configuration_results) != expected_ids:
        raise ValueError("DSR results must be in exact frozen configuration order")

    v6_sharpes: list[float | None] = []
    for result in configuration_results:
        if any(
            trade.config_id != result.config_id
            or trade.environment is not V6Environment.BACKTEST
            or trade.cost_stress_multiplier != 1.0
            for trade in result.trades
        ):
            raise ValueError("DSR results must contain base-cost BACKTEST trades")
        values = np.asarray(
            [
                trade.net_r
                for trade in result.trades
                if V6_OOS_START <= trade.entry_time < V6_OOS_END_EXCLUSIVE
            ],
            dtype=float,
        )
        if (
            result.environment is not V6Environment.BACKTEST
            or result.cost_stress_multiplier != 1.0
            or values.size < 2
            or not np.isfinite(values).all()
        ):
            v6_sharpes.append(None)
            continue
        deviation = float(values.std(ddof=1))
        v6_sharpes.append(
            float(values.mean() / deviation)
            if deviation > 0 and math.isfinite(deviation)
            else None
        )
    combined = tuple(v5_trial_sharpes) + tuple(v6_sharpes)
    return combined


def summarize_v6_perturbation_runs(
    runs: Sequence[V6PerturbationBacktest],
    *,
    spec: StrategySpec,
) -> tuple[float | None, ...] | None:
    """Extract every frozen-scope perturbation OOS mean in canonical order."""
    expected = build_v6_perturbations(spec)
    if len(runs) != len(expected):
        return None
    actual = tuple(
        (run.parameter, run.direction, dict(run.parameters)) for run in runs
    )
    expected_keys = tuple(
        (variant.parameter, variant.direction, dict(variant.parameters))
        for variant in expected
    )
    if actual != expected_keys:
        raise ValueError("perturbation results do not match the frozen variant set/order")
    means: list[float | None] = []
    for run in runs:
        if (
            run.base_config_id != spec.config_id
            or run.result.config_id != spec.config_id
            or run.result.environment is not V6Environment.BACKTEST
            or run.result.cost_stress_multiplier != 1.0
            or any(
                trade.config_id != spec.config_id
                or trade.cost_stress_multiplier != 1.0
                for trade in run.result.trades
            )
        ):
            raise ValueError("perturbation result provenance does not match its base config")
        trades = _oos_trades(run.result.trades)
        values = [trade.net_r for trade in trades]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("perturbation OOS returns must be finite")
        means.append(float(np.mean(values)) if values else None)
    return tuple(means)


def _oos_trades(trades: Sequence[V6Trade]) -> list[V6Trade]:
    return [
        trade
        for trade in trades
        if V6_OOS_START <= trade.entry_time < V6_OOS_END_EXCLUSIVE
    ]


def _summarize_atr_terciles(
    trades: Sequence[V6Trade],
) -> tuple[float, float, float] | None:
    if len(trades) < 3:
        return None
    atr = np.asarray([trade.atr_entry for trade in trades], dtype=float)
    net = np.asarray([trade.net_r for trade in trades], dtype=float)
    if not np.isfinite(atr).all() or not np.isfinite(net).all():
        raise ValueError("ATR-tercile inputs must be finite")
    if (atr <= 0).any():
        raise ValueError("trade ATR values must be positive")
    edges = np.quantile(atr, [1.0 / 3.0, 2.0 / 3.0])
    buckets = np.digitize(atr, edges)
    positive_total = float(net[net > 0].sum())
    if positive_total <= 0:
        return None
    shares = tuple(
        float(net[(buckets == bucket) & (net > 0)].sum() / positive_total)
        for bucket in range(3)
    )
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in shares):
        raise ValueError("ATR-tercile positive-return shares are invalid")
    return (shares[0], shares[1], shares[2])


def evaluate_v6_candidate(
    trades: Sequence[V6Trade],
    *,
    spec: StrategySpec,
    frame_index: pd.DatetimeIndex,
    trial_sharpes: Sequence[float | None],
    all_configuration_fold_means: Sequence[Sequence[float]] | np.ndarray | None,
    stressed_trades: Sequence[V6Trade] | None,
    perturbation_means: Sequence[float | None] | None,
    random_null: RandomNullEvidence | None,
) -> V6ValidationReport:
    """Evaluate one result bundle; missing artifacts remain NOT EVALUABLE."""
    windows = build_v6_fold_windows(frame_index)
    if any(trade.config_id != spec.config_id for trade in trades):
        raise ValueError("candidate trades must all belong to the requested config")
    if any(trade.environment is not V6Environment.BACKTEST for trade in trades):
        raise ValueError("candidate trades must be labeled BACKTEST")
    if random_null is not None and random_null.config_id != spec.config_id:
        raise ValueError("random-null evidence must belong to the candidate config")
    if any(trade.cost_stress_multiplier != 1.0 for trade in trades):
        raise ValueError("base candidate trades must use the frozen 1.0x cost model")
    if any(
        trades[position].entry_time > trades[position + 1].entry_time
        for position in range(len(trades) - 1)
    ):
        raise ValueError("candidate trades must be ordered by entry time")
    if stressed_trades is not None and any(
        trade.config_id != spec.config_id
        or trade.environment is not V6Environment.BACKTEST
        or trade.cost_stress_multiplier != 1.5
        for trade in stressed_trades
    ):
        raise ValueError("stress trades must be the same BACKTEST config at exactly 1.5x")
    if stressed_trades is not None and any(
        stressed_trades[position].entry_time
        > stressed_trades[position + 1].entry_time
        for position in range(len(stressed_trades) - 1)
    ):
        raise ValueError("stress trades must be ordered by entry time")
    folds = summarize_v6_folds(trades, windows)
    oos = _oos_trades(trades)
    net = [trade.net_r for trade in oos]
    if any(not math.isfinite(value) for value in net):
        raise ValueError("OOS trade outcomes must be finite")
    mean = float(np.mean(net)) if net else None
    total = float(sum(net))
    equity = np.concatenate(([0.0], np.cumsum(net)))
    drawdown = float(np.max(np.maximum.accumulate(equity) - equity)) if net else None

    bootstrap = bootstrap_mean_ci_v6(
        net,
        environment=V6Environment.BACKTEST,
    )
    dsr = evaluate_v6_dsr(
        net,
        trial_sharpes,
        environment=V6Environment.BACKTEST,
    )
    if all_configuration_fold_means is None:
        pbo = PboEvaluation(
            V6Environment.BACKTEST,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            6,
            24,
            20,
            "all-configuration fold matrix is unavailable",
        )
    else:
        pbo = evaluate_v6_pbo(
            all_configuration_fold_means,
            environment=V6Environment.BACKTEST,
        )

    stressed_oos = _oos_trades(stressed_trades or ())
    stressed_mean = (
        float(np.mean([trade.net_r for trade in stressed_oos]))
        if stressed_oos
        else None
    )
    expected_variants = len(build_v6_perturbations(spec))
    if perturbation_means is None or len(perturbation_means) != expected_variants:
        perturbation = PerturbationEvaluation(
            V6Environment.BACKTEST,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            f"expected all {expected_variants} frozen-scope perturbation results",
        )
    else:
        perturbation = evaluate_v6_perturbation(
            mean,
            perturbation_means,
            environment=V6Environment.BACKTEST,
        )
    positive_years = sum(
        1
        for year in range(2019, 2023)
        if sum(
            trade.net_r
            for trade in oos
            if trade.entry_time.year == year
        )
        > 0
    )
    atr_shares = _summarize_atr_terciles(oos)
    positive_folds = sum(fold.total_net_r > 0 for fold in folds)

    evidence = GateEvidence(
        trade_count=len(oos),
        mean_net_r=mean,
        bootstrap_ci_lower=bootstrap.lower,
        positive_fold_count=positive_folds,
        dsr=dsr,
        pbo=pbo,
        stressed_mean_net_r=stressed_mean,
        perturbation_passed=(
            perturbation.status is EvaluationStatus.PASS
            if perturbation.status
            in (EvaluationStatus.PASS, EvaluationStatus.FAIL)
            else None
        ),
        atr_tercile_shares=atr_shares,
        positive_year_count=positive_years if oos else None,
        max_drawdown_r=drawdown,
    )
    gate_report = evaluate_v6_core_gates(
        evidence,
        environment=V6Environment.BACKTEST,
        random_entry_evaluator=(lambda: random_null) if random_null is not None else None,
    )
    return V6ValidationReport(
        V6Environment.BACKTEST,
        spec.config_id,
        len(oos),
        mean,
        total,
        drawdown,
        bootstrap.lower,
        dsr,
        pbo,
        folds,
        perturbation,
        atr_shares,
        positive_years if oos else None,
        gate_report,
    )


def evaluate_v6_grid_candidate(
    base_result: V6BacktestResult,
    *,
    spec: StrategySpec,
    frame_index: pd.DatetimeIndex,
    configuration_results: Sequence[V6BacktestResult],
    v5_trial_sharpes: Sequence[float | None],
    stressed_result: V6BacktestResult | None,
    perturbation_runs: Sequence[V6PerturbationBacktest] | None,
    random_null: RandomNullEvidence | None,
) -> V6ValidationReport:
    """Build frozen matrix evidence and evaluate one configuration's core gates."""
    if base_result.config_id != spec.config_id:
        raise ValueError("base result does not match the requested frozen config")
    if base_result.cost_stress_multiplier != 1.0:
        raise ValueError("base result must use 1.0x costs")
    fold_matrix = build_v6_pbo_fold_matrix(configuration_results, frame_index)
    trial_sharpes = build_v6_dsr_trial_sharpes(
        v5_trial_sharpes,
        configuration_results,
    )
    perturbation_means = (
        summarize_v6_perturbation_runs(perturbation_runs, spec=spec)
        if perturbation_runs is not None
        else None
    )
    return evaluate_v6_candidate(
        base_result.trades,
        spec=spec,
        frame_index=frame_index,
        trial_sharpes=trial_sharpes,
        all_configuration_fold_means=fold_matrix,
        stressed_trades=stressed_result.trades if stressed_result is not None else None,
        perturbation_means=perturbation_means,
        random_null=random_null,
    )
