"""Seeded bounded search over the v4 hypotheses on non-holdout data only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from execution.causal_sim import CostConfig, SimTrade
from execution.fast_sim import simulate_next_open_fast
from hypotheses.generators import GENERATORS, SignalPlan
from hypotheses.registry import hypothesis_registry
from search.registry import ExperimentRegistry, code_hash
from validation.core import shifted_signal_percentile
from validation.gates import GateResult, evaluate_candidate
from validation.walkforward import deflated_sharpe, purged_walk_forward


@dataclass(frozen=True)
class TrialOutcome:
    trial: int
    hypothesis: str
    params: dict[str, float]
    n: int
    mean_net_r: float
    gate: GateResult
    fold_pfs: tuple[float, ...]
    dsr: float
    neighborhood_stability: float
    cost_stress_1_5x: float
    cost_stress_2x: float


def _sample(rng: np.random.Generator, ranges: dict[str, tuple[float, ...]]) -> dict[str, float]:
    sampled: dict[str, float] = {}
    for key, values in ranges.items():
        # Integer-valued bounds describe discrete choices (periods, hours, bars),
        # not a continuous interval that is later rounded and can collapse trials.
        if len(values) > 1 and all(float(value).is_integer() for value in values):
            sampled[key] = float(rng.choice(np.asarray(values, dtype=float)))
        else:
            sampled[key] = float(rng.uniform(min(values), max(values)))
    return sampled


def _trades(frame: pd.DataFrame, plan: SignalPlan, multiplier: float = 1.0) -> list[SimTrade]:
    spread_points = float(frame["spread"].mean()) if "spread" in frame.columns else 1.0
    # MT5 spread is in points; XAUUSD point size is 0.01. Convert the observed
    # average spread to an approximate price-bps cost without inventing a zero.
    spread_price = spread_points * 0.01
    mid = float(frame["close"].mean())
    bps = max(0.01, spread_price / mid * 10000.0) * multiplier
    return simulate_next_open_fast(frame, plan.signals,
                                   stop_distance=Decimal(str(plan.stop_distance)),
                                   target_distance=Decimal(str(plan.target_distance)),
                                   max_hold=plan.max_hold,
                                   costs=CostConfig(spread_bps=Decimal(str(bps)),
                                                    slippage_bps=Decimal(str(bps / 2)),
                                                    commission_bps=Decimal("0")))


def _r_values(trades: list[SimTrade], stop: float) -> np.ndarray:
    return np.asarray([float(t.net_pnl) / stop for t in trades], dtype=float)


def run_search(frame: pd.DataFrame, *, trials: int = 200, seed: int = 7,
               registry_path: Path = Path("experiments/registry.jsonl")) -> list[TrialOutcome]:
    if trials < 1:
        raise ValueError("trials must be positive")
    rng = np.random.default_rng(seed)
    registry = ExperimentRegistry(registry_path)
    input_hash = __import__("hashlib").sha256(
        __import__("pandas").util.hash_pandas_object(frame, index=True).values.tobytes()
    ).hexdigest()
    source_root = Path(__file__).resolve().parents[1]
    source_hash = code_hash(source_root)
    specs = hypothesis_registry()
    outcomes: list[TrialOutcome] = []
    for trial in range(trials):
        spec = specs[trial % len(specs)]
        params = _sample(rng, dict(spec.parameter_ranges))
        plan = GENERATORS[spec.name](frame, params)
        trades = _trades(frame, plan)
        values = _r_values(trades, plan.stop_distance)
        folds_raw = purged_walk_forward(values, params, folds=6, embargo=spec.max_hold if len(values) >= 6 else 0)
        folds = [{"mean_r": f.mean_r, "trade_count": f.trade_count,
                  "profit": f.mean_r * f.trade_count,
                  "profit_factor": f.profit_factor} for f in folds_raw]
        perm = 0.0
        if len(values):
            perm = shifted_signal_percentile(np.sign(values), np.abs(values), iterations=1000, seed=seed + trial)
        neighbors: list[float] = []
        for key in params:
            for factor in (0.9, 1.1):
                nearby = dict(params)
                nearby[key] = max(min(spec.parameter_ranges[key]), min(max(spec.parameter_ranges[key]), nearby[key] * factor))
                neighbor_plan = GENERATORS[spec.name](frame, nearby)
                neighbor_values = _r_values(_trades(frame, neighbor_plan), neighbor_plan.stop_distance)
                neighbors.append(float(neighbor_values.mean()) if len(neighbor_values) else -1.0)
        stressed = _r_values(_trades(frame, plan, 1.5), plan.stop_distance)
        stressed_2 = _r_values(_trades(frame, plan, 2.0), plan.stop_distance)
        returns = frame["close"].pct_change().rolling(50, min_periods=10).std()
        trade_vol = np.asarray([float(returns.iloc[min(t.exit_index, len(returns) - 1)]) for t in trades]) if trades else np.array([])
        regimes: dict[str, list[float]] = {}
        if len(values):
            terciles = np.nanquantile(trade_vol, [1 / 3, 2 / 3]) if len(trade_vol) else [0.0, 0.0]
            for name, mask in (("volatility_low", trade_vol <= terciles[0]),
                               ("volatility_mid", (trade_vol > terciles[0]) & (trade_vol <= terciles[1])),
                               ("volatility_high", trade_vol > terciles[1])):
                regimes[name] = values[mask].tolist()
            for year in sorted(set(frame.index.year)):
                year_values = [value for value, trade in zip(values, trades)
                               if frame.index[min(trade.exit_index, len(frame) - 1)].year == year]
                if year_values:
                    regimes[f"year_{year}"] = year_values
        gate = evaluate_candidate(values, folds, registry_trials=max(1, registry.trial_count()),
                                  permutation_percentile=perm, neighborhood=neighbors,
                                  stressed_trades=stressed, regimes=regimes)
        result = TrialOutcome(trial, spec.name, params, len(values), float(values.mean()) if len(values) else float("nan"),
                              gate, tuple(f["profit_factor"] for f in folds),
                              deflated_sharpe(values, max(1, registry.trial_count() + 1)) if len(values) > 1 else float("nan"),
                              float(sum(x > 0 for x in neighbors) / len(neighbors)) if neighbors else 0.0,
                              float(stressed.mean()) if len(stressed) else float("nan"),
                              float(stressed_2.mean()) if len(stressed_2) else float("nan"))
        outcomes.append(result)
        registry.record(strategy=spec.name, params=params, data_hash=input_hash,
                        code_hash_value=source_hash, results={"verdict": gate.verdict, "n": len(values),
                                                                  "mean_net_r": result.mean_net_r,
                                                                  "funnel": gate.funnel,
                                                                  "reasons": gate.reasons}, seed=seed + trial)
    return outcomes


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=200)
    args = parser.parse_args()
    frame = pd.read_parquet(args.input)
    if "timestamp" in frame.columns:
        frame = frame.set_index("timestamp")
    outcomes = run_search(frame.sort_index(), trials=args.trials)
    for result in outcomes:
        print(result.trial, result.hypothesis, result.n, result.mean_net_r, result.gate.verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
