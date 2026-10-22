"""Real dry-run wiring for collection, audit, search, and reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from data.holdout import ResearchDataAccessor
from features.causal import causal_atr, causal_ema
from search.run_search import TrialOutcome, run_search
from sim.monte_carlo import run_monte_carlo
from risk.profiles import FUNDINGPIPS_STANDARD_PHASE1


def run_dry_run(input_path: Path, *, trials: int = 200, output_dir: Path = Path("reports_v4")) -> dict[str, object]:
    """Execute the complete non-holdout pipeline on a bounded real-data slice."""
    frame = pd.read_parquet(input_path)
    if "timestamp" in frame.columns:
        frame = frame.set_index("timestamp")
    frame = frame.sort_index()
    training = ResearchDataAccessor(frame).training_data()
    # 5,000 bars bounds the dry-run while retaining enough history for all templates.
    sample = training.iloc[:5000].copy()
    features = {"ema": causal_ema(sample["close"], 20), "atr": causal_atr(sample, 20)}
    registry = output_dir / "dry_run_registry.jsonl"
    outcomes = run_search(sample, trials=trials, seed=7, registry_path=registry)
    survivors = [item for item in outcomes if item.gate.verdict == "PASS-CANDIDATE"]
    mc = []
    for item in survivors:
        mc.append({"hypothesis": item.hypothesis,
                   "fundingpips": run_monte_carlo(pd.Series([item.mean_net_r]).to_numpy(),
                                                   FUNDINGPIPS_STANDARD_PHASE1, simulations=10_000).__dict__})
    digest = {"environment": "BACKTEST", "input": str(input_path), "training_rows": len(training),
              "sample_rows": len(sample), "trial_count": len(outcomes), "survivors": len(survivors),
              "features_non_null": {key: int(value.notna().sum()) for key, value in features.items()},
              "results": [{"trial": item.trial, "hypothesis": item.hypothesis, "n": item.n,
                           "mean_net_r": item.mean_net_r, "t_stat": item.gate.t_stat,
                           "permutation_percentile": item.gate.permutation_percentile,
                           "fold_pfs": item.fold_pfs, "dsr": item.dsr,
                           "neighborhood_stability": item.neighborhood_stability,
                           "cost_stress_1_5x": item.cost_stress_1_5x,
                           "cost_stress_2x": item.cost_stress_2x, "verdict": item.gate.verdict}
                          for item in outcomes], "monte_carlo": mc}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dry_run_digest.json").write_text(json.dumps(digest, indent=2, default=str), encoding="utf-8")
    return digest
