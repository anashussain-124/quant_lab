"""Synthetic tests for the fail-closed H4 v6 research infrastructure."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.constants_v6 import (
    V6_COST_MODEL_VERSION,
    V6_DATASET_ID,
    V6_DATASET_SHA256,
    V6_GATE_IDS,
    V6_NULL_SIMULATIONS,
    V6_PROTOCOL_SHA256,
    V6Environment,
)
from research.costs_v6 import V6CostModel, V6CostSemanticsBlockedError
from research.registry_v6 import (
    TrialStatus,
    V6Provenance,
    V6Registry,
    V6RegistryError,
    V6TrialRecord,
    validate_v6_provenance,
)
from research.strategies_v6 import MAX_HOLD_H4, all_specs
from research.terminal_policy_v6 import assess_terminal_entry
from research.v6_runner import V6Runner, main
from research.v6_safety import (
    V6DatasetPathError,
    V6RunBlockedError,
    require_authorized_v6_dataset_path,
)
from research.validation_v6 import (
    DsrEvaluation,
    EvaluationStatus,
    GateEvidence,
    GateStatus,
    PboEvaluation,
    RandomNullEvidence,
    TradeOutcome,
    bootstrap_mean_ci_v6,
    build_v6_perturbations,
    evaluate_v6_core_gates,
    evaluate_v6_dsr,
    evaluate_v6_pbo,
    evaluate_v6_perturbation,
    summarize_trade_outcomes,
)


def valid_provenance() -> V6Provenance:
    return V6Provenance(
        code_commit="3dd25e005a2f5bd1429d5fe6e87a453e73c4f9f9-dirty",
        dataset_id=V6_DATASET_ID,
        dataset_sha256=V6_DATASET_SHA256,
        dataset_start_utc="2012-01-03T00:00:00+00:00",
        dataset_end_utc="2022-12-30T20:00:00+00:00",
        protocol_sha256=V6_PROTOCOL_SHA256,
        cost_model_version=V6_COST_MODEL_VERSION,
        random_seed=0,
        engine_version="synthetic-test-engine",
    )


def valid_record(
    *,
    run_id: str = "v6-synthetic-001",
    status: TrialStatus = TrialStatus.COMPLETED,
    outcome: dict[str, object] | None = None,
    failure_reason: str | None = None,
) -> V6TrialRecord:
    spec = all_specs()[0]
    return V6TrialRecord(
        run_id=run_id,
        created_at_utc=datetime(2026, 10, 26, 0, 0, tzinfo=UTC),
        environment=V6Environment.SIMULATED,
        strategy_name="h4_donchian",
        config_id=spec.config_id,
        parameters=dict(spec.params),
        command="synthetic-only test",
        status=status,
        provenance=valid_provenance(),
        gate_results=dict.fromkeys(V6_GATE_IDS, "NOT EVALUATED"),
        outcome={"synthetic_trades": 3} if outcome is None else outcome,
        failure_reason=failure_reason,
    )


def passing_gate_evidence() -> GateEvidence:
    return GateEvidence(
        trade_count=100,
        mean_net_r=0.2,
        bootstrap_ci_lower=0.01,
        positive_fold_count=4,
        dsr=DsrEvaluation(
            environment=V6Environment.SIMULATED,
            status=EvaluationStatus.EVALUATED,
            value=0.96,
            required_trial_count=74,
            supplied_trial_count=74,
            reason=None,
        ),
        pbo=PboEvaluation(
            environment=V6Environment.SIMULATED,
            status=EvaluationStatus.EVALUATED,
            value=0.25,
            fold_count=6,
            configuration_count=24,
            split_count=20,
            reason=None,
        ),
        stressed_mean_net_r=0.05,
        perturbation_passed=True,
        atr_tercile_shares=(0.3, 0.3, 0.4),
        positive_year_count=3,
        max_drawdown_r=10.0,
    )


def test_cost_model_scales_declared_components_and_blocks_swap() -> None:
    model = V6CostModel()
    base = model.non_swap_cost(
        spread_entry_points=10,
        spread_exit_points=20,
        point_size=0.01,
        contract_size_ounces_per_lot=100,
    )
    stressed = model.stressed().non_swap_cost(
        spread_entry_points=10,
        spread_exit_points=20,
        point_size=0.01,
        contract_size_ounces_per_lot=100,
    )

    assert model.version == V6_COST_MODEL_VERSION
    assert model.broker_specification_status == "UNVERIFIED"
    assert model.swap_policy_status == "UNRESOLVED"
    assert base.spread_price_per_ounce == pytest.approx(0.15)
    assert base.slippage_price_per_ounce == pytest.approx(0.01)
    assert base.commission_price_per_ounce == pytest.approx(0.1)
    assert stressed.spread_price_per_ounce == pytest.approx(
        base.spread_price_per_ounce * 1.5
    )
    assert stressed.slippage_price_per_ounce == pytest.approx(
        base.slippage_price_per_ounce * 1.5
    )
    assert stressed.commission_price_per_ounce == pytest.approx(
        base.commission_price_per_ounce * 1.5
    )
    with pytest.raises(V6CostSemanticsBlockedError, match="unresolved"):
        model.swap_cost(direction=1, rollovers=2)


def test_cost_model_rejects_invalid_inputs_and_unapproved_stress() -> None:
    with pytest.raises(ValueError, match="only the base"):
        V6CostModel(stress_multiplier=2.0)
    with pytest.raises(ValueError, match="finite"):
        V6CostModel(slippage_points_per_side=float("nan"))
    with pytest.raises(ValueError, match="frozen at USD 10"):
        V6CostModel(commission_round_turn_usd_per_lot=7.0)
    with pytest.raises(ValueError, match="frozen at 0.5"):
        V6CostModel(slippage_points_per_side=1.0)
    with pytest.raises(ValueError, match="positive"):
        V6CostModel().non_swap_cost(
            spread_entry_points=1,
            spread_exit_points=1,
            point_size=0,
            contract_size_ounces_per_lot=100,
        )


def test_dataset_allowlist_accepts_only_synthetic_frozen_relative_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "synthetic-project"
    root.mkdir()
    approved = require_authorized_v6_dataset_path(
        root, "data/store/v6/xauusd_h4_dev_validation.parquet"
    )
    assert approved == (
        root / "data" / "store" / "v6" / "xauusd_h4_dev_validation.parquet"
    ).resolve()

    forbidden_paths = (
        "data/store/v6/sealed_holdout.parquet",
        "../sealed_holdout.parquet",
        str(tmp_path / "outside" / "data.parquet"),
    )
    for path in forbidden_paths:
        with pytest.raises(V6DatasetPathError, match="dataset path"):
            require_authorized_v6_dataset_path(root, path)


def test_runner_blocks_before_dataset_or_provenance_validation(tmp_path: Path) -> None:
    runner = V6Runner(tmp_path)
    with pytest.raises(V6RunBlockedError, match="partial_h4_bin_policy"):
        runner.preflight(
            tmp_path / "synthetic-sealed-holdout.parquet",
            code_commit="test",
            random_seed=0,
        )


def test_v6_entrypoint_refuses_execution_without_reading_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 2
    captured = capsys.readouterr()
    assert "partial_h4_bin_policy" in captured.err
    assert "h4_rollover_stop_ordering" in captured.err


def test_provenance_requires_frozen_hashes_and_utc_ranges() -> None:
    validate_v6_provenance(valid_provenance())
    bad_hash = valid_provenance()
    bad_hash = V6Provenance(
        **{**bad_hash.__dict__, "dataset_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="frozen H4 artifact"):
        validate_v6_provenance(bad_hash)

    naive_range = V6Provenance(
        **{**valid_provenance().__dict__, "dataset_start_utc": "2012-01-03"}
    )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        validate_v6_provenance(naive_range)

    wrong_seed = V6Provenance(
        **{**valid_provenance().__dict__, "random_seed": 1}
    )
    with pytest.raises(ValueError, match="frozen seed 0"):
        validate_v6_provenance(wrong_seed)


def test_registry_appends_full_outcome_and_rejects_duplicate_run_id(
    tmp_path: Path,
) -> None:
    registry = V6Registry(tmp_path / "registry_v6.jsonl")
    record = valid_record(
        status=TrialStatus.FAILED,
        outcome={"partial_result": {"trade_count": 0}},
        failure_reason="synthetic injected failure",
    )
    registry.append(record)

    lines = registry.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["run_id"] == record.run_id
    assert saved["status"] == "FAILED"
    assert saved["failure_reason"] == "synthetic injected failure"
    assert saved["parameters"] == dict(record.parameters)
    assert saved["provenance"]["dataset_sha256"] == V6_DATASET_SHA256
    assert set(saved["gate_results"]) == set(V6_GATE_IDS)

    with pytest.raises(V6RegistryError, match="duplicate run_id"):
        registry.append(record)
    assert len(registry.path.read_text(encoding="utf-8").splitlines()) == 1
    assert not registry.lock_path.exists()


def test_registry_protects_v5_path_and_rejects_invalid_records(
    tmp_path: Path,
) -> None:
    with pytest.raises(V6RegistryError, match="registry_v6.jsonl"):
        V6Registry(tmp_path / "registry_v5.jsonl")

    registry = V6Registry(tmp_path / "registry_v6.jsonl")
    invalid_hash_record = valid_record(run_id="invalid-hash")
    bad_provenance = V6Provenance(
        **{
            **invalid_hash_record.provenance.__dict__,
            "protocol_sha256": "f" * 64,
        }
    )
    with pytest.raises(ValueError, match="frozen preregistration"):
        registry.append(
            V6TrialRecord(
                **{
                    **invalid_hash_record.__dict__,
                    "provenance": bad_provenance,
                }
            )
        )

    nan_record = valid_record(
        run_id="invalid-nan",
        outcome={"metric": float("nan")},
    )
    with pytest.raises(ValueError, match="finite JSON-compatible"):
        registry.append(nan_record)
    assert not registry.path.exists()


def test_registry_rejects_incomplete_failure_provenance_and_holdout_result_path(
    tmp_path: Path,
) -> None:
    registry = V6Registry(tmp_path / "registry_v6.jsonl")
    failed_without_reason = valid_record(
        run_id="failed-no-reason",
        status=TrialStatus.FAILED,
        failure_reason=None,
    )
    with pytest.raises(ValueError, match="failure_reason"):
        registry.append(failed_without_reason)

    with_holdout_path = V6TrialRecord(
        **{
            **valid_record(run_id="holdout-path").__dict__,
            "result_paths": ("reports/holdout/result.json",),
        }
    )
    with pytest.raises(ValueError, match="holdout"):
        registry.append(with_holdout_path)
    assert not registry.path.exists()


def test_metrics_define_empty_and_zero_loss_cases_and_reject_nonfinite() -> None:
    empty = summarize_trade_outcomes(
        [], environment=V6Environment.SIMULATED
    )
    assert empty.status is EvaluationStatus.NOT_EVALUABLE
    assert empty.trade_count == 0
    assert empty.expectancy_r is None
    assert empty.profit_factor_status == "NO_TRADES"

    zero_gross_loss = summarize_trade_outcomes(
        [
            TradeOutcome(
                V6Environment.SIMULATED,
                gross_r=1.0,
                cost_r=0.1,
                swap_r=0.0,
                bars_held=3,
            ),
            TradeOutcome(
                V6Environment.SIMULATED,
                gross_r=0.5,
                cost_r=0.1,
                swap_r=0.0,
                bars_held=2,
            ),
        ],
        environment=V6Environment.SIMULATED,
    )
    assert zero_gross_loss.gross_loss_r == 0.0
    assert zero_gross_loss.environment is V6Environment.SIMULATED
    assert zero_gross_loss.net_profit_r == pytest.approx(1.3)
    assert zero_gross_loss.profit_factor is None
    assert zero_gross_loss.profit_factor_status == "NO_NET_LOSSES"

    with pytest.raises(ValueError, match="finite"):
        summarize_trade_outcomes(
            [
                TradeOutcome(
                    V6Environment.SIMULATED,
                    gross_r=np.nan,
                    cost_r=0,
                    swap_r=0,
                    bars_held=1,
                )
            ],
            environment=V6Environment.SIMULATED,
        )


def test_dsr_requires_all_74_finite_trial_sharpes() -> None:
    returns = [0.1, -0.03, 0.04, 0.02, -0.01, 0.07]
    missing = evaluate_v6_dsr(
        returns, [0.1] * 73, environment=V6Environment.SIMULATED
    )
    assert missing.status is EvaluationStatus.NOT_EVALUABLE
    assert missing.required_trial_count == 74
    assert missing.supplied_trial_count == 73

    invalid = evaluate_v6_dsr(
        returns, [0.1] * 73 + [None], environment=V6Environment.SIMULATED
    )
    assert invalid.status is EvaluationStatus.NOT_EVALUABLE
    assert invalid.required_trial_count == 74

    nonfinite = evaluate_v6_dsr(
        returns, [0.1] * 73 + [np.nan], environment=V6Environment.SIMULATED
    )
    assert nonfinite.status is EvaluationStatus.NOT_EVALUABLE
    assert nonfinite.supplied_trial_count == 74

    valid = evaluate_v6_dsr(
        returns,
        np.linspace(-0.2, 0.3, 74).tolist(),
        environment=V6Environment.SIMULATED,
    )
    assert valid.supplied_trial_count == 74
    assert valid.status is EvaluationStatus.EVALUATED
    assert valid.value is not None


def test_bootstrap_and_pbo_require_frozen_settings_and_shapes() -> None:
    ci = bootstrap_mean_ci_v6(
        [0.1, 0.2, -0.05],
        environment=V6Environment.SIMULATED,
    )
    assert ci.environment is V6Environment.SIMULATED
    assert ci.status is EvaluationStatus.EVALUATED
    assert ci.lower is not None
    assert ci.upper is not None

    matrix = np.arange(144, dtype=float).reshape(6, 24)
    pbo = evaluate_v6_pbo(matrix, environment=V6Environment.SIMULATED)
    assert pbo.status is EvaluationStatus.EVALUATED
    assert (pbo.fold_count, pbo.configuration_count, pbo.split_count) == (6, 24, 20)

    incomplete = matrix.copy()
    incomplete[0, 0] = np.nan
    assert (
        evaluate_v6_pbo(incomplete, environment=V6Environment.SIMULATED).status
        is EvaluationStatus.NOT_EVALUABLE
    )
    with pytest.raises(ValueError, match="exactly six folds"):
        evaluate_v6_pbo(matrix[:5], environment=V6Environment.SIMULATED)


def test_perturbations_change_only_frozen_per_configuration_numeric_parameters() -> None:
    for spec in (all_specs()[0], all_specs()[12]):
        variants = build_v6_perturbations(spec)
        assert variants
        assert len(variants) == (6 if spec.params["vol_filter"] is None else 8)
        for variant in variants:
            changed = {
                key
                for key in spec.params
                if spec.params[key] != variant.parameters[key]
            }
            assert changed == {variant.parameter}
            assert variant.parameter in {
                "n",
                "stop_atr",
                "trail_atr",
                "vol_filter",
            }
            assert variant.base_config_id == spec.config_id
        assert MAX_HOLD_H4 == 120

    result = evaluate_v6_perturbation(
        1.0, [0.75, 0.8, 0.9], environment=V6Environment.SIMULATED
    )
    assert result.status is EvaluationStatus.PASS
    assert result.median_retention == pytest.approx(0.8)
    assert (
        evaluate_v6_perturbation(
            1.0, [0.8, None], environment=V6Environment.SIMULATED
        ).status
        is EvaluationStatus.NOT_EVALUABLE
    )


def test_gate_9_is_deferred_until_gates_1_through_8_and_10_pass() -> None:
    calls: list[str] = []

    def null_result() -> RandomNullEvidence:
        calls.append("called")
        return RandomNullEvidence(
            config_id="h4d01",
            environment=V6Environment.SIMULATED,
            simulation_count=V6_NULL_SIMULATIONS,
            seed=0,
            valid_simulation_count=V6_NULL_SIMULATIONS,
            simulation_means_net_r=(0.1,) * V6_NULL_SIMULATIONS,
            trade_count_matched=True,
            direction_mix_matched=True,
            stop_distance_distribution_matched=True,
            holding_period_matched=True,
        )

    report = evaluate_v6_core_gates(
        passing_gate_evidence(),
        environment=V6Environment.SIMULATED,
        random_entry_evaluator=null_result,
    )
    assert list(report.gates)[-2:] == ["g10_drawdown", "g9_null"]
    assert report.gates["g9_null"].status is GateStatus.PASS
    assert report.overall is GateStatus.PASS
    assert calls == ["called"]

    calls.clear()
    failed = evaluate_v6_core_gates(
        GateEvidence(
            **{**passing_gate_evidence().__dict__, "trade_count": 99}
        ),
        environment=V6Environment.SIMULATED,
        random_entry_evaluator=null_result,
    )
    assert failed.gates["g1_trades"].status is GateStatus.FAIL
    assert failed.gates["g9_null"].status is GateStatus.NOT_EVALUATED
    assert calls == []

    unmatched_null = RandomNullEvidence(
        config_id="h4d01",
        environment=V6Environment.SIMULATED,
        simulation_count=V6_NULL_SIMULATIONS,
        seed=0,
        valid_simulation_count=V6_NULL_SIMULATIONS,
        simulation_means_net_r=(0.1,) * V6_NULL_SIMULATIONS,
        trade_count_matched=True,
        direction_mix_matched=True,
        stop_distance_distribution_matched=False,
        holding_period_matched=True,
    )
    blocked_null = evaluate_v6_core_gates(
        passing_gate_evidence(),
        environment=V6Environment.SIMULATED,
        random_entry_evaluator=lambda: unmatched_null,
    )
    assert blocked_null.gates["g9_null"].status is GateStatus.NOT_EVALUABLE


def test_missing_dsr_blocks_gate_and_nonfinite_evidence_is_rejected() -> None:
    evidence = GateEvidence(
        **{
            **passing_gate_evidence().__dict__,
            "dsr": DsrEvaluation(
                environment=V6Environment.SIMULATED,
                status=EvaluationStatus.NOT_EVALUABLE,
                value=None,
                required_trial_count=74,
                supplied_trial_count=74,
                reason="synthetic missing Sharpe",
            ),
        }
    )
    report = evaluate_v6_core_gates(
        evidence, environment=V6Environment.SIMULATED
    )
    assert report.gates["g4_dsr"].status is GateStatus.NOT_EVALUABLE
    assert report.gates["g9_null"].status is GateStatus.NOT_EVALUATED
    assert report.overall is GateStatus.NOT_EVALUABLE

    with pytest.raises(ValueError, match="mean_net_r must be finite"):
        evaluate_v6_core_gates(
            GateEvidence(**{**passing_gate_evidence().__dict__, "mean_net_r": np.nan}),
            environment=V6Environment.SIMULATED,
        )


def test_terminal_entries_require_entry_and_full_120_bar_exit_open(
    tmp_path: Path,
) -> None:
    index = pd.date_range("2020-01-01", periods=123, freq="4h", tz="UTC")

    eligible = assess_terminal_entry(1, index)
    assert eligible.eligible is True
    assert eligible.entry_index == 2
    assert eligible.planned_exit_index == 2 + MAX_HOLD_H4
    assert eligible.skip_reason is None

    terminal = assess_terminal_entry(2, index)
    assert terminal.eligible is False
    assert terminal.skip_reason == "full_120_bar_next_open_exit_unavailable"
    assert terminal.signal_time == index[2]
    skip_record = terminal.as_skip_record()
    assert skip_record == {
        "signal_index": 2,
        "signal_time_utc": index[2].isoformat(),
        "reason": "full_120_bar_next_open_exit_unavailable",
    }
    registry = V6Registry(tmp_path / "registry_v6.jsonl")
    registry.append(
        valid_record(
            run_id="terminal-signal-skip",
            outcome={"skipped_signals": [skip_record]},
        )
    )
    saved = json.loads(registry.path.read_text(encoding="utf-8"))
    assert saved["outcome"]["skipped_signals"][0]["reason"] == skip_record["reason"]

    no_entry_open = assess_terminal_entry(len(index) - 1, index)
    assert no_entry_open.eligible is False
    assert no_entry_open.skip_reason == "next_bar_entry_open_unavailable"
