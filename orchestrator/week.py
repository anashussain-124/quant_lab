"""Fail-closed seven-day research orchestration primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from configs.v4 import WeekRunConfig, stop_requested


@dataclass(frozen=True)
class Checkpoint:
    day: int
    stage: str
    status: str
    message: str


class WeekOrchestrator:
    def __init__(self, config: WeekRunConfig) -> None:
        self.config = config
        self.checkpoint_dir = Path(config.checkpoint_dir)

    def run(self) -> list[Checkpoint]:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoints: list[Checkpoint] = []
        stages = ("data_audit", "features", "search", "validation", "report")
        for day in range(1, 8):
            for stage in stages:
                if stop_requested(self.config.safety):
                    result = Checkpoint(day, stage, "STOPPED", "STOP requested")
                    checkpoints.append(result)
                    return checkpoints
                result = Checkpoint(day, stage, "PENDING", "No external work executed by default")
                checkpoints.append(result)
            (self.checkpoint_dir / f"day_{day}.json").write_text(
                "\n".join(f"{item.stage}: {item.status}" for item in checkpoints if item.day == day),
                encoding="utf-8",
            )
        return checkpoints
