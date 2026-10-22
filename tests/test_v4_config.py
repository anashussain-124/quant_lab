from pathlib import Path

import pytest

from configs.v4 import ExecutionMode, SafetyConfig, SearchConfig, V4Environment, WeekRunConfig, stop_requested


def test_search_config_rejects_invalid_holdout() -> None:
    with pytest.raises(ValueError, match="final_holdout_fraction"):
        SearchConfig(final_holdout_fraction=0.5)


def test_safety_config_rejects_demo_orders_outside_mt5_demo() -> None:
    with pytest.raises(ValueError, match="execution_mode"):
        SafetyConfig(enable_demo_orders=True)


def test_week_run_accepts_explicit_mt5_demo_mode_without_live_mode() -> None:
    config = WeekRunConfig(
        safety=SafetyConfig(
            execution_mode=ExecutionMode.MT5_DEMO,
            environment=V4Environment.MT5_DEMO,
            enable_demo_orders=True,
        )
    )
    assert config.safety.environment == V4Environment.MT5_DEMO


def test_stop_file_is_global_and_fail_closed(tmp_path: Path) -> None:
    config = SafetyConfig(stop_file=tmp_path / "STOP")
    assert not stop_requested(config)
    config.stop_file.touch()
    assert stop_requested(config)
