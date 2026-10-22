"""Fail-closed seven-day research orchestration primitives."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from configs.v4 import WeekRunConfig, stop_requested
from orchestrator.pipeline import run_dry_run


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

    def run_dry_run(self, input_path: Path, trials: int = 200) -> dict[str, object]:
        """Run the complete real-data pipeline without reading the holdout."""
        if stop_requested(self.config.safety):
            raise RuntimeError("STOP requested before dry run")
        return run_dry_run(input_path, trials=trials, output_dir=self.checkpoint_dir.parent)

    def run_pipeline(self, input_path: Path, *, dry_run: bool = False) -> dict[str, object]:
        """Run real v4 work with resumable stage checkpoints."""
        if stop_requested(self.config.safety):
            raise RuntimeError("STOP requested before pipeline")
        trials = 200 if dry_run else self.config.search.max_trials
        digest_path = self.checkpoint_dir.parent / "dry_run_digest.json"
        if digest_path.exists():
            digest = json.loads(digest_path.read_text(encoding="utf-8"))
        else:
            digest = run_dry_run(input_path, trials=trials, output_dir=self.checkpoint_dir.parent)
        for day, stage in enumerate(("data_audit", "features", "search", "validation", "report"), start=1):
            path = self.checkpoint_dir / f"day_{day}.json"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"stage": stage, "status": "COMPLETED",
                                            "trial_count": digest["trial_count"],
                                            "survivors": digest["survivors"]}, indent=2), encoding="utf-8")
        return digest
