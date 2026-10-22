import numpy as np

from validation.gates import evaluate_candidate
from validation.power import minimum_detectable_mean_r


def _synthetic_pass(values):
    return evaluate_candidate(
        values,
        [{"mean_r": float(np.mean(chunk)), "trade_count": len(chunk), "profit": float(np.sum(chunk))}
         for chunk in np.array_split(values, 6)],
        registry_trials=1, gross_permutation_percentile=99,
        net_permutation_percentile=99, null_mean_net=0.0, null_p95_net=0.02,
        neighborhood=[0.1] * 10, stressed_trades=np.full(len(values), 0.1),
        regimes={"vol_low": [0.1], "vol_mid": [0.1], "vol_high": [0.1],
                 "year_2024": [0.1]},
    )


def test_synthetic_control_planted_edge_passes_full_gate_stack():
    rng = np.random.default_rng(44)
    # SYNTHETIC CONTROL: trigger-aligned returns with a planted positive bias.
    values = rng.normal(0.15, 0.08, 600)
    result = _synthetic_pass(values)
    assert result.verdict == "PASS-CANDIDATE"


def test_synthetic_control_pure_noise_has_no_false_positive():
    rng = np.random.default_rng(45)
    false_positives = 0
    for _ in range(200):
        # SYNTHETIC CONTROL: zero-edge random walk trade returns.
        result = _synthetic_pass(rng.normal(0.0, 1.0, 600))
        false_positives += result.verdict == "PASS-CANDIDATE"
    assert false_positives == 0


def test_minimum_detectable_mean_r_unit_volatility():
    assert np.isclose(minimum_detectable_mean_r(300), 0.1732050807)
    assert np.isclose(minimum_detectable_mean_r(1000), 0.0948683298)
    assert np.isclose(minimum_detectable_mean_r(3000), 0.0547722557)
