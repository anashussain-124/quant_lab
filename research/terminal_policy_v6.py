"""Approved terminal-entry policy for the frozen H4 v6 holding horizon."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from research.strategies_v6 import MAX_HOLD_H4


@dataclass(frozen=True)
class TerminalEntryDecision:
    signal_index: int
    signal_time: pd.Timestamp
    eligible: bool
    entry_index: int | None
    planned_exit_index: int | None
    skip_reason: str | None

    def as_skip_record(self) -> dict[str, int | str]:
        if self.eligible or self.skip_reason is None:
            raise ValueError("only skipped signals can be serialized as skip records")
        return {
            "signal_index": self.signal_index,
            "signal_time_utc": self.signal_time.isoformat(),
            "reason": self.skip_reason,
        }


def assess_terminal_entry(
    signal_index: int, index: pd.DatetimeIndex
) -> TerminalEntryDecision:
    """Require next-bar entry and the complete 120-bar next-open time exit."""
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("index must be a UTC-aware DatetimeIndex")
    if index.tz is None or str(index.tz) != "UTC":
        raise ValueError("index must be a UTC-aware DatetimeIndex")
    if not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("index must be strictly ordered and unique")
    if not 0 <= signal_index < len(index):
        raise ValueError("signal_index is outside the available index")

    entry_index = signal_index + 1
    if entry_index >= len(index):
        return TerminalEntryDecision(
            signal_index=signal_index,
            signal_time=index[signal_index],
            eligible=False,
            entry_index=None,
            planned_exit_index=None,
            skip_reason="next_bar_entry_open_unavailable",
        )

    planned_exit_index = entry_index + MAX_HOLD_H4
    if planned_exit_index >= len(index):
        return TerminalEntryDecision(
            signal_index=signal_index,
            signal_time=index[signal_index],
            eligible=False,
            entry_index=entry_index,
            planned_exit_index=planned_exit_index,
            skip_reason="full_120_bar_next_open_exit_unavailable",
        )

    return TerminalEntryDecision(
        signal_index=signal_index,
        signal_time=index[signal_index],
        eligible=True,
        entry_index=entry_index,
        planned_exit_index=planned_exit_index,
        skip_reason=None,
    )
