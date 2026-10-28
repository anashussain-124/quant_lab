"""Fail-closed H4 v6 FundingPips challenge-overlay adapter."""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from research.backtest_v6 import (
    V6BacktestConfig,
    V6SwapPolicy,
    V6Trade,
    _build_market,
    validate_v6_frame,
)
from research.challenge_v5 import (
    V5_COMMISSION_ROUND_TURN,
    AttemptResult,
    Bars,
    ChallengeRules,
    CostCase,
    Scenario,
    TradePath,
    simulate_attempt,
)
from research.constants_v6 import (
    V6_DATASET_BAR_COUNT,
    V6_DATASET_END_UTC,
    V6_DATASET_START_UTC,
    V6Environment,
)
from research.costs_v6 import V6CostModel
from research.v6_safety import require_v6_run_ready
from research.validation_pipeline_v6 import (
    V6_OOS_END_EXCLUSIVE,
    V6_OOS_START,
    V6ValidationReport,
)
from research.validation_v6 import GateResult, GateStatus

V6_CHALLENGE_FIRST_START = pd.Timestamp(datetime(2019, 1, 1, tzinfo=UTC))
V6_CHALLENGE_LAST_START = pd.Timestamp(datetime(2022, 7, 1, tzinfo=UTC))
V6_CHALLENGE_STEP_DAYS = 14
V6_CHALLENGE_MIN_FUTURE_DAYS = 180
V6_CHALLENGE_EVALUATION_END = pd.Timestamp(datetime(2022, 12, 30, 23, tzinfo=UTC))
V6_CHALLENGE_EXPECTED_STARTS = 92
V6_CHALLENGE_COMMISSION_USD = 10.0
V6_CHALLENGE_CONTRACT_SIZE = 100.0
V6_CHALLENGE_RISK_FRACTION = 0.005


@dataclass(frozen=True)
class V6ChallengeOverlayResult:
    environment: V6Environment
    gate: GateResult
    attempt_count: int
    pass_rate: float | None
    firm_breach_rate: float | None
    attempts: tuple[AttemptResult, ...]


def v6_challenge_start_times() -> tuple[pd.Timestamp, ...]:
    starts: list[pd.Timestamp] = []
    cursor = V6_CHALLENGE_FIRST_START
    while cursor <= V6_CHALLENGE_LAST_START:
        if (
            cursor + pd.Timedelta(days=V6_CHALLENGE_MIN_FUTURE_DAYS)
            <= V6_CHALLENGE_EVALUATION_END
        ):
            starts.append(cursor)
        cursor += pd.Timedelta(days=V6_CHALLENGE_STEP_DAYS)
    if len(starts) != V6_CHALLENGE_EXPECTED_STARTS:
        raise RuntimeError("frozen v6 challenge start schedule does not contain 92 starts")
    return tuple(starts)


def _challenge_rules() -> ChallengeRules:
    return ChallengeRules(
        initial_balance=5_000.0,
        phase_1_target_pct=0.08,
        phase_2_target_pct=0.05,
        overall_loss_pct=0.10,
        inactivity_days=30,
        daily_reset_hour_utc=21,
        phase_2_resets_to=5_000.0,
    )


def _primary_scenario() -> Scenario:
    return Scenario(
        name="v6_primary",
        daily_loss_pct=0.05,
        min_trading_days=3,
        carry_phase_2=False,
        internal_soft_guards=True,
        daily_soft_guard_pct=0.01,
        overall_soft_guard_pct=0.04,
    )


def _to_challenge_bars(
    frame: pd.DataFrame,
    *,
    config: V6BacktestConfig,
) -> Bars:
    market = _build_market(frame, np.ones(len(frame), dtype=float), config)
    return Bars(
        index=market.index,
        open=market.open,
        high=market.high,
        low=market.low,
        close=market.close,
        spread_price=market.spread_points * config.point_size,
    )


def _to_trade_paths(
    trades: Sequence[V6Trade],
    *,
    config: V6BacktestConfig,
) -> list[TradePath]:
    if config.contract_size_ounces_per_lot != V6_CHALLENGE_CONTRACT_SIZE:
        raise ValueError("challenge overlay requires the frozen 100-ounce XAUUSD contract")
    paths: list[TradePath] = []
    for trade in trades:
        if trade.environment is not V6Environment.BACKTEST:
            raise ValueError("challenge overlay accepts BACKTEST trades only")
        if trade.cost_stress_multiplier != 1.0:
            raise ValueError("challenge overlay requires base-cost BACKTEST trades")
        if trade.reason not in ("stop", "time"):
            raise ValueError(f"unsupported v6 exit reason for challenge: {trade.reason}")
        if trade.stop_distance <= 0 or not math.isfinite(trade.stop_distance):
            raise ValueError("challenge trade stop distance must be finite and positive")
        expected_net_r = (
            trade.gross_r
            - trade.spread_slippage_r
            - trade.commission_r
            + trade.swap_r
        )
        if not math.isclose(trade.net_r, expected_net_r, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("challenge trade net R does not reconcile to its cost fields")
        # The v5 challenge helper expects path costs at its $7 baseline, then
        # applies the frozen $10 v6 commission delta in _net_r().
        baseline_commission_r = V5_COMMISSION_ROUND_TURN / (
            config.contract_size_ounces_per_lot * trade.stop_distance
        )
        paths.append(
            TradePath(
                config_id=trade.config_id,
                entry_idx=trade.entry_index,
                exit_idx=trade.exit_index,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                direction=trade.direction,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                stop_distance=trade.stop_distance,
                gross_r=trade.gross_r,
                cost_r=trade.spread_slippage_r + baseline_commission_r,
                swap_r=trade.swap_r,
                reason=trade.reason,
                bars_held=trade.bars_held,
            )
        )
    paths.sort(key=lambda path: path.entry_time)
    if any(
        paths[position].entry_time < paths[position - 1].exit_time
        for position in range(1, len(paths))
    ):
        raise ValueError("challenge input contains overlapping candidate trades")
    return paths


def evaluate_v6_challenge_results(
    attempts: Sequence[AttemptResult],
    *,
    config_id: str,
) -> V6ChallengeOverlayResult:
    expected_starts = v6_challenge_start_times()
    if len(attempts) != V6_CHALLENGE_EXPECTED_STARTS:
        return V6ChallengeOverlayResult(
            V6Environment.BACKTEST,
            GateResult(
                GateStatus.NOT_EVALUABLE,
                "challenge overlay requires all 92 frozen rolling starts",
            ),
            len(attempts),
            None,
            None,
            tuple(attempts),
        )
    actual_starts = tuple(pd.Timestamp(attempt.start_utc) for attempt in attempts)
    if actual_starts != expected_starts:
        return V6ChallengeOverlayResult(
            V6Environment.BACKTEST,
            GateResult(GateStatus.NOT_EVALUABLE, "challenge start set/order is not frozen"),
            len(attempts),
            None,
            None,
            tuple(attempts),
        )
    if any(
        attempt.environment != V6Environment.BACKTEST.value
        or attempt.config_id != config_id
        for attempt in attempts
    ):
        raise ValueError("challenge attempts must share BACKTEST and candidate config ID")
    passes = sum(
        attempt.phase1_passed and attempt.phase2_passed for attempt in attempts
    )
    breaches = sum(
        attempt.breach_daily or attempt.breach_overall for attempt in attempts
    )
    pass_rate = passes / V6_CHALLENGE_EXPECTED_STARTS
    breach_rate = breaches / V6_CHALLENGE_EXPECTED_STARTS
    passed = pass_rate >= 0.20 and breach_rate <= 0.02
    return V6ChallengeOverlayResult(
        V6Environment.BACKTEST,
        GateResult(
            GateStatus.PASS if passed else GateStatus.FAIL,
            None
            if passed
            else "challenge pass rate is below 20% or firm breach rate exceeds 2%",
        ),
        V6_CHALLENGE_EXPECTED_STARTS,
        pass_rate,
        breach_rate,
        tuple(attempts),
    )


def run_v6_challenge_overlay(
    *,
    validation_report: V6ValidationReport,
    frame: pd.DataFrame,
    trades: Sequence[V6Trade],
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
) -> V6ChallengeOverlayResult:
    """Run gate 7b only after all ten core gates pass and policy is approved."""
    if validation_report.environment is not V6Environment.BACKTEST:
        raise ValueError("challenge overlay requires BACKTEST core gates")
    if validation_report.core_gates.overall is not GateStatus.PASS:
        return V6ChallengeOverlayResult(
            V6Environment.BACKTEST,
            GateResult(GateStatus.NOT_EVALUATED, "core gates did not all pass"),
            0,
            None,
            None,
            (),
        )

    config_id = validation_report.config_id
    require_v6_run_ready()
    validate_v6_frame(frame)
    if config.stress_multiplier != 1.0 or cost_model.stress_multiplier != 1.0:
        raise ValueError("challenge overlay uses the frozen base-cost scenario only")
    if cost_model.commission_round_turn_usd_per_lot != V6_CHALLENGE_COMMISSION_USD:
        raise ValueError("challenge overlay requires the frozen $10 round-turn assumption")
    if config.starting_balance_usd != 5_000.0:
        raise ValueError("challenge overlay requires the frozen $5,000 account")
    if trades and any(trade.config_id != config_id for trade in trades):
        raise ValueError("challenge trades must belong to the candidate config")
    if any(
        not V6_OOS_START <= trade.entry_time < V6_OOS_END_EXCLUSIVE
        for trade in trades
    ):
        raise ValueError("challenge overlay accepts only 2019-2022 validation trades")
    if swap_policy is None:
        raise ValueError("challenge overlay cannot use trades without a swap policy")
    if (
        frame.index[0] != pd.Timestamp(V6_DATASET_START_UTC)
        or frame.index[-1] != pd.Timestamp(V6_DATASET_END_UTC)
        or len(frame) != V6_DATASET_BAR_COUNT
    ):
        raise ValueError("challenge overlay requires the exact frozen H4 frame bounds")

    bars = _to_challenge_bars(frame, config=config)
    paths = _to_trade_paths(trades, config=config)
    starts = v6_challenge_start_times()
    entry_times = [path.entry_time for path in paths]
    horizon = min(V6_CHALLENGE_EVALUATION_END, frame.index[-1])
    scenario = _primary_scenario()
    cost_case = CostCase(
        name="v6_fp10_primary",
        fp_commission_round_turn_usd=V6_CHALLENGE_COMMISSION_USD,
        stress=1.0,
    )
    rules = _challenge_rules()
    attempts = tuple(
        simulate_attempt(
            paths,
            bisect.bisect_left(entry_times, start),
            bars,
            start,
            horizon,
            V6_CHALLENGE_RISK_FRACTION,
            scenario,
            cost_case,
            rules,
            config_id=config_id,
        )
        for start in starts
    )
    return evaluate_v6_challenge_results(attempts, config_id=config_id)
