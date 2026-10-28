"""Post-hoc risk frontier on the frozen v5 trade log.

This is NOT a strategy search and NOT candidate selection. It reuses the exact
50 frozen v5 trade paths and asks how FundingPips pass and breach rates change
when the per-trade risk fraction is raised above the values tested in the
mission. No v5 parameter is retuned, no trade is regenerated, and no result
here can promote a strategy.

Environment: BACKTEST. Every challenge account, fill and rule check is
simulated. A simulated pass is not a FundingPips pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.challenge_v5 import (  # noqa: E402
    _build_scenarios,
    _build_start_list,
    _init_worker,
    _run_config_task,
    _utc,
    _WorkerSettings,
    aggregate,
    load_inputs,
    summarize,
)

CONFIG_PATH = ROOT / "configs" / "fundingpips.yaml"
OUT_DIR = ROOT / "reports_fundingpips" / "risk_frontier_2026-10-23"
DEFAULT_RISKS = (0.005, 0.0075, 0.010, 0.015, 0.020)
SCENARIOS = ("primary", "firm_rules_only")
COST_CASES = ("fp10_base", "fp10_stress1.5")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--risks",
        default=",".join(f"{risk:g}" for risk in DEFAULT_RISKS),
        help="comma-separated risk fractions",
    )
    parser.add_argument("--configs", default="", help="debug subset")
    args = parser.parse_args()

    risks = [float(part) for part in args.risks.split(",") if part.strip()]
    if not risks or any(not 0 < risk <= 0.10 for risk in risks):
        raise SystemExit("risk fractions must be in (0, 0.10]")

    loaded = load_inputs(CONFIG_PATH)
    starts = _build_start_list(loaded)
    scenarios_all, costs_all = _build_scenarios(loaded)
    scenarios = [s for s in scenarios_all if s.name in SCENARIOS]
    costs = [c for c in costs_all if c.name in COST_CASES]
    if len(scenarios) != len(SCENARIOS) or len(costs) != len(COST_CASES):
        raise SystemExit("scenario or cost-case selection failed")

    config_ids = sorted(loaded.trades_by_config)
    if args.configs:
        wanted = {part.strip() for part in args.configs.split(",") if part.strip()}
        config_ids = [cid for cid in config_ids if cid in wanted]
        if not config_ids:
            raise SystemExit("no matching configs")

    horizon = _utc(loaded.config["challenge_simulation"]["evaluation_end_utc"])
    settings = _WorkerSettings(
        bars=loaded.bars,
        starts=starts,
        risk_fractions=risks,
        scenarios=scenarios,
        cost_cases=costs,
        rules=loaded.rules,
        horizon=horizon,
    )
    tasks = [(cid, loaded.trades_by_config[cid]) for cid in config_ids]
    total = len(tasks) * len(risks) * len(scenarios) * len(costs) * len(starts)
    print(
        f"risk frontier: {len(tasks)} configs x {len(risks)} risks x "
        f"{len(scenarios)} scenarios x {len(costs)} costs x {len(starts)} starts "
        f"= {total} attempts",
        flush=True,
    )

    started = time.perf_counter()
    results = []
    if args.workers > 1:
        with ProcessPoolExecutor(
            max_workers=args.workers,
            initializer=_init_worker,
            initargs=(settings,),
        ) as executor:
            for index, batch in enumerate(executor.map(_run_config_task, tasks), 1):
                results.extend(batch)
                print(
                    f"[{index}/{len(tasks)}] {tasks[index - 1][0]} "
                    f"({len(batch)} attempts, {time.perf_counter() - started:.0f}s)",
                    flush=True,
                )
    else:
        _init_worker(settings)
        for index, task in enumerate(tasks, 1):
            batch = _run_config_task(task)
            results.extend(batch)
            print(
                f"[{index}/{len(tasks)}] {task[0]} ({len(batch)} attempts, "
                f"{time.perf_counter() - started:.0f}s)",
                flush=True,
            )

    elapsed = time.perf_counter() - started
    aggregates = aggregate(results)
    summary = summarize(results, aggregates)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "generated_utc": stamp,
        "environment": "BACKTEST",
        "label": "POST_HOC_RISK_FRONTIER_NOT_CANDIDATE_SELECTION",
        "warning": (
            "Frozen v5 trades only. No parameter was retuned. A higher pass "
            "rate caused by larger position size is leverage, not edge, and a "
            "simulated pass is not a FundingPips pass."
        ),
        "source": {
            "config": str(CONFIG_PATH.relative_to(ROOT)),
            "config_sha256": loaded.config_sha256,
            "trade_log": loaded.config["challenge_simulation"]["trade_log"],
            "trade_log_sha256": loaded.trade_log_sha256,
            "market_data_sha256": loaded.data_sha256,
            "report_verdict": loaded.trial_report["verdict"],
        },
        "matrix": {
            "configs": len(config_ids),
            "risk_fractions": risks,
            "scenarios": [s.name for s in scenarios],
            "cost_cases": [c.name for c in costs],
            "rolling_starts": len(starts),
            "attempts": len(results),
            "elapsed_seconds": round(elapsed, 1),
        },
        "summary": summary,
        "per_config": [
            {
                "config_id": row["config_id"],
                "risk_fraction": row["risk_fraction"],
                "scenario": row["scenario"],
                "cost_case": row["cost_case"],
                "attempts": row["attempts"],
                "both_phase_pass": row["both_phase_pass"],
                "both_phase_pass_rate": row["both_phase_pass_rate"],
                "firm_breach": row["firm_breach_any"],
                "firm_breach_rate": row["firm_breach_rate"],
                "median_days_to_pass": row["median_days_to_pass"],
            }
            for row in aggregates
        ],
    }
    (OUT_DIR / "frontier.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )

    fieldnames = list(summary[0].keys()) if summary else []
    with (OUT_DIR / "frontier.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)

    lines = [
        "# FundingPips risk frontier (post-hoc, frozen v5 trades)",
        "",
        f"Generated: {stamp}. Environment: **BACKTEST**, simulated. "
        f"Attempts: {len(results):,} ({elapsed:.0f}s).",
        "",
        "This is a sensitivity analysis on frozen trades, **not** candidate selection.",
        "Raising risk raises both pass and breach probability; it cannot create edge.",
        "",
        "| Risk | Scenario | Cost | Attempts | Passes | Pass rate | Firm breaches | Breach rate | "
        "Median days to pass | p95 max DD |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['risk_fraction']:.2%} | {row['scenario']} | {row['cost_case']} | "
            f"{row['attempts']:,} | {row['both_phase_pass']:,} | "
            f"{row['both_phase_pass_rate']:.2%} | {row['firm_breach']:,} | "
            f"{row['firm_breach_rate']:.2%} | "
            f"{row['median_days_to_pass'] or float('nan'):.0f} | "
            f"{row['p95_max_dd_pct']:.2%} |"
        )
    lines += [
        "",
        "A simulated pass is not a FundingPips pass. No order was sent.",
        "",
    ]
    (OUT_DIR / "frontier.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "\n".join(lines[-17:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
