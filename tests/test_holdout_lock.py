import pandas as pd
import pytest

from data.holdout import ResearchDataAccessor, read_final_holdout


def test_training_accessor_physically_excludes_latest_rows():
    index = pd.date_range("2024-01-01", periods=20, freq="h", tz="UTC")
    accessor = ResearchDataAccessor(pd.DataFrame({"close": range(20)}, index=index))
    training = accessor.training_data()
    assert len(training) == 17
    assert training.index.max() < index[-3]


def test_holdout_requires_authorized_script_and_locks_once(tmp_path):
    path = tmp_path / "data.parquet"
    pd.DataFrame({"close": [1, 2]}).to_parquet(path)
    with pytest.raises(PermissionError):
        read_final_holdout(path, tmp_path / "lock", caller_token="search")
    lock = tmp_path / "lock"
    assert len(read_final_holdout(path, lock, caller_token="run_holdout_once")) == 2
    with pytest.raises(RuntimeError):
        read_final_holdout(path, lock, caller_token="run_holdout_once")
