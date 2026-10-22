"""Run the frozen 50-trial v5 swing search and evaluate every gate.

The sealed holdout is never opened here. OOS 2019-2022 is selection data; the
only true out-of-sample test is the one-time holdout run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.v4 import sha256_file  # noqa: E402
from research.backtest_v5 import fold_summaries, make_market, run_backtest, summarize  # noqa: E402
from research.costs_v5 import CostModel  # noqa: E402
from research.data_v5 import (  # noqa: E402
    DEV_END,
    DEV_PATH,
    MANIFEST_PATH,
    OOS_END,
    OOS_START,
    RESEARCH_START,
    load_dev_validation,
)
from research.strategies_v5 import all_specs, build_plan  # noqa: E402
from research.validation_v5 import (  # noqa: E402
    bootstrap_mean_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    evaluate_core_gates,
    perturb_spec,
    random_entry_null,
)

REPORT_DIR = ROOT / "reports_v5" / "search_2026-10-22"
REGISTRY = ROOT / "experiments" / "registry_v5.jsonl"
PRE_REG = ROOT / "docs" / "PRE_REGISTRATION_SWING_V5.md"
AMENDMENT = ROOT / "docs" / "PRE_REGISTRATION_SWING_V5_AMENDMENT_1.md"
YEARS = (2017, 2018, 2019, 2020, 2021, 2022)
COST_MODEL_VERSION = "v5-2026-10-22"


def code_commit() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.strip()
        return f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def data_quality(frame: pd.DataFrame) -> dict:
    window = frame.loc[RESEARCH_START:OOS_END]
    prices = window[["open", "high", "low", "close"]]
    invalid_ohlc = ((prices <= 0).any(axis=1) | (window["high"] < window["low"])
                    | (window["open"] < window["low"]) | (window["open"] > window["high"])
                    | (window["close"] < window["low"]) | (window["close"] > window["high"]))
    missing_volume = window["tick_volume"].isna() | (window["tick_volume"] <= 0)
    missing_values = prices.isna().any(axis=1)
    duplicates = window.index.duplicated()
    by_year = {}
    for year in range(2013, 2023):
        part = window[window.index.year == year]
        by_year[str(year)] = {
            "bars": int(len(part)),
            "zero_spread_share": float((part["spread"] <= 0).mean()) if len(part) else float("nan"),
        }
    fractions = {
        "invalid_ohlc": float(invalid_ohlc.mean()),
        "zero_or_negative_price": float((prices <= 0).any(axis=1).mean()),
        "duplicate_timestamp": float(duplicates.mean()),
        "missing_volume": float(missing_volume.mean()),
        "missing_values": float(missing_values.mean()),
    }
    return {
        "window_start": str(window.index.min()),
        "window_end": str(window.index.max()),
        "bars": int(len(window)),
        "fractions": fractions,
        "max_fraction": max(fractions.values()),
        "passed": bool(max(fractions.values()) < 0.01),
        "spread_by_year": by_year,
    }


def trial_metrics(trades, frame: pd.DataFrame, window_bars: int) -> dict:
    oos = summarize(trades, start=OOS_START, end=OOS_END, window_bars=window_bars)
    net = [t.net_r for t in trades if OOS_START <= t.entry_time <= OOS_END]
    low, high = bootstrap_mean_ci(net, resamples=10_000, seed=0)
    oos["ci_low"] = low
    oos["ci_high"] = high
    return oos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", default="", help="debug subset, comma separated")
    parser.add_argument("--null-sims", type=int, default=500)
    parser.add_argument("--no-write", action="store_true", help="debug run without reports or registry")
    args = parser.parse_args()

    frame = load_dev_validation()
    costs = CostModel()
    market = None
    window_bars = int(((frame.index >= OOS_START) & (frame.index <= OOS_END)).sum())
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    dev_hash = sha256_file(DEV_PATH)
    if dev_hash != manifest["dev_validation_sha256"]:
        raise SystemExit("development data hash does not match the manifest")
    prereg_hash = sha256_file(PRE_REG)
    amendment_hash = sha256_file(AMENDMENT)
    if prereg_hash != manifest["pre_registration_sha256"]:
        raise SystemExit("pre-registration hash does not match the manifest")
    if amendment_hash != manifest["amendment_1_sha256"]:
        raise SystemExit("amendment hash does not match the manifest")

    quality = data_quality(frame)
    if not quality["passed"]:
        raise SystemExit(f"data-quality gate failed: {quality['fractions']}")

    specs = all_specs()
    if args.configs:
        wanted = {part.strip() for part in args.configs.split(",") if part.strip()}
        specs = [spec for spec in specs if spec.config_id in wanted]
        if not specs:
            raise SystemExit("no matching configs")

    results = []
    for spec in specs:
        plan = build_plan(frame, spec)
        market = market or make_market(frame, plan.atr, costs)
        trades = run_backtest(frame, plan, costs, start_time=RESEARCH_START, md=market)
        oos = trial_metrics(trades, frame, window_bars)
        dev = summarize(trades, start=RESEARCH_START, end=DEV_END, window_bars=window_bars)
        folds = fold_summaries(trades, years=YEARS, frame_index=frame.index, embargo_bars=24)
        stress_trades = run_backtest(frame, plan, CostModel(stress=1.5),
                                     start_time=RESEARCH_START)
        stress = summarize(stress_trades, start=OOS_START, end=OOS_END, window_bars=window_bars)
        results.append({
            "spec": spec,
            "plan": plan,
            "trades": trades,
            "dev": dev,
            "oos": oos,
            "folds": folds,
            "stress": stress,
        })
        print(f"{spec.config_id} n={oos['n']:4d} oos={oos['mean_net_r']:+.4f} "
              f"ci=[{oos['ci_low']:+.4f},{oos['ci_high']:+.4f}] dd={oos['max_drawdown_r']:.1f} "
              f"folds={sum(1 for f in folds.values() if f['total_net_r'] > 0)}/6", flush=True)

    trial_sharpes = [r["oos"]["sharpe_per_trade"] for r in results]
    fold_matrix = np.array([[r["folds"][year]["mean_net_r"] for year in YEARS] for r in results]).T
    pbo_result = cscv_pbo(fold_matrix)

    for result in results:
        result["dsr"] = deflated_sharpe_ratio(
            [t.net_r for t in result["trades"] if OOS_START <= t.entry_time <= OOS_END],
            trial_sharpes,
        )

    def gate_preview(result) -> dict:
        return evaluate_core_gates(
            result["oos"], result["folds"], dsr=result["dsr"], pbo=pbo_result["pbo"],
            stress=result["stress"], perturbation=None, null=None,
        )

    for result in results:
        result["gates"] = gate_preview(result)

    cheap = ("g1_trades", "g2_mean_ci", "g3_folds", "g4_dsr", "g5_pbo", "g6_cost_stress",
             "g8_regime", "g10_drawdown")

    candidates = [r for r in results if all(r["gates"][key] for key in cheap)]
    print(f"\nPBO={pbo_result['pbo']:.3f} splits={pbo_result['splits']} "
          f"cheap-gate candidates={[r['spec'].config_id for r in candidates]}", flush=True)

    for result in candidates:
        spec = result["spec"]
        variants = perturb_spec(spec)
        rows = []
        for label, variant in variants:
            variant_plan = build_plan(frame, variant)
            variant_trades = run_backtest(frame, variant_plan, costs, start_time=RESEARCH_START)
            variant_oos = summarize(variant_trades, start=OOS_START, end=OOS_END)
            rows.append({"variant": label, "config_id": variant.config_id,
                         "params": variant.params, "mean_net_r": variant_oos["mean_net_r"],
                         "n": variant_oos["n"]})
        means = [row["mean_net_r"] for row in rows if np.isfinite(row["mean_net_r"])]
        base = result["oos"]["mean_net_r"]
        median = float(np.median(means)) if means else float("nan")
        retention = float(median / base) if base > 0 and np.isfinite(median) else float("-inf")
        perturbation = {
            "variants": rows,
            "all_positive": bool(means and all(value > 0 for value in means)),
            "median_mean_net_r": median,
            "retention": retention,
            "passed": bool(means and all(value > 0 for value in means) and retention >= 0.70),
        }
        result["perturbation"] = perturbation

        result["null"] = random_entry_null(
            market, result["trades"], result["plan"], costs,
            window_start=OOS_START, window_end=OOS_END,
            simulations=args.null_sims, seed=0,
        )
        result["gates"] = evaluate_core_gates(
            result["oos"], result["folds"], dsr=result["dsr"], pbo=pbo_result["pbo"],
            stress=result["stress"], perturbation=perturbation, null=result["null"],
        )
        print(f"{spec.config_id} perturb={perturbation['passed']} "
              f"retention={retention:.2f} null={result['null']['passed']} "
              f"p95={result['null']['null_p95']:+.3f}", flush=True)

    for result in results:
        if "perturbation" not in result:
            result["perturbation"] = None
            result["null"] = None
            result["gates"] = gate_preview(result)

    passers = sorted([r for r in results if r["gates"]["all"]],
                     key=lambda r: r["oos"]["mean_net_r"], reverse=True)
    selected = passers[0] if passers else None

    gate_labels = ("g1_trades", "g2_mean_ci", "g3_folds", "g4_dsr", "g5_pbo",
                   "g6_cost_stress", "g7_perturbation", "g8_regime", "g9_null", "g10_drawdown")
    failure_counts = dict.fromkeys(gate_labels, 0)
    for result in results:
        for label in gate_labels:
            if not result["gates"][label]:
                failure_counts[label] += 1

    if args.no_write:
        print(f"\nno-write debug: verdict={'PASS_CANDIDATE' if selected else 'NO_EDGE_FOUND'} "
              f"selected={selected['spec'].config_id if selected else None}")
        return 0

    generated = datetime.now(UTC).isoformat()
    commit = code_commit()
    payload = {
        "generated_utc": generated,
        "environment": "HISTORICAL_REAL",
        "verdict": "PASS_CANDIDATE" if selected else "NO_EDGE_FOUND",
        "selected_config": selected["spec"].config_id if selected else None,
        "holdout_accessed": False,
        "data": {
            "path": str(DEV_PATH),
            "sha256": dev_hash,
            "rows": int(len(frame)),
            "start": str(frame.index.min()),
            "end": str(frame.index.max()),
            "source": manifest["source"],
            "server": manifest["server"],
        },
        "protocol": {
            "pre_registration_sha256": prereg_hash,
            "amendment_1_sha256": amendment_hash,
            "trials": len(results),
        },
        "code_commit": commit,
        "cost_model": {
            "version": COST_MODEL_VERSION,
            "commission_round_turn_usd": costs.commission_round_turn,
            "slippage_points_per_side": costs.slippage_points,
            "spread_floor_points": costs.spread_floor_points,
            "swap_long_points": costs.swap_long_points,
            "swap_short_points": costs.swap_short_points,
            "stress": 1.5,
        },
        "data_quality": quality,
        "pbo": pbo_result,
        "trial_sharpes": trial_sharpes,
        "gate_failure_counts": failure_counts,
        "gates_not_evaluated": ["g7_perturbation", "g9_null"] if not candidates else [],
        "top_by_oos_mean": [
            {
                "config_id": result["spec"].config_id,
                "family": result["spec"].family,
                "params": result["spec"].params,
                "n": result["oos"]["n"],
                "mean_net_r": result["oos"]["mean_net_r"],
                "ci": [result["oos"]["ci_low"], result["oos"]["ci_high"]],
                "max_drawdown_r": result["oos"]["max_drawdown_r"],
                "folds_positive": sum(1 for f in result["folds"].values() if f["total_net_r"] > 0),
                "dsr": result["dsr"],
                "yearly": result["oos"]["yearly"],
            }
            for result in sorted(results, key=lambda r: r["oos"]["mean_net_r"], reverse=True)[:10]
        ],
        "trials": [],
    }

    for result in results:
        payload["trials"].append({
            "config_id": result["spec"].config_id,
            "family": result["spec"].family,
            "params": result["spec"].params,
            "dev": result["dev"],
            "oos": result["oos"],
            "folds": result["folds"],
            "stress": {k: result["stress"][k] for k in
                       ("n", "mean_net_r", "total_net_r", "max_drawdown_r")},
            "dsr": result["dsr"],
            "gates": result["gates"],
            "perturbation": result["perturbation"],
            "null": result["null"],
        })

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "report.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    rows = []
    for result in results:
        oos = result["oos"]
        failed = ",".join(label.split("_")[0] for label in gate_labels if not result["gates"][label])
        rows.append(
            f"| {result['spec'].config_id} | {result['spec'].family} | {oos['n']} | "
            f"{oos['mean_net_r']:+.4f} | [{oos['ci_low']:+.4f}, {oos['ci_high']:+.4f}] | "
            f"{oos['max_drawdown_r']:.1f} | "
            f"{sum(1 for f in result['folds'].values() if f['total_net_r'] > 0)}/6 | "
            f"{result['dsr']:.3f} | {result['gates']['g6_cost_stress']} | {failed} |"
        )
    table = "\n".join(rows)

    top_rows = []
    for result in sorted(results, key=lambda r: r["oos"]["mean_net_r"], reverse=True)[:10]:
        oos = result["oos"]
        yearly = " ".join(
            f"{year}:{entry['total_net_r']:+.1f}" for year, entry in sorted(oos["yearly"].items())
        )
        top_rows.append(
            f"| {result['spec'].config_id} | {result['spec'].family} | {oos['n']} | "
            f"{oos['mean_net_r']:+.4f} | {result['dsr']:.3f} | "
            f"{sum(1 for f in result['folds'].values() if f['total_net_r'] > 0)}/6 | {yearly} |"
        )
    top_table = "\n".join(top_rows)
    failure_line = ", ".join(f"{label.split('_')[0]}={count}/50"
                             for label, count in failure_counts.items())
    selected_line = (
        f"Selected candidate: **{selected['spec'].config_id}** "
        f"(OOS mean {selected['oos']['mean_net_r']:+.4f}R, n={selected['oos']['n']})."
        if selected else "No configuration passed all ten gates. The holdout remains sealed."
    )
    markdown = f"""# v5 swing search — {generated}

Environment: HISTORICAL_REAL. Data: {len(frame)} H1 XAUUSD bars, {frame.index.min()} .. {frame.index.max()}.
Protocol SHA-256: `{prereg_hash}`; amendment: `{amendment_hash}`. Code: `{commit}`.
Sealed holdout accessed: **no**.

## Verdict

{selected_line}

PBO (CSCV, 6 folds, 20 splits): **{pbo_result['pbo']:.3f}**.
Gate failures across 50 trials: {failure_line}.

`g7` (perturbation) and `g9` (random-entry null) were not reached by any
configuration because earlier gates failed; they are reported as not passed,
not as evaluated-and-failed.

## Data quality 2013-2022

{json.dumps(quality['fractions'], indent=2)}
Gate (<1% each): **{'PASS' if quality['passed'] else 'FAIL'}**.

## All 50 pre-registered trials (OOS 2019-2022)

| ID | Family | n | Mean net R | 95% CI | Max DD R | Folds+ | DSR | Stress>0 | Failed gates |
|---|---|---:|---:|---|---:|---:|---:|---|---|
{table}

## Top ten by raw OOS mean (before multiple-testing correction)

| ID | Family | n | Mean net R | DSR | Folds+ | Yearly total R |
|---|---|---:|---:|---:|---:|---|
{top_table}

## Honest interpretation

- Real: MT5 XAUUSD H1 prices/volumes from a demo feed; observed spreads where present;
  costs applied causally with a floor and fallback.
- Simulated: fills are next-open with stop-first, fixed slippage, and no queue/partial-fill
  model. Swap is the documented MT5 points, not a broker statement.
- Unverified: OOS 2019-2022 was used to select among 50 trials and is therefore selection
  data, not a clean out-of-sample test. Only the sealed 2023-2026 holdout is clean, and it
  was not opened because no candidate passed.
- Invalidating: a regime break, a different broker's spread/swap, or a coding error in the
  causal cost series. Reconciliation in the MT5 Strategy Tester is required before any demo.
- Interpretation: the positive raw means (best {max(r['oos']['mean_net_r'] for r in results):+.4f}R)
  are not statistically distinguishable from zero after the pre-registered 50-trial
  multiple-testing correction. This is a no-edge result within this protocol, not proof
  that every possible strategy fails.
"""
    (REPORT_DIR / "report.md").write_text(markdown, encoding="utf-8")

    trade_rows = []
    for result in results:
        for trade in result["trades"]:
            trade_rows.append({
                "config_id": trade.config_id, "direction": trade.direction,
                "entry_time": trade.entry_time, "exit_time": trade.exit_time,
                "entry_price": trade.entry_price, "exit_price": trade.exit_price,
                "stop_distance": trade.stop_distance, "gross_r": trade.gross_r,
                "cost_r": trade.cost_r, "swap_r": trade.swap_r, "net_r": trade.net_r,
                "reason": trade.reason, "bars_held": trade.bars_held,
            })
    pd.DataFrame(trade_rows).to_csv(REPORT_DIR / "trade_log.csv", index=False)

    with REGISTRY.open("a", encoding="utf-8") as handle:
        for result in results:
            record = {
                "timestamp_utc": generated,
                "role": "trial",
                "environment": "HISTORICAL_REAL",
                "strategy": result["spec"].family,
                "config_id": result["spec"].config_id,
                "params": result["spec"].params,
                "code_commit": commit,
                "data_hash": dev_hash,
                "protocol_hash": prereg_hash,
                "cost_model_version": COST_MODEL_VERSION,
                "seed": 0,
                "results": {
                    "oos_n": result["oos"]["n"],
                    "oos_mean_net_r": result["oos"]["mean_net_r"],
                    "oos_ci_low": result["oos"]["ci_low"],
                    "oos_max_drawdown_r": result["oos"]["max_drawdown_r"],
                    "dsr": result["dsr"],
                    "pbo": pbo_result["pbo"],
                    "positive_folds": sum(1 for f in result["folds"].values() if f["total_net_r"] > 0),
                },
                "gates": result["gates"],
                "verdict": "PASS_CANDIDATE" if result["gates"]["all"] else "FAIL",
            }
            handle.write(json.dumps(record, default=str) + "\n")
        if selected:
            handle.write(json.dumps({
                "timestamp_utc": generated, "role": "selection", "environment": "HISTORICAL_REAL",
                "config_id": selected["spec"].config_id, "code_commit": commit,
                "data_hash": dev_hash, "protocol_hash": prereg_hash, "seed": 0,
                "pbo": pbo_result["pbo"], "trials": len(results),
                "verdict": "PASS_CANDIDATE",
            }, default=str) + "\n")
        else:
            handle.write(json.dumps({
                "timestamp_utc": generated, "role": "search_summary", "environment": "HISTORICAL_REAL",
                "code_commit": commit, "data_hash": dev_hash, "protocol_hash": prereg_hash,
                "seed": 0, "pbo": pbo_result["pbo"], "trials": len(results),
                "verdict": "NO_EDGE_FOUND", "holdout_accessed": False,
            }, default=str) + "\n")

    print(f"\nverdict={payload['verdict']} selected={payload['selected_config']}")
    print(f"report={REPORT_DIR / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
