import json
from pathlib import Path

import pandas as pd

from scripts.fundingpips_xauusd_runner import run


def _bars(path: Path) -> None:
    index = pd.date_range("2024-01-01", periods=400, freq="5min", tz="UTC")
    frame = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                          "close": 100.0, "spread": 10.0}, index=index)
    frame.to_parquet(path)


def test_runner_preflight_and_smoke_are_unique_and_fail_closed(tmp_path):
    input_path = tmp_path / "bars.parquet"
    _bars(input_path)
    output = tmp_path / "reports"
    preflight = run("preflight", input_path, output, "compare", 5000.0, 0.0025,
                    tmp_path / "registry.jsonl")
    smoke = run("smoke", input_path, output, "standard-5", 5000.0, 0.0025,
                tmp_path / "registry.jsonl")
    assert preflight != smoke
    manifest = json.loads((smoke / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"]["holdout_accessed"] is False
    assert manifest["risk_certification"] == "INCOMPLETE"
    assert (smoke / "strategy_metrics.csv").exists()
    assert (smoke / "trade_log.csv").exists()
