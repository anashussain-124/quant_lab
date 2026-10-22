"""Numpy-array execution path with the reference simulator as its oracle.

The loop is intentionally explicit, but avoids per-bar ``DataFrame.iloc``
objects.  Prices are converted to Decimal at fills and exits, so the ledger
has the same exact-money semantics as :func:`simulate_next_open`.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from execution.causal_sim import CostConfig, SimTrade, _fill


def simulate_next_open_fast(
    bars: pd.DataFrame,
    signals: dict[int, int],
    *,
    stop_distance: Decimal | None = None,
    target_distance: Decimal | None = None,
    max_hold: int | None = None,
    costs: CostConfig | None = None,
) -> list[SimTrade]:
    """Run the causal simulator over numpy arrays, preserving reference output."""
    required = {"open", "high", "low", "close"}
    if not required.issubset(bars.columns):
        raise ValueError("bars require open, high, low, close")
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars must be ordered")
    cfg = costs or CostConfig()
    opens = bars["open"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    closes = bars["close"].to_numpy()
    trades: list[SimTrade] = []
    pending: tuple[int, int, int] | None = None
    position: tuple[int, int, Decimal, Decimal] | None = None

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
        if pending is not None and idx == pending[1] and position is None:
            signal_idx, _, side = pending
            entry, commission = _fill(Decimal(str(opens[idx])), side, True, cfg)
            position = (signal_idx, side, entry, commission)
            pending = None
        if position is not None:
            signal_idx, side, entry, _ = position
            stop = entry - Decimal(str(stop_distance)) * side if stop_distance is not None else None
            target = entry + Decimal(str(target_distance)) * side if target_distance is not None else None
            low = Decimal(str(lows[idx]))
            high = Decimal(str(highs[idx]))
            stop_hit = stop is not None and ((low <= stop) if side == 1 else (high >= stop))
            target_hit = target is not None and ((high >= target) if side == 1 else (low <= target))
            if stop_hit:
                close(idx, "stop", stop)
            elif target_hit:
                close(idx, "target", target)
            elif max_hold is not None and idx - (signal_idx + 1) + 1 >= max_hold:
                close(idx, "time", Decimal(str(closes[idx])))
        if position is None and pending is None and idx in signals and idx + 1 < len(bars):
            pending = (idx, idx + 1, signals[idx])

    if position is not None:
        close(len(bars) - 1, "final", Decimal(str(closes[-1])))
    return trades


__all__ = ["simulate_next_open_fast"]
