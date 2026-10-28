"""Synthetic contract tests for the frozen v6 strategy definitions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.indicators_v5 import rolling_percentile_rank, wilder_atr
from research.strategies_v5 import ATR_PERIOD
from research.strategies_v6 import (
    VOL_LOOKBACK,
    all_specs,
    build_plan_v6,
)


def synthetic_h4_frame(
    bars: int = 500, *, seed: int = 17, start: str = "2018-01-01"
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 1800.0 + np.cumsum(rng.normal(0.0, 2.0, bars))
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.0, bars)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.0, bars)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "spread": np.full(bars, 24.0),
        },
        index=pd.date_range(start, periods=bars, freq="4h", tz="UTC"),
    )


def test_v6_specs_exactly_match_frozen_24_configuration_grid() -> None:
    grid = [
        (lookback, stop, trail)
        for lookback in (20, 50, 100)
        for stop in (1.5, 2.5)
        for trail in (2.0, 3.5)
    ]
    expected = {
        f"h4d{i:02d}": {
            "n": n,
            "stop_atr": stop,
            "trail_atr": trail,
            "vol_filter": None,
        }
        for i, (n, stop, trail) in enumerate(grid, start=1)
    }
    expected.update(
        {
            f"h4v{i:02d}": {
                "n": n,
                "stop_atr": stop,
                "trail_atr": trail,
                "vol_filter": 0.5,
            }
            for i, (n, stop, trail) in enumerate(grid, start=1)
        }
    )
    specs = all_specs()

    assert len(specs) == 24
    assert len({spec.config_id for spec in specs}) == 24
    assert {spec.config_id: spec.params for spec in specs} == expected


def test_filtered_warmup_matches_first_causal_percentile_rank() -> None:
    frame = synthetic_h4_frame()
    close = frame["close"].to_numpy(dtype=float)
    atr = wilder_atr(
        frame["high"].to_numpy(dtype=float),
        frame["low"].to_numpy(dtype=float),
        close,
        ATR_PERIOD,
    )
    rank = rolling_percentile_rank(atr / close, VOL_LOOKBACK)
    first_valid_rank = int(np.flatnonzero(np.isfinite(rank))[0])

    filtered = next(spec for spec in all_specs() if spec.config_id == "h4v01")
    plan = build_plan_v6(frame, filtered)

    assert first_valid_rank == ATR_PERIOD - 1 + VOL_LOOKBACK
    assert plan.warmup == first_valid_rank
    assert not plan.long_signal[: plan.warmup].any()
    assert not plan.short_signal[: plan.warmup].any()


def test_all_v6_signal_plans_are_invariant_to_future_truncation_and_shocks() -> None:
    frame = synthetic_h4_frame()
    cutoff = 360
    check_end = 320
    truncated = frame.iloc[:cutoff]
    shocked = frame.copy()
    shocked.loc[shocked.index[cutoff:], ["open", "high", "low", "close"]] *= 1.4

    for spec in all_specs():
        baseline = build_plan_v6(frame, spec)
        truncated_plan = build_plan_v6(truncated, spec)
        shocked_plan = build_plan_v6(shocked, spec)

        for name in ("long_signal", "short_signal"):
            np.testing.assert_array_equal(
                getattr(baseline, name)[:check_end],
                getattr(truncated_plan, name)[:check_end],
                err_msg=f"{spec.config_id} {name} changed after future truncation",
            )
            np.testing.assert_array_equal(
                getattr(baseline, name)[:check_end],
                getattr(shocked_plan, name)[:check_end],
                err_msg=f"{spec.config_id} {name} changed after future shock",
            )
        np.testing.assert_allclose(
            baseline.atr[:check_end],
            truncated_plan.atr[:check_end],
            equal_nan=True,
            err_msg=f"{spec.config_id} ATR changed after future truncation",
        )


def test_v6_plan_rejects_naive_and_non_utc_timestamps() -> None:
    frame = synthetic_h4_frame()
    spec = all_specs()[0]

    naive = frame.copy()
    naive.index = pd.DatetimeIndex(naive.index).tz_localize(None)
    with pytest.raises(ValueError, match="UTC-aware"):
        build_plan_v6(naive, spec)

    non_utc = frame.copy()
    non_utc.index = pd.DatetimeIndex(non_utc.index).tz_convert("America/New_York")
    with pytest.raises(ValueError, match="UTC-aware"):
        build_plan_v6(non_utc, spec)
