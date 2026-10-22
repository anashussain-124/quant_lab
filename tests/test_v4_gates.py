import numpy as np

from validation.gates import evaluate_candidate


def _kwargs():
    return dict(registry_trials=1, permutation_percentile=99,
                neighborhood=[0.1] * 10, stressed_trades=[0.1] * 300,
                folds=[{"mean_r": 0.1, "trade_count": 100, "profit": 10.0},
                       {"mean_r": 0.1, "trade_count": 100, "profit": 10.0},
                       {"mean_r": 0.1, "trade_count": 100, "profit": 10.0}],
                regimes={"vol_low": [0.1], "vol_mid": [0.1], "vol_high": [0.1], "2024": [0.1]})


def test_each_required_gate_can_pass():
    result = evaluate_candidate(np.ones(300), **_kwargs())
    assert result.verdict == "PASS-CANDIDATE"


def test_negative_expectancy_fails_first_gate():
    result = evaluate_candidate(-np.ones(300), **_kwargs())
    assert result.verdict == "FAIL"
    assert "net expectancy" in result.reasons[0]


def test_small_sample_is_not_pass_candidate():
    result = evaluate_candidate(np.ones(20), **_kwargs())
    assert result.verdict in {"FAIL", "INCONCLUSIVE"}
    assert any("below 300" in reason for reason in result.reasons)


def test_negative_net_cannot_pass_on_gross_permutation():
    result = evaluate_candidate(-np.ones(300), **_kwargs(),
                                gross_permutation_percentile=99,
                                net_permutation_percentile=99)
    assert result.verdict == "FAIL"
    assert result.gross_permutation_percentile == 99
    assert result.net_permutation_percentile == 99


def test_positive_candidate_must_beat_null_baseline():
    result = evaluate_candidate(np.ones(300), **_kwargs(),
                                net_permutation_percentile=99,
                                null_mean_net=0.0, null_p95_net=1.1)
    assert result.verdict != "PASS-CANDIDATE"
    assert any("null p95" in reason for reason in result.reasons)
