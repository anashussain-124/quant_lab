import numpy as np

from validation.core import shifted_signal_percentile, t_statistic


def test_t_statistic_marks_small_samples_insufficient():
    result = t_statistic([1.0, 2.0, 3.0])
    assert result.n == 3
    assert result.insufficient_sample is True
    assert result.t_stat > 0


def test_shifted_signal_percentile_is_reproducible():
    signals = np.array([1.0, 0.0, 0.0, 0.0])
    outcomes = np.array([2.0, -1.0, -1.0, -1.0])
    assert shifted_signal_percentile(signals, outcomes, iterations=100, seed=7) == 100.0
