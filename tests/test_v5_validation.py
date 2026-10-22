"""Statistical-gate tests for the v5 validation module."""

from __future__ import annotations

import numpy as np
import pytest

from research.strategies_v5 import spec_by_id
from research.validation_v5 import (
    bootstrap_mean_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    evaluate_core_gates,
    perturb_spec,
)


def test_bootstrap_ci_excludes_zero_for_clear_positive_edge():
    rng = np.random.default_rng(1)
    values = 0.2 + rng.normal(0, 0.5, 400)
    low, high = bootstrap_mean_ci(values, resamples=2000, seed=1)
    assert low > 0
    assert high > low


def test_bootstrap_ci_spans_zero_for_noise():
    rng = np.random.default_rng(2)
    values = rng.normal(0, 1.0, 400)
    low, high = bootstrap_mean_ci(values, resamples=2000, seed=2)
    assert low < 0 < high


def test_deflated_sharpe_rewards_strength_and_penalizes_trials():
    rng = np.random.default_rng(3)
    trial_sharpes = rng.normal(0, 0.04, 50)
    strong = rng.normal(0.15, 1.0, 400)
    weak = rng.normal(0.02, 1.0, 400)
    strong_dsr = deflated_sharpe_ratio(strong, trial_sharpes)
    weak_dsr = deflated_sharpe_ratio(weak, trial_sharpes)
    assert strong_dsr > weak_dsr
    many = deflated_sharpe_ratio(strong, rng.normal(0, 0.04, 500))
    assert many < strong_dsr


def test_cscv_pbo_zero_when_one_config_dominates_every_fold():
    matrix = np.zeros((6, 10))
    matrix[:, 0] = 0.5
    result = cscv_pbo(matrix)
    assert result["pbo"] == 0.0
    assert result["splits"] == 20


def test_cscv_pbo_rejects_bad_shape():
    with pytest.raises(ValueError):
        cscv_pbo(np.zeros((5, 10)))


def test_perturb_spec_covers_numeric_parameters_and_skips_zero_buffer():
    variants = perturb_spec(spec_by_id("dc01"))
    keys = {label.split("_")[0] for label, _ in variants}
    assert keys == {"n", "stop", "trail"}
    assert len(variants) == 6
    assert all(variant.config_id == "dc01" for _, variant in variants)
    assert any(variant.params["n"] == 40 for _, variant in variants)
    assert any(variant.params["n"] == 60 for _, variant in variants)


def test_perturb_spec_wraps_session_hour():
    variants = perturb_spec(spec_by_id("ar02"))
    hours = sorted(variant.params["exit_hour"] for label, variant in variants if "exit_hour" in label)
    assert hours == [0, 16]


def test_core_gates_require_all_conditions():
    oos = {
        "n": 200, "mean_net_r": 0.1, "ci_low": 0.02, "max_drawdown_r": 10.0,
        "yearly": {2019: {"total_net_r": 1.0}, 2020: {"total_net_r": 2.0},
                   2021: {"total_net_r": -1.0}, 2022: {"total_net_r": 3.0}},
        "atr_tercile_positive_share": [0.4, 0.3, 0.3],
    }
    folds = {year: {"total_net_r": 1.0} for year in range(2017, 2023)}
    gates = evaluate_core_gates(
        oos, folds, dsr=0.99, pbo=0.1,
        stress={"mean_net_r": 0.05},
        perturbation={"passed": True},
        null={"passed": True},
    )
    assert gates["all"] is True
    broken = evaluate_core_gates(
        oos, folds, dsr=0.99, pbo=0.1,
        stress={"mean_net_r": -0.01},
        perturbation={"passed": True},
        null={"passed": True},
    )
    assert broken["all"] is False
    assert broken["g6_cost_stress"] is False
