"""Synthetic tests for H4 v6 challenge-gate evaluation and ordering."""

from __future__ import annotations

import pandas as pd

from research.backtest_v6 import V6BacktestConfig
from research.challenge_overlay_v6 import (
    V6_CHALLENGE_EXPECTED_STARTS,
    evaluate_v6_challenge_results,
    run_v6_challenge_overlay,
    v6_challenge_start_times,
)
from research.challenge_v5 import AttemptResult
from research.constants_v6 import V6Environment
from research.costs_v6 import V6CostModel
from research.strategies_v6 import all_specs
from research.validation_pipeline_v6 import evaluate_v6_candidate
from research.validation_v6 import GateResult, GateStatus


def make_attempt(
    index: int,
    *,
    passed: bool = False,
    breach: bool = False,
) -> AttemptResult:
    start = v6_challenge_start_times()[index]
    return AttemptResult(
        environment=V6Environment.BACKTEST.value,
        config_id="h4d01",
        risk_fraction=0.005,
        scenario="v6_primary",
        cost_case="v6_fp10_primary",
        start_utc=start.isoformat(),
        outcome="PASS" if passed else "FAIL_DAILY" if breach else "INCOMPLETE",
        phase_reached=2 if passed else 1,
        phase1_passed=passed,
        phase2_passed=passed,
        phase2_start_balance=5_000.0 if passed else None,
        days_to_pass=30.0 if passed else None,
        end_utc=start.isoformat(),
        trades_taken=10 if passed or breach else 0,
        skipped_min_lot=0,
        skipped_room=0,
        max_dd_usd=0.0,
        max_dd_pct=0.0,
        min_overall_room_usd=None,
        min_daily_room_usd=None,
        near_overall=False,
        near_daily=False,
        breach_daily=breach,
        breach_overall=False,
        soft_guard=False,
        inactivity=not passed and not breach,
        incomplete=not passed and not breach,
        detail="synthetic",
    )


def test_v6_challenge_start_schedule_is_exactly_92_frozen_starts() -> None:
    starts = v6_challenge_start_times()
    assert len(starts) == V6_CHALLENGE_EXPECTED_STARTS == 92
    assert starts[0].isoformat() == "2019-01-01T00:00:00+00:00"
    assert starts[-1].isoformat() == "2022-06-28T00:00:00+00:00"


def test_challenge_gate_requires_all_92_starts_and_frozen_rates() -> None:
    insufficient = evaluate_v6_challenge_results(
        [make_attempt(index) for index in range(91)],
        config_id="h4d01",
    )
    assert insufficient.gate.status is GateStatus.NOT_EVALUABLE

    passing = [make_attempt(index, passed=index < 20) for index in range(92)]
    passing[20] = make_attempt(20, breach=True)
    result = evaluate_v6_challenge_results(passing, config_id="h4d01")
    assert result.attempt_count == 92
    assert result.pass_rate == 20 / 92
    assert result.firm_breach_rate == 1 / 92
    assert result.gate.status is GateStatus.PASS

    failing = [make_attempt(index, passed=index < 19) for index in range(92)]
    failing[19] = make_attempt(19, breach=True)
    failing[20] = make_attempt(20, breach=True)
    result = evaluate_v6_challenge_results(failing, config_id="h4d01")
    assert result.gate.status is GateStatus.FAIL


def test_challenge_overlay_is_not_invoked_before_core_gate_passes() -> None:
    index = pd.date_range("2016-12-30", "2023-01-02", freq="4h", tz="UTC")
    validation = evaluate_v6_candidate(
        [],
        spec=all_specs()[0],
        frame_index=index,
        trial_sharpes=[],
        all_configuration_fold_means=None,
        stressed_trades=None,
        perturbation_means=None,
        random_null=None,
    )
    blocked = run_v6_challenge_overlay(
        validation_report=validation,
        frame=pd.DataFrame(),
        trades=(),
        config=V6BacktestConfig(
            point_size=0.01,
            contract_size_ounces_per_lot=100.0,
        ),
        cost_model=V6CostModel(),
        swap_policy=None,
    )
    assert blocked.gate == GateResult(
        GateStatus.NOT_EVALUATED,
        "core gates did not all pass",
    )
    assert blocked.attempt_count == 0
