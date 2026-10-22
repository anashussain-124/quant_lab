"""Run frozen-parameter statistical validation on saved V3 trade logs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.validation import append_trial, deflated_sharpe_ratio, permutation_percentile, purged_walk_forward, t_test_mean


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=Path("experiments/registry.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("reports/validation_results.csv"))
    args = parser.parse_args()
    bars = pd.read_parquet(args.input)
    if "timestamp" in bars:
        index = pd.DatetimeIndex(pd.to_datetime(bars["timestamp"], utc=True))
    else:
        index = pd.DatetimeIndex(pd.to_datetime(bars.index, utc=True))
    trials = args.registry.read_text(encoding="utf-8").count("\n") if args.registry.exists() else 0
    rows: list[dict[str, object]] = []
    for report_path in sorted(args.reports.glob("*_research_report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        name = str(report["strategy"])
        trade_path = args.reports / f"{name}_research_trades.csv"
        if not trade_path.exists():
            continue
        trades = pd.read_csv(trade_path)
        values = trades["r"].to_numpy(float)
        stats = t_test_mean(values)
        signals = np.zeros(len(index), dtype=float)
        outcomes = np.zeros(len(index), dtype=float)
        for _, trade in trades.iterrows():
            exit_time = pd.Timestamp(trade["exit_time"]).tz_convert("UTC")
            exit_pos = index.searchsorted(exit_time)
            if exit_pos < len(index):
                # Trade logs contain realized outcomes, not the original full
                # signal stream. For the null, preserve the count of realized
                # exits and shift those signal slots against the outcome bars.
                signals[exit_pos] = 1.0
                outcomes[exit_pos] += float(trade["r"])
        perm = permutation_percentile(signals, outcomes)
        folds = purged_walk_forward(values, folds=6, embargo=20)
        positive_folds = sum(float(f["mean_R"]) > 0 for f in folds)
        dsr = deflated_sharpe_ratio(values, trials + 1)
        reasons: list[str] = []
        if int(stats["n"]) < 300:
            reasons.append("INSUFFICIENT SAMPLE: N < 300")
        if float(stats["t_stat"]) <= 3:
            reasons.append("t-statistic <= 3")
        if not np.isfinite(perm) or perm < 95:
            reasons.append("permutation percentile < 95")
        if positive_folds < 4:
            reasons.append("fewer than 4/6 positive walk-forward folds")
        if dsr < 0.95:
            reasons.append("DSR < 0.95")
        verdict = "PASS-CANDIDATE" if not reasons else ("FAIL" if float(stats["mean"]) <= 0 else "INCONCLUSIVE")
        row = {"strategy": name, "n": stats["n"], "mean_net_R": stats["mean"], "t_stat": stats["t_stat"], "p_value": stats["p_value"], "permutation_percentile": perm, "walk_forward_pfs": ",".join(f"{float(f['net_pf']):.3f}" for f in folds), "positive_folds": positive_folds, "dsr": dsr, "verdict": verdict, "reasons": "; ".join(reasons), "data_hash": report.get("data_hash"), "code_version": report.get("code_version")}
        rows.append(row)
        append_trial(args.registry, {"strategy": name, "params": report.get("risk"), "data_hash": report.get("data_hash"), "code_hash": report.get("code_version"), "results": row})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"saved={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
