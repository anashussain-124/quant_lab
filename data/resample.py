"""Causal resampling of completed OHLCV bars."""

from __future__ import annotations

import pandas as pd


def resample_completed(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample only bars whose right edge has already occurred."""
    allowed = {"M15": "15min", "H1": "1h", "H4": "4h"}
    if timeframe not in allowed:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("resampling requires a timezone-aware index")
    frame = frame.sort_index()
    rule = allowed[timeframe]
    aggregation: dict[str, str] = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for column in ("volume", "real_volume", "spread"):
        if column in frame.columns:
            aggregation[column] = "sum" if column != "spread" else "mean"
    output = frame.resample(rule, label="right", closed="right").agg(aggregation).dropna(subset=["open", "high", "low", "close"])
    last_completed = frame.index[-1].floor(rule)
    return output.loc[output.index <= last_completed]


__all__ = ["resample_completed"]
