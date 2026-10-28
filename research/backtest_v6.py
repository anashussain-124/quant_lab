"""Fail-closed H4 v6 backtest mechanics for synthetic verification."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, Decimal
from typing import Protocol

import numpy as np
import pandas as pd

from research.constants_v6 import V6Environment
from research.costs_v5 import CostModel as SpreadFallbackModel
from research.costs_v5 import effective_spread_points
from research.costs_v6 import V6CostModel, V6CostSemanticsBlockedError
from research.strategies_v5 import StrategySpec
from research.strategies_v6 import all_specs, build_plan_v6
from research.terminal_policy_v6 import assess_terminal_entry
from research.v6_safety import require_v6_run_ready


class V6ExecutionBlockedError(RuntimeError):
    """The v6 execution mechanics lack a human-approved required policy."""


class V6PositionSizeUnavailableError(RuntimeError):
    """The frozen account risk budget cannot fund the minimum lot."""


class V6SwapPolicy(Protocol):
    def credit_price_per_ounce(
        self,
        *,
        direction: int,
        entry_time: pd.Timestamp,
        exit_time: pd.Timestamp,
        stress_multiplier: float,
    ) -> float: ...


@dataclass(frozen=True)
class V6BacktestConfig:
    point_size: float
    contract_size_ounces_per_lot: float
    starting_balance_usd: float = 5_000.0
    risk_fraction: float = 0.005
    stress_multiplier: float = 1.0
    min_volume_lots: Decimal = Decimal("0.01")
    volume_step_lots: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        numeric = (
            self.point_size,
            self.contract_size_ounces_per_lot,
            self.starting_balance_usd,
            self.risk_fraction,
            self.stress_multiplier,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("backtest configuration values must be finite")
        if self.point_size <= 0 or self.contract_size_ounces_per_lot <= 0:
            raise ValueError("point size and contract size must be positive")
        if self.starting_balance_usd <= 0:
            raise ValueError("starting balance must be positive")
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk fraction must be in (0, 1]")
        if self.stress_multiplier not in (1.0, 1.5):
            raise ValueError("only the frozen base and 1.5x stress are allowed")
        if self.min_volume_lots <= 0 or self.volume_step_lots <= 0:
            raise ValueError("volume minimum and step must be positive")


@dataclass(frozen=True)
class V6SkippedSignal:
    signal_index: int
    signal_time: pd.Timestamp
    reason: str


@dataclass(frozen=True)
class V6Trade:
    environment: V6Environment
    config_id: str
    direction: int
    signal_index: int
    entry_index: int
    exit_index: int
    max_hold_bars: int
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_distance: float
    atr_entry: float
    volume_lots: float
    risk_usd: float
    gross_r: float
    spread_slippage_r: float
    commission_r: float
    swap_r: float
    net_r: float
    cost_stress_multiplier: float
    gross_pnl_usd: float
    spread_slippage_usd: float
    commission_usd: float
    swap_usd: float
    net_pnl_usd: float
    equity_after_usd: float
    reason: str
    bars_held: int
    open_fill: bool


@dataclass(frozen=True)
class V6EquityPoint:
    time: pd.Timestamp
    equity_usd: float
    cumulative_net_r: float
    drawdown_usd: float


@dataclass(frozen=True)
class V6BacktestResult:
    environment: V6Environment
    config_id: str
    cost_stress_multiplier: float
    trades: tuple[V6Trade, ...]
    skipped_signals: tuple[V6SkippedSignal, ...]
    equity_curve: tuple[V6EquityPoint, ...]


@dataclass(frozen=True)
class V6PerturbationBacktest:
    base_config_id: str
    parameter: str
    direction: str
    parameters: Mapping[str, object]
    result: V6BacktestResult


@dataclass(frozen=True)
class _Market:
    index: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    spread_points: np.ndarray


def validate_v6_frame(frame: pd.DataFrame) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("v6 data requires a DatetimeIndex")
    if frame.index.tz is None or str(frame.index.tz) != "UTC":
        raise ValueError("v6 data index must be timezone-aware UTC")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("v6 data timestamps must be unique and increasing")
    required = ("open", "high", "low", "close", "spread")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"v6 data is missing required columns: {missing}")
    if frame.empty:
        raise ValueError("v6 data frame is empty")

    values = frame.loc[:, required].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("v6 OHLC and spread values must be finite")
    if (values[:, :4] <= 0).any():
        raise ValueError("v6 OHLC prices must be positive")
    if (values[:, 4] < 0).any():
        raise ValueError("spread must be non-negative")

    open_values, high_values, low_values, close_values = (
        frame[name].to_numpy(dtype=float) for name in required[:4]
    )
    if (
        (high_values < np.maximum.reduce((open_values, close_values, low_values))).any()
        or (low_values > np.minimum.reduce((open_values, close_values, high_values))).any()
    ):
        raise ValueError("v6 OHLC values violate candle bounds")


def _utc_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("v6 data requires a DatetimeIndex")
    if frame.index.tz is None or str(frame.index.tz) != "UTC":
        raise ValueError("v6 data index must be timezone-aware UTC")
    return frame.index


def _build_market(
    frame: pd.DataFrame,
    atr: np.ndarray,
    config: V6BacktestConfig,
) -> _Market:
    validate_v6_frame(frame)
    fallback = SpreadFallbackModel(
        point=config.point_size,
        contract_size=config.contract_size_ounces_per_lot,
        commission_round_turn=10.0,
        slippage_points=0.5,
        spread_floor_points=10.0,
        spread_median_window=100,
    )
    return _Market(
        index=_utc_index(frame),
        open=frame["open"].to_numpy(dtype=float),
        high=frame["high"].to_numpy(dtype=float),
        low=frame["low"].to_numpy(dtype=float),
        close=frame["close"].to_numpy(dtype=float),
        atr=np.asarray(atr, dtype=float),
        spread_points=effective_spread_points(frame, fallback),
    )


def _floor_volume(
    equity_usd: float,
    stop_distance: float,
    config: V6BacktestConfig,
) -> tuple[Decimal, float]:
    risk_budget = Decimal(str(equity_usd)) * Decimal(str(config.risk_fraction))
    per_lot_risk = (
        Decimal(str(stop_distance))
        * Decimal(str(config.contract_size_ounces_per_lot))
    )
    if risk_budget <= 0 or per_lot_risk <= 0:
        return Decimal("0"), 0.0
    steps = (risk_budget / per_lot_risk / config.volume_step_lots).to_integral_value(
        rounding=ROUND_FLOOR
    )
    volume = steps * config.volume_step_lots
    if volume < config.min_volume_lots:
        return Decimal("0"), float(risk_budget)
    risk_usd = float(volume * per_lot_risk)
    return volume, risk_usd


def _simulate_v6_trade(
    market: _Market,
    *,
    signal_index: int,
    entry_index: int,
    direction: int,
    stop_distance: float,
    trail_atr: float,
    max_hold: int,
    allow_matched_null_horizon: bool,
    equity_usd: float,
    spec_config_id: str,
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
) -> V6Trade:
    if swap_policy is None:
        raise V6ExecutionBlockedError(
            "no approved H4 rollover/stop-ordering policy is installed"
        )
    if direction not in (-1, 1):
        raise ValueError("direction must be +1 or -1")
    if (
        isinstance(max_hold, bool)
        or not isinstance(max_hold, int)
        or not 1 <= max_hold <= 120
    ):
        raise ValueError("maximum hold must be an integer from 1 through 120")
    if max_hold != 120 and not allow_matched_null_horizon:
        raise ValueError("base v6 execution requires the frozen 120-bar maximum hold")
    if not math.isfinite(stop_distance) or stop_distance <= 0:
        raise ValueError("stop distance must be finite and positive")
    if not math.isfinite(trail_atr) or trail_atr <= 0:
        raise ValueError("trailing ATR multiple must be finite and positive")
    if entry_index != signal_index + 1:
        raise ValueError("entry must occur at the next H4 bar open")
    if signal_index < 0 or entry_index >= len(market.index):
        raise ValueError("signal and entry indexes must lie within the market frame")
    if entry_index + max_hold >= len(market.index):
        raise ValueError("next-open exit after the full holding horizon is unavailable")
    if cost_model.stress_multiplier != config.stress_multiplier:
        raise ValueError("cost model and execution stress multipliers must match")

    entry_price = float(market.open[entry_index])
    atr_entry = float(market.atr[signal_index])
    if not math.isfinite(entry_price) or not math.isfinite(atr_entry) or atr_entry <= 0:
        raise ValueError("entry price and signal-bar ATR must be finite and positive")
    volume_decimal, risk_usd = _floor_volume(equity_usd, stop_distance, config)
    if volume_decimal <= 0:
        raise V6PositionSizeUnavailableError(
            "minimum 0.01-lot position exceeds the configured risk budget"
        )
    volume = float(volume_decimal)
    active_stop = entry_price - direction * stop_distance
    best_close = entry_price
    planned_exit = entry_index + max_hold
    exit_index: int | None = None
    exit_price = 0.0
    reason = "time"
    open_fill = True

    for bar_index in range(entry_index, planned_exit):
        bar_open = float(market.open[bar_index])
        bar_high = float(market.high[bar_index])
        bar_low = float(market.low[bar_index])
        gap_hit = bar_open <= active_stop if direction > 0 else bar_open >= active_stop
        stop_hit = bar_low <= active_stop if direction > 0 else bar_high >= active_stop
        if gap_hit or stop_hit:
            exit_index = bar_index
            exit_price = bar_open if gap_hit else active_stop
            reason = "stop"
            open_fill = gap_hit
            break

        close = float(market.close[bar_index])
        atr = float(market.atr[bar_index])
        if math.isfinite(atr) and atr > 0:
            if direction > 0:
                best_close = max(best_close, close)
                active_stop = max(active_stop, best_close - trail_atr * atr)
            else:
                best_close = min(best_close, close)
                active_stop = min(active_stop, best_close + trail_atr * atr)

    if exit_index is None:
        exit_index = planned_exit
        exit_price = float(market.open[planned_exit])
    if not math.isfinite(exit_price) or exit_price <= 0:
        raise ValueError("exit fill price must be finite and positive")

    entry_spread = float(market.spread_points[entry_index])
    exit_spread = float(market.spread_points[exit_index])
    cost = cost_model.non_swap_cost(
        spread_entry_points=entry_spread,
        spread_exit_points=exit_spread,
        point_size=config.point_size,
        contract_size_ounces_per_lot=config.contract_size_ounces_per_lot,
    )
    swap_credit = swap_policy.credit_price_per_ounce(
        direction=direction,
        entry_time=market.index[entry_index],
        exit_time=market.index[exit_index],
        stress_multiplier=config.stress_multiplier,
    )
    if not math.isfinite(swap_credit):
        raise ValueError("swap policy must return a finite signed price-per-ounce value")

    gross_r = direction * (exit_price - entry_price) / stop_distance
    spread_slippage_r = (
        cost.spread_price_per_ounce + cost.slippage_price_per_ounce
    ) / stop_distance
    commission_r = cost.commission_price_per_ounce / stop_distance
    swap_r = swap_credit / stop_distance
    net_r = gross_r - spread_slippage_r - commission_r + swap_r
    contract_volume = config.contract_size_ounces_per_lot * volume
    gross_usd = direction * (exit_price - entry_price) * contract_volume
    spread_slippage_usd = (
        cost.spread_price_per_ounce + cost.slippage_price_per_ounce
    ) * contract_volume
    commission_usd = cost.commission_price_per_ounce * contract_volume
    swap_usd = swap_credit * contract_volume
    net_usd = gross_usd - spread_slippage_usd - commission_usd + swap_usd
    if not all(
        math.isfinite(value)
        for value in (
            gross_r,
            spread_slippage_r,
            commission_r,
            swap_r,
            net_r,
            gross_usd,
            spread_slippage_usd,
            commission_usd,
            swap_usd,
            net_usd,
        )
    ):
        raise ValueError("v6 trade accounting produced a non-finite value")

    return V6Trade(
        environment=V6Environment.BACKTEST,
        config_id=spec_config_id,
        direction=direction,
        signal_index=signal_index,
        entry_index=entry_index,
        exit_index=exit_index,
        max_hold_bars=max_hold,
        signal_time=market.index[signal_index],
        entry_time=market.index[entry_index],
        exit_time=market.index[exit_index],
        entry_price=entry_price,
        exit_price=exit_price,
        stop_distance=stop_distance,
        atr_entry=atr_entry,
        volume_lots=volume,
        risk_usd=risk_usd,
        gross_r=gross_r,
        spread_slippage_r=spread_slippage_r,
        commission_r=commission_r,
        swap_r=swap_r,
        net_r=net_r,
        cost_stress_multiplier=config.stress_multiplier,
        gross_pnl_usd=gross_usd,
        spread_slippage_usd=spread_slippage_usd,
        commission_usd=commission_usd,
        swap_usd=swap_usd,
        net_pnl_usd=net_usd,
        equity_after_usd=equity_usd + net_usd,
        reason=reason,
        bars_held=max(1, exit_index - entry_index),
        open_fill=open_fill,
    )


def _simulate_v6_plan(
    frame: pd.DataFrame,
    *,
    spec: StrategySpec,
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
    allow_perturbation: bool = False,
) -> V6BacktestResult:
    frozen = {candidate.config_id: candidate for candidate in all_specs()}
    expected = frozen.get(spec.config_id)
    if expected is None or expected.family != spec.family:
        raise ValueError("only frozen v6 configurations and their approved variants are allowed")
    if expected.params != spec.params:
        if not allow_perturbation:
            raise ValueError("only the exact 24 frozen v6 specifications may be simulated")
        from research.validation_v6 import build_v6_perturbations

        approved = build_v6_perturbations(expected)
        if not any(dict(variant.parameters) == spec.params for variant in approved):
            raise ValueError("specification is outside the approved perturbation set")
    if cost_model.stress_multiplier != config.stress_multiplier:
        raise ValueError("cost model and execution stress multipliers must match")
    plan = build_plan_v6(frame, spec)
    market = _build_market(frame, plan.atr, config)
    equity = config.starting_balance_usd
    cumulative_net_r = 0.0
    peak_equity = equity
    trades: list[V6Trade] = []
    skipped: list[V6SkippedSignal] = []
    curve: list[V6EquityPoint] = [
        V6EquityPoint(frame.index[0], equity, cumulative_net_r, 0.0)
    ]
    signal_index = plan.warmup

    while signal_index < len(frame) - 1:
        if plan.long_signal[signal_index]:
            direction = 1
        elif plan.short_signal[signal_index]:
            direction = -1
        else:
            signal_index += 1
            continue

        eligibility = assess_terminal_entry(signal_index, _utc_index(frame))
        if not eligibility.eligible:
            skipped.append(
                V6SkippedSignal(
                    signal_index,
                    frame.index[signal_index],
                    eligibility.skip_reason or "terminal_entry_not_eligible",
                )
            )
            signal_index += 1
            continue

        atr_entry = float(plan.atr[signal_index])
        if not math.isfinite(atr_entry) or atr_entry <= 0:
            skipped.append(
                V6SkippedSignal(signal_index, frame.index[signal_index], "invalid_signal_atr")
            )
            signal_index += 1
            continue
        try:
            trade = _simulate_v6_trade(
                market,
                signal_index=signal_index,
                entry_index=signal_index + 1,
                direction=direction,
                stop_distance=plan.stop_atr * atr_entry,
                trail_atr=plan.trail_atr or 0.0,
                max_hold=plan.max_hold,
                allow_matched_null_horizon=False,
                equity_usd=equity,
                spec_config_id=plan.spec.config_id,
                config=config,
                cost_model=cost_model,
                swap_policy=swap_policy,
            )
        except V6PositionSizeUnavailableError:
            skipped.append(
                V6SkippedSignal(
                    signal_index,
                    frame.index[signal_index],
                    "minimum_lot_exceeds_risk_budget",
                )
            )
            signal_index += 1
            continue
        trades.append(trade)
        equity = trade.equity_after_usd
        cumulative_net_r += trade.net_r
        peak_equity = max(peak_equity, equity)
        curve.append(
            V6EquityPoint(
                trade.exit_time,
                equity,
                cumulative_net_r,
                peak_equity - equity,
            )
        )
        signal_index = max(signal_index + 1, trade.exit_index)

    return V6BacktestResult(
        V6Environment.BACKTEST,
        plan.spec.config_id,
        config.stress_multiplier,
        tuple(trades),
        tuple(skipped),
        tuple(curve),
    )


def run_v6_configuration(
    frame: pd.DataFrame,
    *,
    spec: StrategySpec,
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None = None,
) -> V6BacktestResult:
    """Run one frozen configuration only after all protocol blockers are cleared."""
    require_v6_run_ready()
    if swap_policy is None:
        raise V6CostSemanticsBlockedError(
            "an approved H4 swap/stop-ordering policy is required"
        )
    return _simulate_v6_plan(
        frame,
        spec=spec,
        config=config,
        cost_model=cost_model,
        swap_policy=swap_policy,
    )


def run_v6_cost_stress(
    frame: pd.DataFrame,
    *,
    spec: StrategySpec,
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
) -> V6BacktestResult:
    """Run one frozen configuration under the preregistered 1.5x cost stress."""
    if config.stress_multiplier != 1.0 or cost_model.stress_multiplier != 1.0:
        raise ValueError("stress execution starts from the frozen 1.0x base model")
    return run_v6_configuration(
        frame,
        spec=spec,
        config=replace(config, stress_multiplier=1.5),
        cost_model=cost_model.stressed(),
        swap_policy=swap_policy,
    )


def run_v6_perturbation_scenarios(
    frame: pd.DataFrame,
    *,
    spec: StrategySpec,
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
) -> tuple[V6PerturbationBacktest, ...]:
    """Run only the preregistered per-configuration numeric perturbations."""
    require_v6_run_ready()
    if config.stress_multiplier != 1.0 or cost_model.stress_multiplier != 1.0:
        raise ValueError("perturbations use the frozen 1.0x base cost model")
    if swap_policy is None:
        raise V6ExecutionBlockedError(
            "an approved H4 swap/stop-ordering policy is required"
        )
    from research.validation_v6 import build_v6_perturbations

    variants = build_v6_perturbations(spec)
    results: list[V6PerturbationBacktest] = []
    for variant in variants:
        variant_spec = replace(spec, params=dict(variant.parameters))
        result = _simulate_v6_plan(
            frame,
            spec=variant_spec,
            config=config,
            cost_model=cost_model,
            swap_policy=swap_policy,
            allow_perturbation=True,
        )
        results.append(
            V6PerturbationBacktest(
                spec.config_id,
                variant.parameter,
                variant.direction,
                dict(variant.parameters),
                result,
            )
        )
    return tuple(results)


def run_v6_frozen_grid(
    frame: pd.DataFrame,
    *,
    config: V6BacktestConfig,
    cost_model: V6CostModel,
    swap_policy: V6SwapPolicy | None,
) -> tuple[V6BacktestResult, ...]:
    """Run the exact frozen 24 only after the protocol readiness guard clears."""
    require_v6_run_ready()
    expected_ids = tuple(
        [f"h4d{index:02d}" for index in range(1, 13)]
        + [f"h4v{index:02d}" for index in range(1, 13)]
    )
    specs = all_specs()
    actual_ids = tuple(spec.config_id for spec in specs)
    if actual_ids != expected_ids:
        raise ValueError("v6 strategy table differs from the frozen 24 configuration IDs")
    if swap_policy is None:
        raise V6ExecutionBlockedError(
            "an approved H4 swap/stop-ordering policy is required"
        )
    if cost_model.stress_multiplier != config.stress_multiplier:
        raise ValueError("cost model and execution stress multipliers must match")
    return tuple(
        _simulate_v6_plan(
            frame,
            spec=spec,
            config=config,
            cost_model=cost_model,
            swap_policy=swap_policy,
        )
        for spec in specs
    )


def max_drawdown_usd(equity_curve: Sequence[V6EquityPoint]) -> float:
    if not equity_curve:
        return 0.0
    equity = np.asarray([point.equity_usd for point in equity_curve], dtype=float)
    if not np.isfinite(equity).all():
        raise ValueError("equity curve values must be finite")
    return float(np.max(np.maximum.accumulate(equity) - equity))
