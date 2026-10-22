from datetime import date

import pytest

from data import collectors
import pandas as pd


class _Response:
    def __init__(self, status_code, content=b"x", text="error"):
        self.status_code = status_code
        self.content = content
        self.text = text
        self.headers = {"Retry-After": "7"}
        self.ok = status_code < 400

    def raise_for_status(self):
        raise RuntimeError(f"status {self.status_code}")


def test_dukascopy_fixture_retry_and_resume(monkeypatch, tmp_path):
    responses = iter([_Response(429, text="rate limited"), _Response(200, b"fixture")])
    monkeypatch.setattr(collectors.requests, "get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(collectors.time, "sleep", lambda _: None)
    state = {"count": 0}
    path = collectors.collect_dukascopy_candles("XAUUSD", date(2024, 1, 1), tmp_path,
                                                min_delay_seconds=0, request_state=state)
    assert path.read_bytes() == b"fixture"
    assert state["count"] == 2
    assert collectors.collect_dukascopy_candles("XAUUSD", date(2024, 1, 1), tmp_path,
                                                request_state=state) == path
    assert state["count"] == 2


def test_dukascopy_fixture_preserves_429_body_and_headers(monkeypatch, tmp_path):
    monkeypatch.setattr(collectors.requests, "get", lambda *args, **kwargs: _Response(429, text="blocked"))
    with pytest.raises(RuntimeError, match="body='blocked'.*Retry-After"):
        collectors.collect_dukascopy_candles("XAUUSD", date(2024, 1, 2), tmp_path,
                                             retries=0, min_delay_seconds=0)


def test_binance_fixture_detects_milliseconds_and_microseconds():
    milliseconds = pd.Series([1704067200000])
    microseconds = pd.Series([1704067200000000])
    assert collectors.binance_epoch_to_datetime(milliseconds).iloc[0] == pd.Timestamp("2024-01-01", tz="UTC")
    assert collectors.binance_epoch_to_datetime(microseconds).iloc[0] == pd.Timestamp("2024-01-01", tz="UTC")
