from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from execution.causal_sim import CostConfig, simulate_next_open
from execution.fast_sim import simulate_next_open_fast


REAL_MT5 = Path("data/store/v4/mt5_XAUUSD_M5_a052314f19605be8.parquet")


def _assert_same(a, b):
    assert a == b


@pytest.mark.parametrize("start", [0, 10_000, 100_000])
def test_fast_sim_matches_reference_on_real_windows(start):
    if not REAL_MT5.exists():
        pytest.skip("saved MT5 real-data fixture is unavailable")
    bars = pd.read_parquet(REAL_MT5).sort_values("timestamp").set_index("timestamp")
    bars = bars.iloc[start:start + 2_000]
    signals = {i: 1 if i % 37 else -1 for i in range(50, len(bars) - 1, 113)}
    costs = CostConfig(Decimal("1.25"), Decimal("0.4"), Decimal("0.15"))
    expected = simulate_next_open(bars, signals, stop_distance=Decimal("1.0"),
                                  target_distance=Decimal("1.5"), max_hold=24, costs=costs)
    actual = simulate_next_open_fast(bars, signals, stop_distance=Decimal("1.0"),
                                     target_distance=Decimal("1.5"), max_hold=24, costs=costs)
    _assert_same(actual, expected)
