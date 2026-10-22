"""Minimal event-driven causal simulator used by the v4 validation tests.

Signals are decisions at bar close.  Orders fill at the next bar open.  The
simulator is deliberately small and explicit so its assumptions are auditable;
it is not a live execution adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pandas as pd

Side = Literal[1, -1]


@dataclass(frozen=True)
class CostConfig:
    # A zero-cost model must be explicit in a unit test; production defaults
    # are nonzero so v4 cannot silently run with a constant-zero spread.
    spread_bps: Decimal = Decimal("1")
    slippage_bps: Decimal = Decimal("0.5")
    commission_bps: Decimal = Decimal("0")


@dataclass(frozen=True)
class SimTrade:
    signal_index: int
    entry_index: int
    exit_index: int
    side: Side
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    exit_reason: str


def _d(value: object) -> Decimal:
    return Decimal(str(value))


def _fill(mid: Decimal, side: Side, is_entry: bool, costs: CostConfig) -> tuple[Decimal, Decimal]:
    half_spread = costs.spread_bps / Decimal("20000")
    slippage = costs.slippage_bps / Decimal("10000")
    # Buy pays ask and sell receives bid. Slippage worsens both directions.
    direction = Decimal("1") if side == 1 else Decimal("-1")
    signed = (half_spread + slippage) * direction
    price = mid * (Decimal("1") + signed) if is_entry else mid * (Decimal("1") - signed)
    commission = abs(price) * costs.commission_bps / Decimal("10000")
    return price, commission


def simulate_next_open(
    bars: pd.DataFrame,
    signals: dict[int, Side],
    *,
    stop_distance: Decimal | None = None,
    target_distance: Decimal | None = None,
    max_hold: int | None = None,
    costs: CostConfig | None = None,
) -> list[SimTrade]:
    """Simulate one-at-a-time positions with causal next-open execution."""
    required = {"open", "high", "low", "close"}
    if not required.issubset(bars.columns):
        raise ValueError("bars require open, high, low, close")
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars must be ordered")
    cfg = costs or CostConfig()
    trades: list[SimTrade] = []
    pending: tuple[int, int, Side] | None = None
    position: tuple[int, Side, Decimal, Decimal] | None = None

    def close(exit_idx: int, reason: str, mid: Decimal) -> None:
        nonlocal position
        assert position is not None
        signal_idx, side, entry, entry_commission = position
        exit_price, exit_commission = _fill(mid, side, False, cfg)
        gross = (exit_price - entry) * Decimal(side)
        costs_total = entry_commission + exit_commission
        trades.append(SimTrade(signal_idx, signal_idx + 1, exit_idx, side, entry,
                                exit_price, gross, costs_total, gross - costs_total, reason))
        position = None

    for idx in range(len(bars)):
        row = bars.iloc[idx]
        if pending is not None and idx == pending[1] and position is None:
            signal_idx, _, side = pending
            entry, commission = _fill(_d(row["open"]), side, True, cfg)
            position = (signal_idx, side, entry, commission)
            pending = None
        if position is not None:
            signal_idx, side, entry, _ = position
            stop = entry - _d(stop_distance) * side if stop_distance is not None else None
            target = entry + _d(target_distance) * side if target_distance is not None else None
            stop_hit = stop is not None and ((_d(row["low"]) <= stop) if side == 1 else (_d(row["high"]) >= stop))
            target_hit = target is not None and ((_d(row["high"]) >= target) if side == 1 else (_d(row["low"]) <= target))
            # Pessimistic ordering: if both are touched, stop is first.
            if stop_hit:
                close(idx, "stop", stop)
            elif target_hit:
                close(idx, "target", target)
            elif max_hold is not None and idx - (signal_idx + 1) + 1 >= max_hold:
                close(idx, "time", _d(row["close"]))
        if position is None and pending is None and idx in signals:
            if idx + 1 < len(bars):
                pending = (idx, idx + 1, signals[idx])

    if position is not None:
        close(len(bars) - 1, "final", _d(bars.iloc[-1]["close"]))
    return trades
