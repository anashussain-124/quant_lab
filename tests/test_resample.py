import pandas as pd

from data.resample import resample_completed


def test_resample_excludes_partial_bar_and_is_prefix_stable():
    index = pd.date_range("2024-01-01", periods=13, freq="5min", tz="UTC")
    frame = pd.DataFrame({"open": range(13), "high": range(1, 14), "low": range(13), "close": range(1, 14)}, index=index)
    full = resample_completed(frame, "M15")
    prefix = resample_completed(frame.iloc[:10], "M15")
    assert full.index[-1] == pd.Timestamp("2024-01-01 01:00:00", tz="UTC")
    assert prefix.equals(full.loc[full.index <= prefix.index[-1]])


def test_resample_rejects_unknown_timeframe():
    frame = pd.DataFrame({"open": [1], "high": [1], "low": [1], "close": [1]},
                         index=pd.date_range("2024-01-01", periods=1, tz="UTC"))
    try:
        resample_completed(frame, "M5")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown timeframe was accepted")
