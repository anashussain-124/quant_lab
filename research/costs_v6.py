"""Versioned v6 cost assumptions; H4 swap accrual remains intentionally blocked."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from research.constants_v6 import V6_COST_MODEL_VERSION


class V6CostSemanticsBlockedError(RuntimeError):
    """A complete cost cannot be calculated under unresolved H4 swap semantics."""


@dataclass(frozen=True)
class V6NonSwapCost:
    spread_price_per_ounce: float
    slippage_price_per_ounce: float
    commission_price_per_ounce: float

    @property
    def total_price_per_ounce(self) -> float:
        return (
            self.spread_price_per_ounce
            + self.slippage_price_per_ounce
            + self.commission_price_per_ounce
        )


@dataclass(frozen=True)
class V6CostModel:
    """Frozen protocol assumptions, without an invented rollover algorithm.

    Commission and slippage values are protocol assumptions, not broker-certified
    FundingPips specifications. Instrument point size and contract size are
    required inputs rather than undocumented defaults.
    """

    commission_round_turn_usd_per_lot: float = 10.0
    slippage_points_per_side: float = 0.5
    stress_multiplier: float = 1.0

    @property
    def version(self) -> str:
        return V6_COST_MODEL_VERSION

    @property
    def broker_specification_status(self) -> str:
        return "UNVERIFIED"

    @property
    def swap_policy_status(self) -> str:
        return "UNRESOLVED"

    def __post_init__(self) -> None:
        values = (
            self.commission_round_turn_usd_per_lot,
            self.slippage_points_per_side,
            self.stress_multiplier,
        )
        if any(isinstance(value, bool) for value in values):
            raise ValueError("cost model values must be numeric, not boolean")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("cost model values must be finite")
        if self.commission_round_turn_usd_per_lot != 10.0:
            raise ValueError("v6 commission is frozen at USD 10 round turn per lot")
        if self.slippage_points_per_side != 0.5:
            raise ValueError("v6 slippage is frozen at 0.5 point per side")
        if self.stress_multiplier not in (1.0, 1.5):
            raise ValueError("v6 permits only the base and preregistered 1.5x stress")

    def stressed(self) -> V6CostModel:
        return replace(self, stress_multiplier=1.5)

    def non_swap_cost(
        self,
        *,
        spread_entry_points: float,
        spread_exit_points: float,
        point_size: float,
        contract_size_ounces_per_lot: float,
    ) -> V6NonSwapCost:
        inputs = (
            spread_entry_points,
            spread_exit_points,
            point_size,
            contract_size_ounces_per_lot,
        )
        if not all(math.isfinite(value) for value in inputs):
            raise ValueError("cost inputs must be finite")
        if spread_entry_points < 0 or spread_exit_points < 0:
            raise ValueError("spread inputs must be non-negative")
        if point_size <= 0 or contract_size_ounces_per_lot <= 0:
            raise ValueError("point size and contract size must be positive")

        stress = self.stress_multiplier
        return V6NonSwapCost(
            spread_price_per_ounce=(
                (spread_entry_points + spread_exit_points)
                * 0.5
                * point_size
                * stress
            ),
            slippage_price_per_ounce=(
                2.0 * self.slippage_points_per_side * point_size * stress
            ),
            commission_price_per_ounce=(
                self.commission_round_turn_usd_per_lot
                / contract_size_ounces_per_lot
                * stress
            ),
        )

    def swap_cost(self, *, direction: int, rollovers: int) -> float:
        if direction not in (-1, 1):
            raise ValueError("direction must be +1 or -1")
        if isinstance(rollovers, bool) or not isinstance(rollovers, int) or rollovers < 0:
            raise ValueError("rollovers must be a non-negative integer")
        raise V6CostSemanticsBlockedError(
            "H4 rollover/stop ordering and FundingPips swap rates are unresolved"
        )
