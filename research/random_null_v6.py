"""Deterministic, strict-matching random-entry null for H4 v6."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.backtest_v6 import (
    V6BacktestConfig,
    V6SwapPolicy,
    V6Trade,
    _build_market,
    _simulate_v6_trade,
    validate_v6_frame,
)
from research.constants_v6 import (
    V6_NULL_SIMULATIONS,
    V6_RANDOM_SEED,
    V6Environment,
)
from research.costs_v6 import V6CostModel
from research.strategies_v5 import StrategySpec
from research.strategies_v6 import MAX_HOLD_H4, all_specs, build_plan_v6
from research.v6_safety import require_v6_run_ready
from research.validation_pipeline_v6 import V6_OOS_END_EXCLUSIVE, V6_OOS_START
from research.validation_v6 import RandomNullEvidence


@dataclass(frozen=True)
class V6NullProfile:
    direction: int
    stop_atr_multiple: float
    max_hold_bars: int


@dataclass(frozen=True)
class V6NullMatch:
    trade_count: bool
    direction_mix: bool
    stop_distance_distribution: bool
    holding_period: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.trade_count,
                self.direction_mix,
                self.stop_distance_distribution,
                self.holding_period,
            )
        )


def profile_from_trade(trade: V6Trade) -> V6NullProfile:
    if trade.direction not in (-1, 1):
        raise ValueError("candidate trade direction must be +1 or -1")
    if (
        not math.isfinite(trade.stop_distance)
        or not math.isfinite(trade.atr_entry)
        or trade.stop_distance <= 0
        or trade.atr_entry <= 0
    ):
        raise ValueError("candidate stop distance and ATR must be finite and positive")
    if not 1 <= trade.bars_held <= MAX_HOLD_H4:
        raise ValueError("candidate holding period is outside the frozen max hold")
    return V6NullProfile(
        trade.direction,
        trade.stop_distance / trade.atr_entry,
        trade.bars_held,
    )


def verify_v6_null_match(
    candidate_profiles: Sequence[V6NullProfile],
    simulation_trades: Sequence[V6Trade],
) -> V6NullMatch:
    simulated_profiles: list[V6NullProfile] = []
    for trade in simulation_trades:
        profile = profile_from_trade(trade)
        simulated_profiles.append(
            V6NullProfile(
                profile.direction,
                profile.stop_atr_multiple,
                trade.max_hold_bars,
            )
        )

    trade_count = len(candidate_profiles) == len(simulation_trades)
    candidate_directions = sorted(profile.direction for profile in candidate_profiles)
    simulated_directions = sorted(profile.direction for profile in simulated_profiles)
    direction_mix = trade_count and candidate_directions == simulated_directions

    candidate_stops = sorted(
        round(profile.stop_atr_multiple, 10) for profile in candidate_profiles
    )
    simulated_stops = sorted(
        round(profile.stop_atr_multiple, 10) for profile in simulated_profiles
    )
    stop_distribution = trade_count and candidate_stops == simulated_stops

    candidate_holds = sorted(profile.max_hold_bars for profile in candidate_profiles)
    simulated_holds = sorted(trade.bars_held for trade in simulation_trades)
    holding_period = trade_count and candidate_holds == simulated_holds
    return V6NullMatch(
        trade_count,
        direction_mix,
        stop_distribution,
        holding_period,
    )


def _generate_v6_random_entry_null(
    frame: pd.DataFrame,
    *,
    spec: StrategySpec,
    candidate_trades: Sequence[V6Trade],
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
) -> RandomNullEvidence:
    validate_v6_frame(frame)
    frozen = {candidate.config_id: candidate for candidate in all_specs()}
    expected = frozen.get(spec.config_id)
    if expected is None or expected.params != spec.params or expected.family != spec.family:
        raise ValueError("random-entry null requires an unchanged frozen configuration")
    if config.stress_multiplier != cost_model.stress_multiplier:
        raise ValueError("null cost model and execution stress multipliers must match")
    if config.stress_multiplier != 1.0:
        raise ValueError("the matched random-entry null uses base 1.0x costs")
    if any(
        trade.config_id != spec.config_id
        or trade.environment is not V6Environment.BACKTEST
        for trade in candidate_trades
    ):
        raise ValueError("null candidate trades must be BACKTEST results for this config")
    plan = build_plan_v6(frame, spec)
    market = _build_market(frame, plan.atr, config)
    selected = [
        trade
        for trade in candidate_trades
        if V6_OOS_START <= trade.entry_time < V6_OOS_END_EXCLUSIVE
    ]
    profiles = tuple(profile_from_trade(trade) for trade in selected)
    start_index = max(
        int(frame.index.searchsorted(V6_OOS_START)),
        plan.warmup + 1,
    )
    end_index = min(
        int(frame.index.searchsorted(V6_OOS_END_EXCLUSIVE, side="left")),
        len(frame) - MAX_HOLD_H4 - 1,
    )
    pool = np.arange(start_index, max(start_index, end_index), dtype=int)

    rng = np.random.default_rng(V6_RANDOM_SEED)
    means: list[float | None] = []
    all_matches: list[V6NullMatch] = []
    for _ in range(V6_NULL_SIMULATIONS):
        if not profiles or len(pool) < len(profiles):
            means.append(None)
            all_matches.append(V6NullMatch(False, False, False, False))
            continue
        entries = np.sort(rng.choice(pool, size=len(profiles), replace=False))
        shuffled_profiles = tuple(
            profiles[int(position)]
            for position in rng.permutation(len(profiles))
        )
        if any(
            int(entries[position])
            <= int(entries[position - 1]) + shuffled_profiles[position - 1].max_hold_bars
            for position in range(1, len(entries))
        ):
            means.append(None)
            all_matches.append(V6NullMatch(False, False, False, False))
            continue

        generated: list[V6Trade] = []
        for entry_index, profile in zip(entries, shuffled_profiles, strict=True):
            signal_index = int(entry_index) - 1
            atr = float(market.atr[signal_index])
            if not math.isfinite(atr) or atr <= 0:
                break
            generated.append(
                _simulate_v6_trade(
                    market,
                    signal_index=signal_index,
                    entry_index=int(entry_index),
                    direction=profile.direction,
                    stop_distance=profile.stop_atr_multiple * atr,
                    trail_atr=float(plan.trail_atr or 0.0),
                    max_hold=profile.max_hold_bars,
                    allow_matched_null_horizon=True,
                    equity_usd=config.starting_balance_usd,
                    spec_config_id=f"{spec.config_id}-NULL",
                    config=config,
                    cost_model=cost_model,
                    swap_policy=swap_policy,
                )
            )
        if len(generated) != len(profiles):
            means.append(None)
            all_matches.append(V6NullMatch(False, False, False, False))
            continue
        match = verify_v6_null_match(profiles, generated)
        all_matches.append(match)
        means.append(
            float(np.mean([trade.net_r for trade in generated]))
            if match.passed
            else None
        )

    valid_count = sum(value is not None and math.isfinite(value) for value in means)
    return RandomNullEvidence(
        config_id=spec.config_id,
        environment=V6Environment.BACKTEST,
        simulation_count=V6_NULL_SIMULATIONS,
        seed=V6_RANDOM_SEED,
        valid_simulation_count=valid_count,
        simulation_means_net_r=tuple(means),
        trade_count_matched=bool(all(match.trade_count for match in all_matches)),
        direction_mix_matched=bool(all(match.direction_mix for match in all_matches)),
        stop_distance_distribution_matched=bool(
            all(match.stop_distance_distribution for match in all_matches)
        ),
        holding_period_matched=bool(all(match.holding_period for match in all_matches)),
    )


def generate_v6_random_entry_null(
    frame: pd.DataFrame,
    *,
    spec: StrategySpec,
    candidate_trades: Sequence[V6Trade],
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None = None,
) -> RandomNullEvidence:
    """Generate exactly 500 fixed-seed attempts after readiness is approved."""
    require_v6_run_ready()
    if swap_policy is None:
        raise ValueError("an approved swap/rollover policy is required for the null")
    return _generate_v6_random_entry_null(
        frame,
        spec=spec,
        candidate_trades=candidate_trades,
        config=config,
        cost_model=cost_model,
        swap_policy=swap_policy,
    )
