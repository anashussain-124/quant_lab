"""Synthetic tests for frozen H4 v6 fold and null orchestration."""

from __future__ import annotations

from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from research.backtest_v6 import (
    V6BacktestConfig,
    V6BacktestResult,
    V6Trade,
    _build_market,
    _simulate_v6_trade,
)
from research.challenge_overlay_v6 import _to_challenge_bars, _to_trade_paths
from research.constants_v6 import V6_NULL_SIMULATIONS, V6Environment
from research.costs_v6 import V6CostModel
from research.random_null_v6 import (
    V6NullProfile,
    _generate_v6_random_entry_null,
    verify_v6_null_match,
)
from research.strategies_v6 import all_specs
from research.validation_pipeline_v6 import (
    build_v6_dsr_trial_sharpes,
    build_v6_fold_windows,
    build_v6_pbo_fold_matrix,
    evaluate_v6_candidate,
    summarize_v6_folds,
    summarize_v6_perturbation_runs,
)
from research.validation_v6 import EvaluationStatus, GateStatus, evaluate_v6_pbo


class SyntheticNoSwapPolicy:
    def credit_price_per_ounce(
        self,
        *,
        direction: int,
        entry_time: pd.Timestamp,
        exit_time: pd.Timestamp,
        stress_multiplier: float,
    ) -> float:
        del direction, entry_time, exit_time, stress_multiplier
        return 0.0


def make_trade(
    timestamp: pd.Timestamp,
    *,
    config_id: str = "h4d01",
    net_r: float = 0.1,
    direction: int = 1,
    index: int = 1,
    atr: float = 2.0,
    stop_distance: float = 3.0,
    bars_held: int = 1,
) -> V6Trade:
    gross = net_r
    return V6Trade(
        environment=V6Environment.BACKTEST,
        config_id=config_id,
        direction=direction,
        signal_index=index - 1,
        entry_index=index,
        exit_index=index + bars_held,
        max_hold_bars=bars_held,
        signal_time=timestamp - pd.Timedelta(hours=4),
        entry_time=timestamp,
        exit_time=timestamp + pd.Timedelta(hours=4 * bars_held),
        entry_price=100.0,
        exit_price=100.0 + direction * gross * stop_distance,
        stop_distance=stop_distance,
        atr_entry=atr,
        volume_lots=0.01,
        risk_usd=3.0,
        gross_r=gross,
        spread_slippage_r=0.0,
        commission_r=0.0,
        swap_r=0.0,
        net_r=net_r,
        cost_stress_multiplier=1.0,
        gross_pnl_usd=gross * 3.0,
        spread_slippage_usd=0.0,
        commission_usd=0.0,
        swap_usd=0.0,
        net_pnl_usd=gross * 3.0,
        equity_after_usd=5_000.0 + gross * 3.0,
        reason="synthetic",
        bars_held=bars_held,
        open_fill=True,
    )


def synthetic_frame(length: int = 1_000) -> pd.DataFrame:
    index = pd.date_range("2019-01-01", periods=length, freq="4h", tz="UTC")
    price = np.full(length, 100.0)
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "spread": np.full(length, 10.0),
        },
        index=index,
    )


def test_fold_windows_use_six_calendar_years_and_skip_first_24_bars() -> None:
    index = pd.date_range("2016-12-30", "2023-01-02", freq="4h", tz="UTC")
    windows = build_v6_fold_windows(index)
    assert [window.year for window in windows] == list(range(2017, 2023))
    assert all(
        window.first_eligible_entry_index == window.first_index + 24
        for window in windows
    )

    first_fold = windows[0]
    excluded = make_trade(
        index[first_fold.first_index + 23],
        index=first_fold.first_index + 23,
    )
    included = make_trade(
        index[first_fold.first_index + 24],
        index=first_fold.first_index + 24,
    )
    summaries = summarize_v6_folds([excluded, included], windows)
    assert summaries[0].trade_count == 1
    assert summaries[0].total_net_r == pytest.approx(0.1)
    assert all(summary.trade_count == 0 for summary in summaries[1:])


def test_validation_orchestrator_never_passes_gates_with_missing_evidence() -> None:
    index = pd.date_range("2016-12-30", "2023-01-02", freq="4h", tz="UTC")
    windows = build_v6_fold_windows(index)
    eligible_index = windows[2].first_eligible_entry_index
    trade = make_trade(
        index[eligible_index],
        index=eligible_index,
    )
    report = evaluate_v6_candidate(
        [trade],
        spec=all_specs()[0],
        frame_index=index,
        trial_sharpes=[None] * 74,
        all_configuration_fold_means=None,
        stressed_trades=None,
        perturbation_means=None,
        random_null=None,
    )

    assert report.dsr.status is EvaluationStatus.NOT_EVALUABLE
    assert report.pbo.status is EvaluationStatus.NOT_EVALUABLE
    assert report.core_gates.gates["g6_cost_stress"].status is GateStatus.NOT_EVALUABLE
    assert report.core_gates.gates["g7_perturbation"].status is GateStatus.NOT_EVALUABLE
    assert report.core_gates.gates["g9_null"].status is GateStatus.NOT_EVALUATED
    assert report.core_gates.overall is not GateStatus.PASS


def test_dsr_uses_only_validation_sharpes_and_retains_all_74_trials() -> None:
    specs = all_specs()
    oos_trades = (
        make_trade(pd.Timestamp("2019-01-01", tz=UTC), net_r=0.1),
        make_trade(pd.Timestamp("2019-01-02", tz=UTC), net_r=-0.2),
    )
    development_trade = make_trade(
        pd.Timestamp("2018-01-01", tz=UTC),
        net_r=100.0,
    )
    results = tuple(
        V6BacktestResult(
            V6Environment.BACKTEST,
            spec.config_id,
            1.0,
            oos_trades + (development_trade,) if index == 0 else (),
            (),
            (),
        )
        for index, spec in enumerate(specs)
    )
    sharpes = build_v6_dsr_trial_sharpes([0.2] * 50, results)
    expected = float(np.mean([0.1, -0.2]) / np.std([0.1, -0.2], ddof=1))
    assert len(sharpes) == 74
    assert sharpes[50] == pytest.approx(expected)
    assert sharpes[50] is not None
    assert all(value is None for value in sharpes[51:])


def test_fold_matrix_and_perturbation_builders_fail_closed_on_missing_results() -> None:
    index = pd.date_range("2016-12-30", "2023-01-02", freq="4h", tz="UTC")
    assert build_v6_pbo_fold_matrix([], index) is None

    results = tuple(
        V6BacktestResult(
            V6Environment.BACKTEST,
            spec.config_id,
            1.0,
            (),
            (),
            (),
        )
        for spec in all_specs()
    )
    matrix = build_v6_pbo_fold_matrix(results, index)
    assert matrix is not None
    assert matrix.shape == (6, 24)
    assert np.isnan(matrix).all()
    assert (
        evaluate_v6_pbo(matrix, environment=V6Environment.BACKTEST).status
        is EvaluationStatus.NOT_EVALUABLE
    )
    assert summarize_v6_perturbation_runs([], spec=all_specs()[0]) is None


def test_null_match_requires_trade_count_direction_stops_and_realized_holds() -> None:
    candidate = (
        V6NullProfile(1, 1.5, 2),
        V6NullProfile(-1, 2.5, 3),
    )
    matched = [
        make_trade(
            pd.Timestamp("2019-01-01", tz=UTC),
            direction=-1,
            stop_distance=5.0,
            atr=2.0,
            bars_held=3,
        ),
        make_trade(
            pd.Timestamp("2019-01-02", tz=UTC),
            direction=1,
            stop_distance=3.0,
            atr=2.0,
            bars_held=2,
        ),
    ]
    result = verify_v6_null_match(candidate, matched)
    assert result.passed

    wrong_holding = [matched[0], make_trade(
        pd.Timestamp("2019-01-02", tz=UTC),
        direction=1,
        stop_distance=3.0,
        atr=2.0,
        bars_held=1,
    )]
    mismatch = verify_v6_null_match(candidate, wrong_holding)
    assert mismatch.trade_count
    assert mismatch.direction_mix
    assert mismatch.stop_distance_distribution
    assert not mismatch.holding_period


def test_random_null_generator_runs_exact_fixed_seed_count_on_synthetic_data() -> None:
    frame = synthetic_frame()
    index = frame.index
    config = V6BacktestConfig(
        point_size=0.01,
        contract_size_ounces_per_lot=100.0,
    )
    spec = all_specs()[0]
    atr = np.full(len(frame), 2.0)
    market = _build_market(frame, atr, config)
    candidate = _simulate_v6_trade(
        market,
        signal_index=30,
        entry_index=31,
        direction=1,
        stop_distance=3.0,
        trail_atr=2.0,
        max_hold=2,
        allow_matched_null_horizon=True,
        equity_usd=config.starting_balance_usd,
        spec_config_id=spec.config_id,
        config=config,
        cost_model=V6CostModel(),
        swap_policy=SyntheticNoSwapPolicy(),
    )
    assert candidate.entry_time == index[31]
    challenge_path = _to_trade_paths([candidate], config=config)[0]
    assert challenge_path.cost_r == pytest.approx(
        candidate.spread_slippage_r + 7.0 / (100.0 * candidate.stop_distance)
    )
    challenge_net_r = (
        candidate.gross_r
        - challenge_path.cost_r
        + candidate.swap_r
        - 3.0 / (100.0 * candidate.stop_distance)
    )
    assert challenge_net_r == pytest.approx(candidate.net_r)
    challenge_bars = _to_challenge_bars(frame, config=config)
    assert np.allclose(challenge_bars.spread_price, 0.1)

    result = _generate_v6_random_entry_null(
        frame,
        spec=spec,
        candidate_trades=[candidate],
        config=config,
        cost_model=V6CostModel(),
        swap_policy=SyntheticNoSwapPolicy(),
    )
    assert result.environment is V6Environment.BACKTEST
    assert result.simulation_count == V6_NULL_SIMULATIONS == 500
    assert result.valid_simulation_count == V6_NULL_SIMULATIONS
    assert len(result.simulation_means_net_r) == V6_NULL_SIMULATIONS
    assert all(value is not None and np.isfinite(value) for value in result.simulation_means_net_r)
    assert result.trade_count_matched
    assert result.direction_mix_matched
    assert result.stop_distance_distribution_matched
    assert result.holding_period_matched
