import pandas as pd
import pytest

from features.causal import causal_ema, causal_zscore, prior_donchian_high
from hypotheses.registry import hypothesis_registry


def _series(n: int = 20) -> pd.Series:
    index = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.Series(range(n), index=index, dtype=float)


def test_prior_breakout_level_is_causal_and_prefix_stable():
    values = _series()
    full = prior_donchian_high(values, 3)
    prefix = prior_donchian_high(values.iloc[:12], 3)
    pd.testing.assert_series_equal(full.iloc[:12], prefix)


def test_ema_and_zscore_are_prefix_stable():
    values = _series()
    pd.testing.assert_series_equal(causal_ema(values, 4).iloc[:12], causal_ema(values.iloc[:12], 4))
    pd.testing.assert_series_equal(causal_zscore(values, 4).iloc[:12], causal_zscore(values.iloc[:12], 4))


def test_features_require_timezone_aware_index():
    with pytest.raises(ValueError, match="timezone-aware"):
        causal_ema(pd.Series([1.0, 2.0]), 2)


def test_registry_is_bounded_and_has_required_templates():
    names = {item.name for item in hypothesis_registry()}
    assert len(names) == 5
    assert {"opening_range_breakout", "prior_day_sweep", "volatility_trend",
            "zscore_mean_reversion", "session_drift"} == names
