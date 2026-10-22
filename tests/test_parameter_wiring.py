from pathlib import Path

import pandas as pd
import pytest

from hypotheses.generators import GENERATORS
from hypotheses.registry import hypothesis_registry


REAL_MT5 = Path("data/store/v4/mt5_XAUUSD_M5_a052314f19605be8.parquet")


def _real_frame() -> pd.DataFrame:
    if not REAL_MT5.exists():
        pytest.skip("saved MT5 real-data fixture is unavailable")
    frame = pd.read_parquet(REAL_MT5).sort_values("timestamp").set_index("timestamp")
    return frame.iloc[:5000]


@pytest.mark.parametrize("spec", hypothesis_registry(), ids=lambda item: item.name)
def test_each_hypothesis_parameter_set_changes_real_signals(spec):
    frame = _real_frame()
    keys = list(spec.parameter_ranges)
    first = {key: float(values[0]) for key, values in spec.parameter_ranges.items()}
    second = {key: float(values[-1]) for key, values in spec.parameter_ranges.items()}
    plan_a = GENERATORS[spec.name](frame, first)
    plan_b = GENERATORS[spec.name](frame, second)
    assert plan_a.signals != plan_b.signals, (
        f"{spec.name} parameters {keys} do not change real signals"
    )
