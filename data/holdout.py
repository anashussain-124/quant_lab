"""Physically split research data from its sealed final holdout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class HoldoutSplit:
    train: pd.DataFrame
    cutoff: pd.Timestamp
    holdout_fraction: float


class ResearchDataAccessor:
    """Only exposes the pre-holdout training partition to search code."""

    def __init__(self, frame: pd.DataFrame, holdout_fraction: float = 0.15) -> None:
        if not 0 < holdout_fraction < 0.5:
            raise ValueError("holdout_fraction must be between 0 and 0.5")
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
            raise ValueError("research data requires a timezone-aware DatetimeIndex")
        if not frame.index.is_monotonic_increasing:
            raise ValueError("research data must be sorted")
        cutoff_position = max(1, int(len(frame) * (1.0 - holdout_fraction)))
        self._split = HoldoutSplit(frame.iloc[:cutoff_position].copy(),
                                   frame.index[cutoff_position - 1], holdout_fraction)

    @classmethod
    def from_parquet(cls, path: Path, holdout_fraction: float = 0.15) -> "ResearchDataAccessor":
        frame = pd.read_parquet(path)
        if "timestamp" in frame.columns:
            frame = frame.set_index("timestamp")
        return cls(frame.sort_index(), holdout_fraction)

    def training_data(self) -> pd.DataFrame:
        """Return a copy that physically contains no final-holdout rows."""
        return self._split.train.copy()

    @property
    def cutoff(self) -> pd.Timestamp:
        return self._split.cutoff


def read_final_holdout(path: Path, lock_path: Path, *, caller_token: str) -> pd.DataFrame:
    """Read the holdout once, only from the explicitly authorized script."""
    if caller_token != "run_holdout_once":
        raise PermissionError("final holdout is restricted to run_holdout_once.py")
    if lock_path.exists():
        raise RuntimeError("final holdout lock already exists")
    frame = pd.read_parquet(path)
    lock_path.write_text(pd.Timestamp.now(tz="UTC").isoformat(), encoding="utf-8")
    return frame
