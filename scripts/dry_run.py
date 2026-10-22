"""Run the real-data v4 pipeline without touching the sealed holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from configs.v4 import WeekRunConfig
from orchestrator.week import WeekOrchestrator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--output-dir", type=Path, default=Path("reports_v4"))
    args = parser.parse_args()
    result = WeekOrchestrator(WeekRunConfig(checkpoint_dir=args.output_dir / "checkpoints")).run_pipeline(
        args.input, dry_run=args.trials <= 200)
    print(json.dumps({"training_rows": result["training_rows"], "sample_rows": result["sample_rows"],
                      "trial_count": result["trial_count"], "survivors": result["survivors"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
