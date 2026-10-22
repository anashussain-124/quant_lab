"""Cost decomposition for every registered hypothesis on pre-holdout data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from data.holdout import ResearchDataAccessor
from execution.causal_sim import CostConfig
from execution.fast_sim import simulate_next_open_fast
from hypotheses.generators import GENERATORS
from hypotheses.registry import hypothesis_registry


def _params(spec, registry_path: Path) -> tuple[dict[str, float], dict[str, float]]:
    default = {key: float(np.median(values)) for key, values in spec.parameter_ranges.items()}
    records = []
    if registry_path.exists():
        for line in registry_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if (record.get("strategy") == spec.name and
                        not record.get("results", {}).get("degenerate", False)):
                    records.append(record["params"])
    median = {key: float(np.median([float(item[key]) for item in records]))
              for key in spec.parameter_ranges} if records else dict(default)
    return default, median


def _metrics(frame: pd.DataFrame, spec, params: dict[str, float], label: str) -> dict[str, object]:
    plan = GENERATORS[spec.name](frame, params)
    spread_price = float(frame["spread"].mean()) * 0.01 if "spread" in frame else 0.01
    mid = float(frame["close"].mean())
    spread_bps = max(0.01, spread_price / mid * 10000.0)
    trades = simulate_next_open_fast(
        frame, plan.signals, stop_distance=Decimal(str(plan.stop_distance)),
        target_distance=Decimal(str(plan.target_distance)),
        max_hold=plan.max_hold,
        costs=CostConfig(Decimal(str(spread_bps)), Decimal(str(spread_bps / 2)), Decimal("0")),
    )
    stop = float(plan.stop_distance)
    # SimTrade.gross_pnl is fill-to-fill and therefore already includes the
    # spread/slippage embedded in the fill prices. Reconstruct mid-to-mid
    # gross return so the decomposition does not hide execution costs in gross.
    fill_adjustment = (spread_bps / 20000.0) + (spread_bps / 2.0 / 10000.0)
    gross_mid_values = []
    for item in trades:
        entry_mid = float(item.entry_price) / (1.0 + fill_adjustment * item.side)
        exit_mid = float(item.exit_price) / (1.0 - fill_adjustment * item.side)
        gross_mid_values.append((exit_mid - entry_mid) * item.side)
    gross = np.asarray(gross_mid_values) / stop
    net = np.asarray([float(item.net_pnl) / stop for item in trades])
    cost = gross - net
    wins = net[net > 0]
    losses = net[net < 0]
    def pf(values: np.ndarray) -> float:
        positive = values[values > 0].sum()
        negative = -values[values < 0].sum()
        return float(positive / negative) if negative else float("inf")
    return {
        "hypothesis": spec.name, "parameter_set": label, "params": json.dumps(params, sort_keys=True),
        "trades": int(len(net)), "trades_per_day": float(len(net) / max(1e-9, (frame.index[-1] - frame.index[0]).total_seconds() / 86400)),
        "mean_gross_R": float(gross.mean()) if len(gross) else float("nan"),
        "mean_cost_R": float(cost.mean()) if len(cost) else float("nan"),
        "mean_net_R": float(net.mean()) if len(net) else float("nan"),
        "win_rate": float((net > 0).mean()) if len(net) else float("nan"),
        "avg_win_R": float(wins.mean()) if len(wins) else float("nan"),
        "avg_loss_R": float(losses.mean()) if len(losses) else float("nan"),
        "gross_profit_factor": pf(gross), "net_profit_factor": pf(net),
        "average_spread_price": spread_price, "average_stop_distance_price": stop,
        "spread_stop_fraction": spread_price / stop,
        "break_even_win_rate_given_cost": float(abs(losses.mean()) / (wins.mean() + abs(losses.mean()))) if len(wins) and len(losses) else float("nan"),
        "positive_gross": bool(gross.mean() > 0) if len(gross) else False,
    }


def run(input_path: Path, output_dir: Path, registry_path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(input_path)
    if "timestamp" in frame.columns:
        frame = frame.set_index("timestamp")
    frame = ResearchDataAccessor(frame.sort_index()).training_data()
    rows = []
    for spec in hypothesis_registry():
        default, median = _params(spec, registry_path)
        rows.extend((_metrics(frame, spec, default, "default"),
                     _metrics(frame, spec, median, "median_searched")))
    result = pd.DataFrame(rows)
    data_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()[:16]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"cost_decomposition_{data_hash}.csv"
    result.to_csv(output, index=False)
    print(result.to_string(index=False))
    print(f"saved_csv={output}")
    print("positive_gross_templates=" + ",".join(sorted(result.loc[result.positive_gross, "hypothesis"].unique())) )
    print("negative_gross_templates=" + ",".join(sorted(result.loc[~result.positive_gross, "hypothesis"].unique())) )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("reports_v4"))
    parser.add_argument("--registry", type=Path, default=Path("experiments/registry.jsonl"))
    args = parser.parse_args()
    run(args.input, args.output_dir, args.registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
