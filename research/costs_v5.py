"""Causal cost model for the v5 swing research harness."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CostModel:
    point: float = 0.01
    contract_size: float = 100.0
    commission_round_turn: float = 7.0
    slippage_points: float = 0.5
    spread_floor_points: float = 10.0
    spread_median_window: int = 100
    swap_long_points: float = -12.6
    swap_short_points: float = -4.6
    swap_rollover_hour_utc: int = 21
    stress: float = 1.0

    @property
    def commission_side_price(self) -> float:
        return (self.commission_round_turn / 2.0) / self.contract_size * self.stress

    @property
    def slippage_price(self) -> float:
        return self.slippage_points * self.point * self.stress

    def swap_price_per_oz(self, direction: int, weekday: int) -> float:
        points = self.swap_long_points if direction > 0 else self.swap_short_points
        multiplier = 3.0 if weekday == 2 else 1.0
        return points * self.point * self.stress * multiplier


def effective_spread_points(frame: pd.DataFrame, model: CostModel) -> np.ndarray:
    """Observed spread, falling back to the causal median of past non-zero spreads."""
    raw = frame["spread"].to_numpy(dtype=float)
    out = np.empty(len(raw), dtype=float)
    history: deque[float] = deque(maxlen=model.spread_median_window)
    floor = model.spread_floor_points
    for i, value in enumerate(raw):
        if value > 0:
            effective = max(value, floor)
            history.append(float(value))
        else:
            fallback = median(history) if history else floor
            effective = max(fallback, floor)
        out[i] = effective
    return out
