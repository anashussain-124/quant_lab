"""Pure H4 v6 metric, trial-correction, perturbation, and gate functions."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from numbers import Real
from statistics import median

import numpy as np

from research.constants_v6 import (
    V6_BOOTSTRAP_RESAMPLES,
    V6_NULL_SIMULATIONS,
    V6_RANDOM_SEED,
    V6_REQUIRED_DSR_TRIALS,
    V6Environment,
)
from research.strategies_v5 import StrategySpec
from research.strategies_v6 import all_specs
from research.validation_v5 import (
    bootstrap_mean_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
)

PERTURBABLE_V6_PARAMETERS = frozenset(
    {"n", "stop_atr", "trail_atr", "vol_filter"}
)


class EvaluationStatus(StrEnum):
    EVALUATED = "EVALUATED"
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT EVALUABLE"
    NOT_EVALUATED = "NOT EVALUATED"


@dataclass(frozen=True)
class TradeOutcome:
    environment: V6Environment
    gross_r: float
    cost_r: float
    swap_r: float
    bars_held: int

    @property
    def net_r(self) -> float:
        return self.gross_r - self.cost_r + self.swap_r


@dataclass(frozen=True)
class MetricSummary:
    environment: V6Environment
    status: EvaluationStatus
    trade_count: int
    gross_profit_r: float | None
    gross_loss_r: float | None
    net_profit_r: float | None
    win_rate: float | None
    average_win_r: float | None
    average_loss_r: float | None
    expectancy_r: float | None
    profit_factor: float | None
    profit_factor_status: str
    max_drawdown_r: float | None
    sharpe_per_trade: float | None
    average_holding_bars: float | None


def summarize_trade_outcomes(
    outcomes: Sequence[TradeOutcome],
    *,
    environment: V6Environment,
) -> MetricSummary:
    environment = _require_metric_environment(environment)
    if not outcomes:
        return MetricSummary(
            environment=environment,
            status=EvaluationStatus.NOT_EVALUABLE,
            trade_count=0,
            gross_profit_r=None,
            gross_loss_r=None,
            net_profit_r=None,
            win_rate=None,
            average_win_r=None,
            average_loss_r=None,
            expectancy_r=None,
            profit_factor=None,
            profit_factor_status="NO_TRADES",
            max_drawdown_r=None,
            sharpe_per_trade=None,
            average_holding_bars=None,
        )

    for outcome in outcomes:
        if _require_metric_environment(outcome.environment) is not environment:
            raise ValueError("trade outcomes must share the summary environment")
        values = (outcome.gross_r, outcome.cost_r, outcome.swap_r)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("trade outcome values must be finite")
        if outcome.cost_r < 0:
            raise ValueError("cost_r must be a non-negative cost")
        if (
            isinstance(outcome.bars_held, bool)
            or not isinstance(outcome.bars_held, int)
            or outcome.bars_held < 1
        ):
            raise ValueError("bars_held must be a positive integer")

    gross = np.asarray([outcome.gross_r for outcome in outcomes], dtype=float)
    net = np.asarray([outcome.net_r for outcome in outcomes], dtype=float)
    holding = np.asarray([outcome.bars_held for outcome in outcomes], dtype=float)
    wins = net[net > 0]
    losses = net[net < 0]
    gross_profit = float(gross[gross > 0].sum())
    gross_loss = float(-gross[gross < 0].sum())
    net_profit = float(net.sum())
    loss_total = float(-losses.sum())

    if loss_total > 0:
        profit_factor = float(wins.sum() / loss_total)
        profit_factor_status = "DEFINED"
    else:
        profit_factor = None
        profit_factor_status = "NO_NET_LOSSES"

    equity = np.concatenate(([0.0], np.cumsum(net)))
    max_drawdown = float(np.max(np.maximum.accumulate(equity) - equity))
    std = float(net.std(ddof=1)) if net.size > 1 else 0.0
    sharpe = float(net.mean() / std) if std > 0 else None

    return MetricSummary(
        environment=environment,
        status=EvaluationStatus.EVALUATED,
        trade_count=len(outcomes),
        gross_profit_r=gross_profit,
        gross_loss_r=gross_loss,
        net_profit_r=net_profit,
        win_rate=float((net > 0).mean()),
        average_win_r=float(wins.mean()) if wins.size else None,
        average_loss_r=float(losses.mean()) if losses.size else None,
        expectancy_r=float(net.mean()),
        profit_factor=profit_factor,
        profit_factor_status=profit_factor_status,
        max_drawdown_r=max_drawdown,
        sharpe_per_trade=sharpe,
        average_holding_bars=float(holding.mean()),
    )


@dataclass(frozen=True)
class BootstrapCIResult:
    environment: V6Environment
    status: EvaluationStatus
    lower: float | None
    upper: float | None


def bootstrap_mean_ci_v6(
    net_returns: Sequence[float],
    *,
    environment: V6Environment,
) -> BootstrapCIResult:
    environment = _require_metric_environment(environment)
    if len(net_returns) < 2:
        return BootstrapCIResult(
            environment, EvaluationStatus.NOT_EVALUABLE, None, None
        )
    if not all(math.isfinite(value) for value in net_returns):
        raise ValueError("net returns must be finite; values are never silently dropped")
    low, high = bootstrap_mean_ci(
        net_returns,
        resamples=V6_BOOTSTRAP_RESAMPLES,
        seed=V6_RANDOM_SEED,
    )
    if not math.isfinite(low) or not math.isfinite(high):
        return BootstrapCIResult(
            environment, EvaluationStatus.NOT_EVALUABLE, None, None
        )
    return BootstrapCIResult(
        environment,
        EvaluationStatus.EVALUATED,
        float(low),
        float(high),
    )


@dataclass(frozen=True)
class DsrEvaluation:
    environment: V6Environment
    status: EvaluationStatus
    value: float | None
    required_trial_count: int
    supplied_trial_count: int
    reason: str | None


@dataclass(frozen=True)
class PboEvaluation:
    environment: V6Environment
    status: EvaluationStatus
    value: float | None
    fold_count: int
    configuration_count: int
    split_count: int
    reason: str | None


def evaluate_v6_pbo(
    fold_matrix: Sequence[Sequence[float]] | np.ndarray,
    *,
    environment: V6Environment,
) -> PboEvaluation:
    environment = _require_metric_environment(environment)
    matrix = np.asarray(fold_matrix, dtype=float)
    if matrix.shape != (6, 24):
        raise ValueError("v6 PBO requires exactly six folds and 24 configurations")
    if not np.isfinite(matrix).all():
        return PboEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            6,
            24,
            20,
            "all 144 fold/configuration values must be finite",
        )

    result = cscv_pbo(matrix)
    if result["splits"] != 20:
        raise RuntimeError("CSCV implementation did not evaluate the 20 frozen splits")
    value = float(result["pbo"])
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return PboEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            6,
            24,
            20,
            "CSCV returned an invalid PBO value",
        )
    return PboEvaluation(
        environment,
        EvaluationStatus.EVALUATED,
        value,
        6,
        24,
        20,
        None,
    )


def evaluate_v6_dsr(
    candidate_net_returns: Sequence[float],
    trial_sharpes: Sequence[float | None],
    *,
    environment: V6Environment,
) -> DsrEvaluation:
    environment = _require_metric_environment(environment)
    supplied = len(trial_sharpes)
    if supplied != V6_REQUIRED_DSR_TRIALS:
        return DsrEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            V6_REQUIRED_DSR_TRIALS,
            supplied,
            f"expected all {V6_REQUIRED_DSR_TRIALS} preregistered trial Sharpes",
        )
    if any(
        value is None
        or isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        for value in trial_sharpes
    ):
        return DsrEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            V6_REQUIRED_DSR_TRIALS,
            supplied,
            "at least one of the 74 trial Sharpes is missing or invalid",
        )
    if len(candidate_net_returns) < 2 or any(
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        for value in candidate_net_returns
    ):
        return DsrEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            V6_REQUIRED_DSR_TRIALS,
            supplied,
            "candidate returns must contain at least two finite values",
        )

    value = float(
        deflated_sharpe_ratio(
            candidate_net_returns,
            [float(sharpe) for sharpe in trial_sharpes if sharpe is not None],
        )
    )
    if not math.isfinite(value):
        return DsrEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            V6_REQUIRED_DSR_TRIALS,
            supplied,
            "DSR calculation returned a non-finite value",
        )
    return DsrEvaluation(
        environment,
        EvaluationStatus.EVALUATED,
        value,
        V6_REQUIRED_DSR_TRIALS,
        supplied,
        None,
    )


@dataclass(frozen=True)
class PerturbationVariant:
    base_config_id: str
    parameter: str
    direction: str
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class PerturbationEvaluation:
    environment: V6Environment
    status: EvaluationStatus
    median_retention: float | None
    reason: str | None


def build_v6_perturbations(spec: StrategySpec) -> tuple[PerturbationVariant, ...]:
    frozen = {candidate.config_id: candidate for candidate in all_specs()}
    base = frozen.get(spec.config_id)
    if base is None or base.params != spec.params or base.family != spec.family:
        raise ValueError("perturbations are only defined for unchanged frozen v6 specs")

    variants: list[PerturbationVariant] = []
    for name, value in spec.params.items():
        if isinstance(value, bool) or value is None or isinstance(value, str):
            continue
        if not isinstance(value, Real):
            raise ValueError(f"unsupported parameter value for {name}")
        if name not in PERTURBABLE_V6_PARAMETERS:
            raise ValueError(f"numeric parameter {name} is outside approved scope")
        if float(value) == 0.0:
            continue
        for direction, multiplier in (("plus", 1.2), ("minus", 0.8)):
            changed: int | float
            scaled = float(value) * multiplier
            if isinstance(value, int):
                changed = int(round(scaled))
                if changed < 1:
                    raise ValueError(f"perturbation makes {name} invalid")
            else:
                changed = round(scaled, 6)
            parameters = dict(spec.params)
            parameters[name] = changed
            variants.append(
                PerturbationVariant(
                    base_config_id=spec.config_id,
                    parameter=name,
                    direction=direction,
                    parameters=parameters,
                )
            )
    return tuple(variants)


def evaluate_v6_perturbation(
    base_mean_net_r: float | None,
    variant_means_net_r: Sequence[float | None],
    *,
    environment: V6Environment,
) -> PerturbationEvaluation:
    environment = _require_metric_environment(environment)
    if (
        base_mean_net_r is None
        or isinstance(base_mean_net_r, bool)
        or not isinstance(base_mean_net_r, Real)
        or not math.isfinite(base_mean_net_r)
        or not variant_means_net_r
    ):
        return PerturbationEvaluation(
            environment,
            EvaluationStatus.NOT_EVALUABLE,
            None,
            "base result or perturbation results are unavailable",
        )
    values: list[float] = []
    for value in variant_means_net_r:
        if (
            value is None
            or isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
        ):
            return PerturbationEvaluation(
                environment,
                EvaluationStatus.NOT_EVALUABLE,
                None,
                "every approved perturbation must have a finite result",
            )
        values.append(float(value))
    if base_mean_net_r <= 0:
        return PerturbationEvaluation(
            environment,
            EvaluationStatus.FAIL,
            None,
            "base OOS mean net R is not positive",
        )

    retention = median(values) / base_mean_net_r
    passed = all(value > 0 for value in values) and retention >= 0.70
    return PerturbationEvaluation(
        environment,
        EvaluationStatus.PASS if passed else EvaluationStatus.FAIL,
        retention,
        None if passed else "a variant is non-positive or median retention is below 70%",
    )


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT EVALUABLE"
    NOT_EVALUATED = "NOT EVALUATED"


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    reason: str | None = None


@dataclass(frozen=True)
class GateEvidence:
    trade_count: int | None = None
    mean_net_r: float | None = None
    bootstrap_ci_lower: float | None = None
    positive_fold_count: int | None = None
    dsr: DsrEvaluation | None = None
    pbo: PboEvaluation | None = None
    stressed_mean_net_r: float | None = None
    perturbation_passed: bool | None = None
    atr_tercile_shares: tuple[float, ...] | None = None
    positive_year_count: int | None = None
    max_drawdown_r: float | None = None


@dataclass(frozen=True)
class GateReport:
    environment: V6Environment
    gates: Mapping[str, GateResult]
    overall: GateStatus


@dataclass(frozen=True)
class RandomNullEvidence:
    config_id: str
    environment: V6Environment
    simulation_count: int
    seed: int
    valid_simulation_count: int
    simulation_means_net_r: tuple[float | None, ...]
    trade_count_matched: bool
    direction_mix_matched: bool
    stop_distance_distribution_matched: bool
    holding_period_matched: bool


def _finite_or_missing(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number or None")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def evaluate_v6_random_entry_gate(
    candidate_mean_net_r: float | None,
    null_evidence: RandomNullEvidence | None,
    *,
    environment: V6Environment,
) -> GateResult:
    environment = _require_metric_environment(environment)
    if candidate_mean_net_r is None or null_evidence is None:
        return GateResult(
            GateStatus.NOT_EVALUABLE,
            "candidate or matched null evidence is unavailable",
        )
    if _require_metric_environment(null_evidence.environment) is not environment:
        raise ValueError("random-null evidence environment does not match")
    if not null_evidence.config_id.strip():
        raise ValueError("random-null evidence must identify its frozen config")
    if (
        isinstance(null_evidence.simulation_count, bool)
        or not isinstance(null_evidence.simulation_count, int)
        or null_evidence.simulation_count != V6_NULL_SIMULATIONS
        or isinstance(null_evidence.valid_simulation_count, bool)
        or not isinstance(null_evidence.valid_simulation_count, int)
        or null_evidence.valid_simulation_count != V6_NULL_SIMULATIONS
        or isinstance(null_evidence.seed, bool)
        or not isinstance(null_evidence.seed, int)
        or null_evidence.seed != V6_RANDOM_SEED
    ):
        return GateResult(
            GateStatus.NOT_EVALUABLE,
            "random-null simulation count or seed differs from preregistration",
        )
    match_flags = (
        null_evidence.trade_count_matched,
        null_evidence.direction_mix_matched,
        null_evidence.stop_distance_distribution_matched,
        null_evidence.holding_period_matched,
    )
    if any(not isinstance(flag, bool) for flag in match_flags) or not all(match_flags):
        return GateResult(
            GateStatus.NOT_EVALUABLE,
            "random-null matching requirements are not verified",
        )
    if len(null_evidence.simulation_means_net_r) != V6_NULL_SIMULATIONS:
        return GateResult(
            GateStatus.NOT_EVALUABLE,
            "random-null must provide exactly 500 simulation mean returns",
        )
    if any(
        value is None
        or isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        for value in null_evidence.simulation_means_net_r
    ):
        return GateResult(
            GateStatus.NOT_EVALUABLE,
            "at least one random-null simulation mean is missing or invalid",
        )
    candidate = _finite_or_missing("candidate_mean_net_r", candidate_mean_net_r)
    if candidate is None:
        return GateResult(GateStatus.NOT_EVALUABLE, "candidate mean net R is missing")
    null_means = np.asarray(null_evidence.simulation_means_net_r, dtype=float)
    null_p95 = float(np.quantile(null_means, 0.95))
    if not math.isfinite(null_p95):
        return GateResult(GateStatus.NOT_EVALUABLE, "random-null p95 is invalid")
    passed = candidate > null_p95
    return GateResult(
        GateStatus.PASS if passed else GateStatus.FAIL,
        None if passed else "candidate mean net R does not exceed matched null p95",
    )


def _require_metric_environment(environment: V6Environment) -> V6Environment:
    try:
        validated = V6Environment(environment)
    except ValueError as exc:
        raise ValueError(f"invalid v6 environment: {environment}") from exc
    if validated not in (V6Environment.BACKTEST, V6Environment.SIMULATED):
        raise ValueError("v6 metrics must be labeled BACKTEST or SIMULATED")
    return validated


def evaluate_v6_core_gates(
    evidence: GateEvidence,
    *,
    environment: V6Environment,
    random_entry_evaluator: Callable[[], RandomNullEvidence] | None = None,
) -> GateReport:
    """Evaluate gates in protocol order; gate 9 callback is deferred until 1-8,10 pass."""
    environment = _require_metric_environment(environment)
    if evidence.trade_count is not None and (
        isinstance(evidence.trade_count, bool)
        or not isinstance(evidence.trade_count, int)
        or evidence.trade_count < 0
    ):
        raise ValueError("trade_count must be a non-negative integer or None")
    if evidence.positive_fold_count is not None and (
        isinstance(evidence.positive_fold_count, bool)
        or not isinstance(evidence.positive_fold_count, int)
        or not 0 <= evidence.positive_fold_count <= 6
    ):
        raise ValueError("positive_fold_count must be between 0 and 6")
    if evidence.positive_year_count is not None and (
        isinstance(evidence.positive_year_count, bool)
        or not isinstance(evidence.positive_year_count, int)
        or not 0 <= evidence.positive_year_count <= 4
    ):
        raise ValueError("positive_year_count must be between 0 and 4")

    mean = _finite_or_missing("mean_net_r", evidence.mean_net_r)
    ci_lower = _finite_or_missing("bootstrap_ci_lower", evidence.bootstrap_ci_lower)
    dsr: float | None = None
    if evidence.dsr is not None:
        if _require_metric_environment(evidence.dsr.environment) is not environment:
            raise ValueError("DSR environment does not match gate environment")
        if (
            evidence.dsr.status is EvaluationStatus.EVALUATED
            and evidence.dsr.required_trial_count == V6_REQUIRED_DSR_TRIALS
            and evidence.dsr.supplied_trial_count == V6_REQUIRED_DSR_TRIALS
            and evidence.dsr.value is not None
        ):
            dsr = _finite_or_missing("dsr", evidence.dsr.value)

    pbo: float | None = None
    if evidence.pbo is not None:
        if _require_metric_environment(evidence.pbo.environment) is not environment:
            raise ValueError("PBO environment does not match gate environment")
        if (
            evidence.pbo.status is EvaluationStatus.EVALUATED
            and evidence.pbo.fold_count == 6
            and evidence.pbo.configuration_count == 24
            and evidence.pbo.split_count == 20
            and evidence.pbo.value is not None
        ):
            pbo = _finite_or_missing("pbo", evidence.pbo.value)
    stress_mean = _finite_or_missing(
        "stressed_mean_net_r", evidence.stressed_mean_net_r
    )
    max_drawdown = _finite_or_missing("max_drawdown_r", evidence.max_drawdown_r)
    if max_drawdown is not None and max_drawdown < 0:
        raise ValueError("max_drawdown_r must be non-negative")
    if dsr is not None and not 0.0 <= dsr <= 1.0:
        raise ValueError("dsr must be between 0 and 1")
    if pbo is not None and not 0.0 <= pbo <= 1.0:
        raise ValueError("pbo must be between 0 and 1")
    if evidence.perturbation_passed is not None and not isinstance(
        evidence.perturbation_passed, bool
    ):
        raise ValueError("perturbation_passed must be bool or None")

    terciles = evidence.atr_tercile_shares
    if terciles is not None:
        if len(terciles) != 3:
            raise ValueError("exactly three ATR-tercile shares are required")
        valid_shares: list[float] = []
        for index, value in enumerate(terciles):
            share = _finite_or_missing(f"atr_tercile_shares[{index}]", value)
            if share is None or not 0 <= share <= 1:
                raise ValueError("ATR-tercile shares must be between 0 and 1")
            valid_shares.append(share)
        terciles = tuple(valid_shares)

    def threshold(
        present: bool, passed: bool, missing_reason: str, fail_reason: str
    ) -> GateResult:
        if not present:
            return GateResult(GateStatus.NOT_EVALUABLE, missing_reason)
        return GateResult(GateStatus.PASS if passed else GateStatus.FAIL,
                          None if passed else fail_reason)

    preliminary: dict[str, GateResult] = {
        "g1_trades": threshold(
            evidence.trade_count is not None,
            evidence.trade_count is not None and evidence.trade_count >= 100,
            "trade count is missing",
            "OOS trade count is below 100",
        ),
        "g2_mean_ci": threshold(
            mean is not None and ci_lower is not None,
            mean is not None and ci_lower is not None and mean > 0 and ci_lower > 0,
            "mean or bootstrap lower bound is missing",
            "mean net R or bootstrap lower bound is not positive",
        ),
        "g3_folds": threshold(
            evidence.positive_fold_count is not None,
            evidence.positive_fold_count is not None
            and evidence.positive_fold_count >= 4,
            "fold evidence is missing",
            "fewer than four stability folds are positive",
        ),
        "g4_dsr": threshold(
            dsr is not None,
            dsr is not None and dsr >= 0.95,
            "DSR is NOT EVALUABLE",
            "DSR is below 0.95",
        ),
        "g5_pbo": threshold(
            pbo is not None,
            pbo is not None and pbo <= 0.30,
            "PBO is missing",
            "PBO exceeds 0.30",
        ),
        "g6_cost_stress": threshold(
            stress_mean is not None,
            stress_mean is not None and stress_mean > 0,
            "1.5x cost-stress result is missing",
            "stressed mean net R is not positive",
        ),
        "g7_perturbation": threshold(
            evidence.perturbation_passed is not None,
            evidence.perturbation_passed is True,
            "perturbation result is missing",
            "perturbation gate failed",
        ),
        "g8_regime": threshold(
            terciles is not None and evidence.positive_year_count is not None,
            terciles is not None
            and evidence.positive_year_count is not None
            and max(terciles) <= 0.60
            and evidence.positive_year_count >= 3,
            "ATR-tercile or yearly evidence is missing",
            "regime concentration or positive-year requirement failed",
        ),
        "g10_drawdown": threshold(
            max_drawdown is not None,
            max_drawdown is not None and max_drawdown <= 25.0,
            "maximum drawdown is missing",
            "OOS maximum drawdown exceeds 25R",
        ),
    }

    if all(result.status is GateStatus.PASS for result in preliminary.values()):
        if random_entry_evaluator is None:
            gate9 = GateResult(
                GateStatus.NOT_EVALUABLE, "matched random-entry result is unavailable"
            )
        else:
            null_evidence = random_entry_evaluator()
            if not isinstance(null_evidence, RandomNullEvidence):
                raise ValueError("random-null evaluator must return RandomNullEvidence")
            gate9 = evaluate_v6_random_entry_gate(
                mean,
                null_evidence,
                environment=environment,
            )
    else:
        gate9 = GateResult(
            GateStatus.NOT_EVALUATED,
            "gate 9 is run only after gates 1-8 and 10 pass",
        )

    ordered: dict[str, GateResult] = {}
    for gate_id in (
        "g1_trades",
        "g2_mean_ci",
        "g3_folds",
        "g4_dsr",
        "g5_pbo",
        "g6_cost_stress",
        "g7_perturbation",
        "g8_regime",
        "g10_drawdown",
        "g9_null",
    ):
        ordered[gate_id] = gate9 if gate_id == "g9_null" else preliminary[gate_id]

    statuses = [result.status for result in ordered.values()]
    if GateStatus.FAIL in statuses:
        overall = GateStatus.FAIL
    elif all(status is GateStatus.PASS for status in statuses):
        overall = GateStatus.PASS
    else:
        overall = GateStatus.NOT_EVALUABLE
    return GateReport(environment=environment, gates=ordered, overall=overall)
