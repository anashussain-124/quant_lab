from pathlib import Path

import pandas as pd
import pytest

from data.v4 import audit_ohlcv, normalize_utc, partition_parquet, sha256_file


def _frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}, index=index)


def test_naive_timestamps_require_explicit_offset() -> None:
    with pytest.raises(ValueError, match="broker_utc_offset"):
        normalize_utc(pd.DataFrame({"time": ["2024-01-01 03:00"]}), "time")


def test_broker_timestamp_is_normalized_to_utc() -> None:
    frame = normalize_utc(pd.DataFrame({"time": ["2024-01-01 03:00"]}), "time", broker_utc_offset=3)
    assert frame.index[0] == pd.Timestamp("2024-01-01 00:00:00+00:00")


def test_audit_and_partition_store_are_hashed(tmp_path: Path) -> None:
    frame = _frame(pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC"))
    audit = audit_ohlcv(frame, expected_interval="1h")
    assert audit["invalid_ohlc_rows"] == 0
    records = partition_parquet(frame, tmp_path, "XAUUSD", "1h")
    assert len(records) == 1
    assert records[0].sha256 == sha256_file(Path(records[0].path))
