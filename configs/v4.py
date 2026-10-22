"""Validated v4 configuration models.

These models intentionally fail closed. They do not enable live trading and
do not contain live credentials or a live execution mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class V4Environment(StrEnum):
    HISTORICAL_REAL = "HISTORICAL_REAL"
    SIMULATED = "SIMULATED"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    MT5_DEMO = "MT5_DEMO"


class ExecutionMode(StrEnum):
    SHADOW = "SHADOW"
    MT5_DEMO = "MT5_DEMO"


@dataclass(frozen=True)
class SearchConfig:
    seed: int = 7
    max_trials: int = 1000
    final_holdout_fraction: float = 0.15
    embargo_bars: int = 20
    min_trades: int = 300

    def __post_init__(self) -> None:
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.max_trials < 1:
            raise ValueError("max_trials must be positive")
        if not 0.0 < self.final_holdout_fraction < 0.5:
            raise ValueError("final_holdout_fraction must be between 0 and 0.5")
        if self.embargo_bars < 0:
            raise ValueError("embargo_bars must be non-negative")
        if self.min_trades < 1:
            raise ValueError("min_trades must be positive")


@dataclass(frozen=True)
class SafetyConfig:
    environment: V4Environment = V4Environment.BACKTEST
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    stop_file: Path = Path("STOP")
    enable_demo_orders: bool = False

    def __post_init__(self) -> None:
        if self.enable_demo_orders and self.execution_mode != ExecutionMode.MT5_DEMO:
            raise ValueError("demo orders require execution_mode=MT5_DEMO")
        if self.environment == V4Environment.MT5_DEMO and self.execution_mode != ExecutionMode.MT5_DEMO:
            raise ValueError("MT5_DEMO environment requires MT5_DEMO execution mode")


@dataclass(frozen=True)
class WeekRunConfig:
    search: SearchConfig = field(default_factory=SearchConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    checkpoint_dir: Path = Path("reports_v4/checkpoints")
    data_dir: Path = Path("data/store")

    def __post_init__(self) -> None:
        if not self.checkpoint_dir:
            raise ValueError("checkpoint_dir is required")


def stop_requested(config: SafetyConfig) -> bool:
    """Return whether the global kill-switch file exists."""
    return config.stop_file.is_file()
