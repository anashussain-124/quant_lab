"""Bounded, pre-registered hypothesis templates.

The registry describes research candidates; it does not claim that any
candidate has positive expectancy or authorize execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Hypothesis:
    name: str
    rationale: str
    parameter_ranges: Mapping[str, tuple[float, ...]]
    min_history: int
    max_hold: int
    stop_distance: float
    target_distance: float
    time_exit: int

    def __post_init__(self) -> None:
        if not self.name or self.min_history < 1 or self.max_hold < 1:
            raise ValueError("hypothesis identity and horizons must be positive")
        if self.stop_distance <= 0 or self.target_distance <= 0 or self.time_exit < 1:
            raise ValueError("exit distances and time exit must be positive")
        object.__setattr__(self, "parameter_ranges", MappingProxyType(dict(self.parameter_ranges)))


HYPOTHESES: tuple[Hypothesis, ...] = (
    Hypothesis("opening_range_breakout", "Breakout of the first session range.",
               {"range_minutes": (15.0, 30.0), "buffer_r": (0.0, 0.1)}, 60, 24, 1.0, 1.5, 24),
    Hypothesis("prior_day_sweep", "Reversal after a prior-day high or low sweep.",
               {"lookback_days": (1.0, 2.0), "reclaim_bars": (1.0, 3.0)}, 200, 48, 1.0, 1.5, 48),
    Hypothesis("volatility_trend", "Trend continuation conditioned on causal volatility.",
               {"ema_fast": (8.0, 21.0), "ema_slow": (50.0, 100.0)}, 150, 72, 1.0, 2.0, 72),
    Hypothesis("zscore_mean_reversion", "Reversion from an extreme rolling z-score.",
               {"window": (20.0, 50.0), "entry_z": (1.5, 2.0)}, 100, 24, 1.0, 1.0, 24),
    Hypothesis("session_drift", "Conditional drift during a fixed UTC session.",
               {"start_hour_utc": (7.0, 8.0), "end_hour_utc": (12.0, 13.0)}, 100, 24, 1.0, 1.0, 24),
)


def hypothesis_registry() -> tuple[Hypothesis, ...]:
    """Return the immutable bounded hypothesis set."""
    return HYPOTHESES
