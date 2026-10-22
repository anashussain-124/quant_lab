import pandas as pd

from orchestrator.pipeline import run_dry_run


def test_dry_pipeline_runs_real_logic_without_holdout(tmp_path):
    index = pd.date_range("2024-01-01", periods=250, freq="5min", tz="UTC")
    frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                          "close": 100.0, "spread": 10.0}, index=index)
    frame.to_parquet(tmp_path / "bars.parquet")
    result = run_dry_run(tmp_path / "bars.parquet", trials=1, output_dir=tmp_path / "report")
    assert result["trial_count"] == 1
    assert result["survivors"] == 0
