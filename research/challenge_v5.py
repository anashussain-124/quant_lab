"""Two-phase FundingPips challenge-path simulator for the frozen v5 trials.

This module does not search for a strategy. It takes the already-frozen v5 trade
log and asks what would have happened if those exact trades had been sized and
stopped under FundingPips 2 Step Standard rules.

Environment label: BACKTEST. Historical bars are real MT5-demo data; every fill,
challenge account, and rule check here is simulated. A simulated pass is not a
FundingPips pass.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import platform
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DAY_OFFSET_HOURS = 21
INACTIVITY_DAYS = 30
CONTRACT_SIZE = 100.0
VOLUME_STEP = 0.01
MIN_VOLUME = 0.01
MAX_VOLUME = 100.0
METALS_LEVERAGE = 30.0
V5_COMMISSION_ROUND_TURN = 7.0
EPS = 1e-9
ENVIRONMENT = "BACKTEST"


def fp_day_id(ts: pd.Timestamp, reset_hour_utc: int = DAY_OFFSET_HOURS) -> str:
    """Shifted FundingPips day label; reset occurs at `reset_hour_utc` UTC.

    The label is the UTC date of the reset that opened the session, matching
    the compliance engine's convention. It is a label only; the reset boundary
    is what matters.
    """
    return (ts - timedelta(hours=reset_hour_utc)).date().isoformat()


def next_reset_after(
    ts: pd.Timestamp, reset_hour_utc: int = DAY_OFFSET_HOURS
) -> pd.Timestamp:
    """First reset at `reset_hour_utc` UTC strictly after `ts`."""
    candidate = pd.Timestamp(
        datetime.combine(ts.date(), time(reset_hour_utc), tzinfo=UTC)
    )
    if candidate <= ts:
        candidate += pd.Timedelta(days=1)
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


@dataclass(frozen=True)
class ChallengeRules:
    initial_balance: float = 5_000.0
    phase_1_target_pct: float = 0.08
    phase_2_target_pct: float = 0.05
    overall_loss_pct: float = 0.10
    inactivity_days: int = 30
    daily_reset_hour_utc: int = 21
    phase_2_resets_to: float = 5_000.0

    def __post_init__(self) -> None:
        if self.initial_balance <= 0 or self.phase_2_resets_to <= 0:
            raise ValueError("balances must be positive")
        for value in (
            self.phase_1_target_pct,
            self.phase_2_target_pct,
            self.overall_loss_pct,
        ):
            if not 0 < value < 1:
                raise ValueError("percentages must be in (0, 1)")
        if not 0 <= self.daily_reset_hour_utc <= 23:
            raise ValueError("daily reset hour must be 0..23")
        if self.inactivity_days <= 0:
            raise ValueError("inactivity days must be positive")


@dataclass(frozen=True)
class Bars:
    index: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread_price: np.ndarray

    def __len__(self) -> int:
        return len(self.index)


@dataclass(frozen=True)
class TradePath:
    config_id: str
    entry_idx: int
    exit_idx: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry_price: float
    exit_price: float
    stop_distance: float
    gross_r: float
    cost_r: float
    swap_r: float
    reason: str
    bars_held: int


@dataclass(frozen=True)
class Scenario:
    name: str
    daily_loss_pct: float
    min_trading_days: int
    carry_phase_2: bool
    internal_soft_guards: bool
    daily_soft_guard_pct: float = 0.01
    overall_soft_guard_pct: float = 0.04

    def __post_init__(self) -> None:
        if not 0 < self.daily_loss_pct < 1:
            raise ValueError("daily loss pct must be in (0, 1)")
        if self.min_trading_days < 0:
            raise ValueError("min trading days cannot be negative")
        if not 0 < self.daily_soft_guard_pct < 1:
            raise ValueError("daily soft guard pct must be in (0, 1)")
        if not 0 < self.overall_soft_guard_pct < 1:
            raise ValueError("overall soft guard pct must be in (0, 1)")


@dataclass(frozen=True)
class CostCase:
    name: str
    fp_commission_round_turn_usd: float
    stress: float

    def __post_init__(self) -> None:
        if self.fp_commission_round_turn_usd < 0:
            raise ValueError("commission cannot be negative")
        if self.stress <= 0:
            raise ValueError("stress must be positive")


@dataclass
class PhaseState:
    number: int
    start_balance: float
    balance: float
    day_id: str
    daily_reference: float
    trading_days: set[str]
    phase_start_time: pd.Timestamp
    peak_equity: float
    max_dd_usd: float = 0.0
    max_dd_pct: float = 0.0


@dataclass
class AttemptResult:
    environment: str
    config_id: str
    risk_fraction: float
    scenario: str
    cost_case: str
    start_utc: str
    outcome: str
    phase_reached: int
    phase1_passed: bool
    phase2_passed: bool
    phase2_start_balance: float | None
    days_to_pass: float | None
    end_utc: str | None
    trades_taken: int
    skipped_min_lot: int
    skipped_room: int
    max_dd_usd: float
    max_dd_pct: float
    min_overall_room_usd: float | None
    min_daily_room_usd: float | None
    near_overall: bool
    near_daily: bool
    breach_daily: bool
    breach_overall: bool
    soft_guard: bool
    inactivity: bool
    incomplete: bool
    detail: str


@dataclass
class LoadedInputs:
    config: dict[str, Any]
    rules: ChallengeRules
    trial_report: dict[str, Any]
    trades_by_config: dict[str, list[TradePath]]
    bars: Bars
    trade_log_sha256: str
    data_sha256: str
    config_sha256: str


def _load_bars(data_path: Path) -> Bars:
    frame = pd.read_parquet(data_path)
    frame = frame.sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise RuntimeError("Market data index is not a DatetimeIndex")
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    if frame.index.has_duplicates:
        raise RuntimeError("Market data index contains duplicates")
    if not frame.index.is_monotonic_increasing:
        raise RuntimeError("Market data index is not increasing")
    required = ["open", "high", "low", "close", "spread"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise RuntimeError(f"Market data missing columns: {missing}")

    arrays = {
        column: frame[column].to_numpy(dtype=float) for column in required
    }
    for column, values in arrays.items():
        if not np.all(np.isfinite(values)):
            raise RuntimeError(f"Market data {column} contains non-finite values")
    if np.any(arrays["high"] < arrays["low"]):
        raise RuntimeError("Market data has high < low")
    if np.any(arrays["high"] < np.maximum(arrays["open"], arrays["close"])):
        raise RuntimeError("Market data has high below open/close")
    if np.any(arrays["low"] > np.minimum(arrays["open"], arrays["close"])):
        raise RuntimeError("Market data has low above open/close")
    if np.any(arrays["spread"] < 0):
        raise RuntimeError("Market data has negative spread")

    return Bars(
        index=frame.index,
        open=arrays["open"],
        high=arrays["high"],
        low=arrays["low"],
        close=arrays["close"],
        spread_price=np.maximum(arrays["spread"], 10.0) * 0.01,
    )


def _load_trades(
    trade_log_path: Path,
    bars: Bars,
    report_ids: set[str],
    evaluation_end: pd.Timestamp,
) -> dict[str, list[TradePath]]:
    trades = pd.read_csv(trade_log_path)
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True)

    log_ids = set(trades["config_id"].unique())
    if log_ids != report_ids:
        raise RuntimeError("Trade log config set does not match the 50-trial report")
    if trades["entry_time"].min() >= pd.Timestamp("2023-01-01", tz="UTC"):
        raise RuntimeError("Trade log starts in the sealed holdout")
    if trades["exit_time"].max() > evaluation_end:
        raise RuntimeError("Trade log exits after the frozen evaluation window")
    if trades["entry_time"].max() >= pd.Timestamp("2023-01-01", tz="UTC"):
        raise RuntimeError("Trade log contains sealed-holdout entries")

    needed = [
        "config_id",
        "direction",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "stop_distance",
        "gross_r",
        "cost_r",
        "swap_r",
        "net_r",
        "reason",
        "bars_held",
    ]
    missing = [column for column in needed if column not in trades.columns]
    if missing:
        raise RuntimeError(f"Trade log missing columns: {missing}")
    if trades.empty:
        raise RuntimeError("Trade log is empty")

    net_identity = (
        trades["gross_r"] - trades["cost_r"] + trades["swap_r"] - trades["net_r"]
    ).abs()
    tolerance = 1e-9 * np.maximum(1.0, trades["net_r"].abs())
    if bool((net_identity > tolerance).any()):
        raise RuntimeError("Trade log net_r is not gross_r - cost_r + swap_r")

    if not trades["entry_time"].is_monotonic_increasing:
        trades = trades.sort_values(["config_id", "entry_time"]).reset_index(drop=True)

    by_config: dict[str, list[TradePath]] = defaultdict(list)
    index_values = bars.index
    for row in trades.itertuples(index=False):
        entry_idx = int(index_values.searchsorted(row.entry_time))
        exit_idx = int(index_values.searchsorted(row.exit_time))
        if entry_idx >= len(bars) or exit_idx >= len(bars):
            raise RuntimeError(
                f"Trade outside market data: {row.config_id} "
                f"{row.entry_time} -> {row.exit_time}"
            )
        if index_values[entry_idx] != row.entry_time:
            raise RuntimeError(
                f"Entry time not on an H1 bar: {row.config_id} {row.entry_time}"
            )
        if index_values[exit_idx] != row.exit_time:
            raise RuntimeError(
                f"Exit time not on an H1 bar: {row.config_id} {row.exit_time}"
            )
        if exit_idx < entry_idx:
            raise RuntimeError(f"Exit before entry: {row.config_id}")
        if int(row.direction) not in (-1, 1):
            raise RuntimeError(f"Invalid direction for {row.config_id}")
        if not (math.isfinite(row.stop_distance) and row.stop_distance > 0):
            raise RuntimeError(
                f"Invalid stop distance for {row.config_id} at {row.entry_time}"
            )
        for field in ("gross_r", "cost_r", "swap_r", "net_r"):
            if not math.isfinite(float(getattr(row, field))):
                raise RuntimeError(
                    f"Non-finite {field} for {row.config_id} at {row.entry_time}"
                )
        if float(row.cost_r) < 0:
            raise RuntimeError(f"Negative cost_r for {row.config_id}")

        by_config[row.config_id].append(
            TradePath(
                config_id=row.config_id,
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                entry_time=row.entry_time,
                exit_time=row.exit_time,
                direction=int(row.direction),
                entry_price=float(row.entry_price),
                exit_price=float(row.exit_price),
                stop_distance=float(row.stop_distance),
                gross_r=float(row.gross_r),
                cost_r=float(row.cost_r),
                swap_r=float(row.swap_r),
                reason=str(row.reason),
                bars_held=int(row.bars_held),
            )
        )

    if set(by_config) != report_ids:
        raise RuntimeError("Some frozen configs have no trades")
    for config_id, paths in by_config.items():
        paths.sort(key=lambda item: item.entry_time)
        previous_exit = None
        for path in paths:
            if previous_exit is not None and path.entry_time < previous_exit:
                raise RuntimeError(f"Overlapping trades in {config_id}")
            if path.exit_time > evaluation_end:
                raise RuntimeError(f"Trade exits after evaluation end in {config_id}")
            previous_exit = path.exit_time
    return dict(by_config)


def load_inputs(config_path: Path) -> LoadedInputs:
    import yaml

    config_text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(config_text)
    sim = config["challenge_simulation"]
    prop = config["prop_firm"]
    project_root = config_path.parent.parent

    trade_log_path = project_root / sim["trade_log"]
    data_path = project_root / sim["market_data"]
    report_path = project_root / sim["trial_report"]

    data_sha = sha256_file(data_path)
    if data_sha != sim["market_data_sha256"]:
        raise RuntimeError(
            f"Market data hash mismatch: {data_sha} != {sim['market_data_sha256']}"
        )
    trade_log_sha = sha256_file(trade_log_path)
    if trade_log_sha != sim["trade_log_sha256"]:
        raise RuntimeError(
            f"Trade log hash mismatch: {trade_log_sha} != {sim['trade_log_sha256']}"
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("holdout_accessed") is not False:
        raise RuntimeError("Trial report says the holdout was accessed; refusing")
    if report.get("verdict") != "NO_EDGE_FOUND":
        raise RuntimeError(
            f"Unexpected v5 verdict {report.get('verdict')!r}; this simulator "
            "is only for the frozen NO_EDGE_FOUND run"
        )
    report_ids = {t["config_id"] for t in report["trials"]}
    if len(report_ids) != 50:
        raise RuntimeError(f"Expected 50 frozen trials, got {len(report_ids)}")

    rules_cfg = prop["rules"]
    initial_balance = float(prop["initial_balance"])
    account_sizes = tuple(float(x) for x in rules_cfg["account_sizes_usd"])
    if initial_balance not in account_sizes:
        raise RuntimeError(
            f"Initial balance {initial_balance} is not a verified FundingPips size"
        )
    rules = ChallengeRules(
        initial_balance=initial_balance,
        phase_1_target_pct=float(rules_cfg["phase_1_target_pct"]),
        phase_2_target_pct=float(rules_cfg["phase_2_target_pct"]),
        overall_loss_pct=float(rules_cfg["max_overall_loss_pct"]),
        inactivity_days=int(rules_cfg["inactivity_max_days"]),
        daily_reset_hour_utc=int(rules_cfg["daily_reset_utc_hour"]),
        phase_2_resets_to=float(sim["primary"]["phase_2_resets_to"]),
    )
    if int(rules_cfg["xauusd_contract_size"]) != int(CONTRACT_SIZE):
        raise RuntimeError("Config contract size does not match the simulator")
    if abs(float(rules_cfg["min_volume_lots"]) - MIN_VOLUME) > EPS:
        raise RuntimeError("Config min volume does not match the simulator")
    if abs(float(rules_cfg["volume_step_lots"]) - VOLUME_STEP) > EPS:
        raise RuntimeError("Config volume step does not match the simulator")
    if abs(float(rules_cfg["metals_leverage"]) - METALS_LEVERAGE) > EPS:
        raise RuntimeError("Config metals leverage does not match the simulator")

    bars = _load_bars(data_path)
    evaluation_end = _utc(sim["evaluation_end_utc"])
    by_config = _load_trades(trade_log_path, bars, report_ids, evaluation_end)

    return LoadedInputs(
        config=config,
        rules=rules,
        trial_report=report,
        trades_by_config=by_config,
        bars=bars,
        trade_log_sha256=trade_log_sha,
        data_sha256=data_sha,
        config_sha256=hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
    )


def _net_r(path: TradePath, cost: CostCase) -> float:
    # v5 cost_r already includes $7 round-turn commission. Replace the delta and
    # apply the same stress multiplier to every cost component, matching the
    # frozen v5 CostModel.stress semantics.
    commission_delta = (
        (cost.fp_commission_round_turn_usd - V5_COMMISSION_ROUND_TURN)
        * cost.stress
        / (CONTRACT_SIZE * path.stop_distance)
    )
    return (
        path.gross_r
        - path.cost_r * cost.stress
        + path.swap_r * cost.stress
        - commission_delta
    )


def _size_lots(
    equity: float,
    risk_fraction: float,
    stop_distance: float,
    entry_price: float,
    daily_room: float | None = None,
    overall_room: float | None = None,
) -> tuple[float, float, str | None]:
    """Return (lots, actual_risk_usd, skip_reason). Volume is always floored.

    The risk budget is the risk fraction of current equity, capped by the
    remaining daily and overall firm room. If the 0.01-lot minimum would exceed
    that budget, the trade is skipped rather than resized upward.
    """
    if not (
        math.isfinite(equity)
        and math.isfinite(stop_distance)
        and math.isfinite(entry_price)
    ):
        return 0.0, 0.0, "INVALID_INPUT"
    if equity <= 0 or stop_distance <= 0 or entry_price <= 0:
        return 0.0, 0.0, "INVALID_INPUT"
    if not (math.isfinite(risk_fraction) and 0 < risk_fraction <= 1):
        return 0.0, 0.0, "INVALID_RISK_FRACTION"

    risk_budget = risk_fraction * equity
    min_lot_risk = MIN_VOLUME * stop_distance * CONTRACT_SIZE
    if risk_budget + EPS < min_lot_risk:
        return 0.0, 0.0, "MIN_LOT_EXCEEDS_BUDGET"

    budget = risk_budget
    if daily_room is not None:
        budget = min(budget, max(0.0, daily_room))
    if overall_room is not None:
        budget = min(budget, max(0.0, overall_room))
    if budget <= EPS:
        return 0.0, 0.0, "NO_ROOM"
    if budget + EPS < min_lot_risk:
        return 0.0, 0.0, "ROOM_BELOW_MIN_LOT"

    raw_steps = math.floor((budget / (stop_distance * CONTRACT_SIZE)) / VOLUME_STEP + EPS)
    if raw_steps < 1:
        return 0.0, 0.0, "ROOM_BELOW_MIN_LOT"

    max_margin_steps = math.floor(
        (equity * METALS_LEVERAGE / (entry_price * CONTRACT_SIZE)) / VOLUME_STEP + EPS
    )
    max_steps = min(
        raw_steps,
        max_margin_steps,
        int(math.floor(MAX_VOLUME / VOLUME_STEP + EPS)),
    )
    if max_steps < 1:
        return 0.0, 0.0, "INSUFFICIENT_MARGIN"

    lots = math.floor(max_steps * VOLUME_STEP / VOLUME_STEP + EPS) * VOLUME_STEP
    lots = round(lots, 2)
    if lots < MIN_VOLUME:
        return 0.0, 0.0, "INSUFFICIENT_MARGIN"
    actual_risk = lots * stop_distance * CONTRACT_SIZE
    if actual_risk > budget + 1e-6:
        return 0.0, 0.0, "SIZING_OVER_BUDGET"
    return lots, actual_risk, None


def _bar_extremes(
    bars: Bars,
    index: int,
    direction: int,
    entry_price: float,
    stop_distance: float,
) -> tuple[float, float]:
    """Worst and best floating R inside one bar.

    Bars are treated as bid prices. A long is marked at the bid; a short is
    marked at the ask (bid + observed spread). This is slightly conservative
    for shorts and matches the pre-registered worst-intrabar mark.
    """
    spread = float(bars.spread_price[index])
    if direction > 0:
        worst = (float(bars.low[index]) - entry_price) / stop_distance
        best = (float(bars.high[index]) - entry_price) / stop_distance
    else:
        worst = (entry_price - (float(bars.high[index]) + spread)) / stop_distance
        best = (entry_price - (float(bars.low[index]) + spread)) / stop_distance
    return worst, best


def _mark_equity(
    balance: float,
    direction: int,
    price: float,
    spread: float,
    entry_price: float,
    stop_distance: float,
    actual_risk: float,
) -> float:
    if direction > 0:
        floating_r = (price - entry_price) / stop_distance
    else:
        floating_r = (entry_price - (price + spread)) / stop_distance
    return balance + floating_r * actual_risk


def _finish(
    result: AttemptResult,
    outcome: str,
    end_time: pd.Timestamp | None,
    *,
    breach_daily: bool = False,
    breach_overall: bool = False,
    soft_guard: bool = False,
    inactivity: bool = False,
    incomplete: bool = False,
    detail: str = "",
) -> AttemptResult:
    result.outcome = outcome
    result.end_utc = end_time.isoformat() if end_time is not None else None
    result.breach_daily = breach_daily
    result.breach_overall = breach_overall
    result.soft_guard = soft_guard
    result.inactivity = inactivity
    result.incomplete = incomplete
    result.detail = detail
    return result


def simulate_attempt(
    paths: list[TradePath],
    start_index: int,
    bars: Bars,
    attempt_start: pd.Timestamp,
    horizon: pd.Timestamp,
    risk_fraction: float,
    scenario: Scenario,
    cost: CostCase,
    rules: ChallengeRules,
    *,
    config_id: str,
) -> AttemptResult:
    if attempt_start.tzinfo is None or horizon.tzinfo is None:
        raise ValueError("attempt times must be timezone-aware")
    if horizon <= attempt_start:
        raise ValueError("horizon must be after attempt start")

    phase = PhaseState(
        number=1,
        start_balance=rules.initial_balance,
        balance=rules.initial_balance,
        day_id=fp_day_id(attempt_start, rules.daily_reset_hour_utc),
        daily_reference=rules.initial_balance,
        trading_days=set(),
        phase_start_time=attempt_start,
        peak_equity=rules.initial_balance,
    )
    result = AttemptResult(
        environment=ENVIRONMENT,
        config_id=config_id,
        risk_fraction=risk_fraction,
        scenario=scenario.name,
        cost_case=cost.name,
        start_utc=attempt_start.isoformat(),
        outcome="INCOMPLETE",
        phase_reached=1,
        phase1_passed=False,
        phase2_passed=False,
        phase2_start_balance=None,
        days_to_pass=None,
        end_utc=None,
        trades_taken=0,
        skipped_min_lot=0,
        skipped_room=0,
        max_dd_usd=0.0,
        max_dd_pct=0.0,
        min_overall_room_usd=None,
        min_daily_room_usd=None,
        near_overall=False,
        near_daily=False,
        breach_daily=False,
        breach_overall=False,
        soft_guard=False,
        inactivity=False,
        incomplete=True,
        detail="",
    )

    near_overall_seen = False
    near_daily_seen = False

    def overall_floor() -> float:
        return phase.start_balance * (1.0 - rules.overall_loss_pct)

    def daily_floor() -> float:
        return phase.daily_reference * (1.0 - scenario.daily_loss_pct)

    def track_equity(equity: float) -> None:
        if equity > phase.peak_equity:
            phase.peak_equity = equity
        drawdown = phase.peak_equity - equity
        if drawdown > phase.max_dd_usd:
            phase.max_dd_usd = drawdown
            phase.max_dd_pct = (
                drawdown / phase.start_balance if phase.start_balance > 0 else 0.0
            )

    def note_rooms(equity: float) -> None:
        nonlocal near_overall_seen, near_daily_seen
        overall_room = equity - overall_floor()
        daily_room = equity - daily_floor()
        if result.min_overall_room_usd is None or overall_room < result.min_overall_room_usd:
            result.min_overall_room_usd = overall_room
        if result.min_daily_room_usd is None or daily_room < result.min_daily_room_usd:
            result.min_daily_room_usd = daily_room
        if overall_room <= phase.start_balance * 0.01:
            near_overall_seen = True
        if daily_room <= phase.daily_reference * 0.01:
            near_daily_seen = True

    def check_firm(equity: float) -> str | None:
        note_rooms(equity)
        if equity <= overall_floor() + EPS or phase.balance <= overall_floor() + EPS:
            return "OVERALL"
        if equity <= daily_floor() + EPS:
            return "DAILY"
        return None

    def check_soft(equity: float) -> str | None:
        daily_loss = phase.daily_reference - equity
        total_loss = phase.start_balance - equity
        daily_hit = daily_loss >= phase.start_balance * scenario.daily_soft_guard_pct - EPS
        total_hit = (
            total_loss >= phase.start_balance * scenario.overall_soft_guard_pct - EPS
        )
        if daily_hit and total_hit:
            return "BOTH"
        if daily_hit:
            return "DAILY"
        if total_hit:
            return "OVERALL"
        return None

    def fold_drawdown() -> None:
        if phase.max_dd_usd > result.max_dd_usd:
            result.max_dd_usd = phase.max_dd_usd
            result.max_dd_pct = phase.max_dd_pct

    def finish(
        outcome: str,
        end_time: pd.Timestamp | None,
        *,
        breach_daily: bool = False,
        breach_overall: bool = False,
        soft_guard: bool = False,
        inactivity: bool = False,
        incomplete: bool = False,
        detail: str = "",
    ) -> AttemptResult:
        fold_drawdown()
        result.near_overall = near_overall_seen and not breach_overall
        result.near_daily = near_daily_seen and not breach_daily
        return _finish(
            result,
            outcome,
            end_time,
            breach_daily=breach_daily,
            breach_overall=breach_overall,
            soft_guard=soft_guard,
            inactivity=inactivity,
            incomplete=incomplete,
            detail=detail,
        )

    last_close_time = attempt_start
    last_reset = attempt_start
    last_price: float | None = None

    def process_flat_resets(upto: pd.Timestamp) -> None:
        nonlocal last_reset
        reset = next_reset_after(last_reset, rules.daily_reset_hour_utc)
        while reset <= upto:
            phase.day_id = fp_day_id(reset, rules.daily_reset_hour_utc)
            phase.daily_reference = phase.balance
            last_reset = reset
            reset = next_reset_after(last_reset, rules.daily_reset_hour_utc)

    for path_index in range(start_index, len(paths)):
        path = paths[path_index]
        if path.entry_time > horizon:
            break
        if path.entry_time < attempt_start:
            continue

        if path.exit_time - last_close_time >= timedelta(days=rules.inactivity_days):
            return finish(
                "FAIL_INACTIVITY",
                last_close_time + timedelta(days=rules.inactivity_days),
                inactivity=True,
                detail="No completed trade for the inactivity window",
            )

        process_flat_resets(path.entry_time)

        lots, actual_risk, skip_reason = _size_lots(
            phase.balance,
            risk_fraction,
            path.stop_distance,
            path.entry_price,
            daily_room=max(0.0, phase.balance - daily_floor()),
            overall_room=max(0.0, phase.balance - overall_floor()),
        )
        if skip_reason is not None:
            if skip_reason == "MIN_LOT_EXCEEDS_BUDGET":
                result.skipped_min_lot += 1
            else:
                result.skipped_room += 1
            continue

        net_r = _net_r(path, cost)
        entry_price = path.entry_price
        direction = path.direction
        stop_distance = path.stop_distance

        for bar_index in range(path.entry_idx, path.exit_idx + 1):
            bar_ts = bars.index[bar_index]

            reset = next_reset_after(last_reset, rules.daily_reset_hour_utc)
            while reset <= bar_ts:
                if reset == bar_ts:
                    mark_price = float(bars.open[bar_index])
                elif last_price is not None:
                    mark_price = last_price
                else:
                    mark_price = float(bars.open[bar_index])
                spread = float(bars.spread_price[bar_index])
                equity_reset = _mark_equity(
                    phase.balance,
                    direction,
                    mark_price,
                    spread,
                    entry_price,
                    stop_distance,
                    actual_risk,
                )
                track_equity(equity_reset)
                firm = check_firm(equity_reset)
                if firm is not None:
                    return finish(
                        f"FAIL_{firm}",
                        reset,
                        breach_daily=firm == "DAILY",
                        breach_overall=firm == "OVERALL",
                        detail="Firm loss limit touched at daily reset",
                    )
                if scenario.internal_soft_guards:
                    soft = check_soft(equity_reset)
                    if soft is not None:
                        return finish(
                            f"SOFT_GUARD_{soft}",
                            reset,
                            soft_guard=True,
                            detail="Internal guard at daily reset",
                        )
                phase.day_id = fp_day_id(reset, rules.daily_reset_hour_utc)
                phase.daily_reference = max(phase.balance, equity_reset)
                last_reset = reset
                reset = next_reset_after(last_reset, rules.daily_reset_hour_utc)

            if fp_day_id(bar_ts, rules.daily_reset_hour_utc) != phase.day_id:
                # Safety net for a missing reset bar in the data.
                phase.day_id = fp_day_id(bar_ts, rules.daily_reset_hour_utc)
                phase.daily_reference = phase.balance

            worst_raw, best_raw = _bar_extremes(
                bars, bar_index, direction, entry_price, stop_distance
            )
            is_exit_bar = bar_index == path.exit_idx
            if is_exit_bar and path.reason == "stop":
                worst_r = min(path.gross_r, max(worst_raw, -1.0))
            else:
                worst_r = max(worst_raw, -1.0)
            best_r = max(best_raw, 0.0)

            equity_worst = phase.balance + worst_r * actual_risk
            equity_best = phase.balance + best_r * actual_risk
            # Conservative intrabar order for drawdown: best first, worst second.
            track_equity(equity_best)
            track_equity(equity_worst)

            firm = check_firm(equity_worst)
            if firm is not None:
                return finish(
                    f"FAIL_{firm}",
                    bar_ts,
                    breach_daily=firm == "DAILY",
                    breach_overall=firm == "OVERALL",
                    detail="Firm loss limit touched by floating equity",
                )
            if scenario.internal_soft_guards:
                soft = check_soft(equity_worst)
                if soft is not None:
                    return finish(
                        f"SOFT_GUARD_{soft}",
                        bar_ts,
                        soft_guard=True,
                        detail="Internal guard touched by floating equity",
                    )

            last_price = float(bars.close[bar_index])

            if is_exit_bar:
                phase.balance += net_r * actual_risk
                result.trades_taken += 1
                phase.trading_days.add(phase.day_id)
                last_close_time = path.exit_time
                track_equity(phase.balance)

                firm = check_firm(phase.balance)
                if firm is not None:
                    return finish(
                        f"FAIL_{firm}",
                        bar_ts,
                        breach_daily=firm == "DAILY",
                        breach_overall=firm == "OVERALL",
                        detail="Firm loss limit touched at close",
                    )
                if scenario.internal_soft_guards:
                    soft = check_soft(phase.balance)
                    if soft is not None:
                        return finish(
                            f"SOFT_GUARD_{soft}",
                            bar_ts,
                            soft_guard=True,
                            detail="Internal guard touched at close",
                        )

                profit = phase.balance - phase.start_balance
                target = phase.start_balance * (
                    rules.phase_1_target_pct
                    if phase.number == 1
                    else rules.phase_2_target_pct
                )
                if (
                    profit >= target - EPS
                    and len(phase.trading_days) >= scenario.min_trading_days
                ):
                    if phase.number == 1:
                        fold_drawdown()
                        result.phase1_passed = True
                        result.phase_reached = 2
                        next_balance = (
                            phase.balance
                            if scenario.carry_phase_2
                            else rules.phase_2_resets_to
                        )
                        result.phase2_start_balance = next_balance
                        phase = PhaseState(
                            number=2,
                            start_balance=next_balance,
                            balance=next_balance,
                            day_id=fp_day_id(
                                path.exit_time, rules.daily_reset_hour_utc
                            ),
                            daily_reference=next_balance,
                            trading_days=set(),
                            phase_start_time=path.exit_time,
                            peak_equity=next_balance,
                        )
                        last_close_time = path.exit_time
                        last_reset = path.exit_time
                    else:
                        result.phase2_passed = True
                        result.days_to_pass = (
                            path.exit_time - attempt_start
                        ).total_seconds() / 86400.0
                        return finish(
                            "PASS",
                            path.exit_time,
                            detail="Both phases passed under simulated rules",
                        )
                break
        else:
            raise RuntimeError(f"Trade bar loop did not reach exit: {path.config_id}")

    if horizon - last_close_time >= timedelta(days=rules.inactivity_days):
        return finish(
            "FAIL_INACTIVITY",
            last_close_time + timedelta(days=rules.inactivity_days),
            inactivity=True,
            detail="No completed trade for the inactivity window before horizon",
        )

    return finish(
        "INCOMPLETE",
        horizon,
        incomplete=True,
        detail="Data horizon reached without a challenge outcome",
    )


def _build_start_list(loaded: LoadedInputs) -> list[pd.Timestamp]:
    sim = loaded.config["challenge_simulation"]
    rolling = sim["rolling_start"]
    first = _utc(rolling["first_utc"])
    last = _utc(rolling["last_utc"])
    step = int(rolling["step_days"])
    min_future = int(rolling["min_future_days"])
    evaluation_end = _utc(sim["evaluation_end_utc"])

    starts: list[pd.Timestamp] = []
    cursor = first
    while cursor <= last:
        if cursor + pd.Timedelta(days=min_future) <= evaluation_end:
            starts.append(cursor)
        cursor += pd.Timedelta(days=step)
    if not starts:
        raise RuntimeError("No rolling starts satisfy the future-data requirement")
    return starts


def _build_scenarios(loaded: LoadedInputs) -> tuple[list[Scenario], list[CostCase]]:
    sim = loaded.config["challenge_simulation"]
    primary = sim["primary"]
    scenarios = [
        Scenario(
            name="primary",
            daily_loss_pct=float(primary["daily_loss_pct"]),
            min_trading_days=int(primary["min_trading_days"]),
            carry_phase_2=False,
            internal_soft_guards=True,
            daily_soft_guard_pct=float(
                primary["internal_daily_soft_guard_pct_of_start"]
            ),
            overall_soft_guard_pct=float(
                primary["internal_overall_soft_guard_pct_of_start"]
            ),
        ),
        Scenario(
            name="strict_daily",
            daily_loss_pct=float(
                sim["sensitivities"]["strict_daily"]["daily_loss_pct"]
            ),
            min_trading_days=int(
                sim["sensitivities"]["strict_daily"]["min_trading_days"]
            ),
            carry_phase_2=False,
            internal_soft_guards=True,
            daily_soft_guard_pct=float(
                primary["internal_daily_soft_guard_pct_of_start"]
            ),
            overall_soft_guard_pct=float(
                primary["internal_overall_soft_guard_pct_of_start"]
            ),
        ),
        Scenario(
            name="carry_phase_2",
            daily_loss_pct=float(primary["daily_loss_pct"]),
            min_trading_days=int(primary["min_trading_days"]),
            carry_phase_2=True,
            internal_soft_guards=True,
            daily_soft_guard_pct=float(
                primary["internal_daily_soft_guard_pct_of_start"]
            ),
            overall_soft_guard_pct=float(
                primary["internal_overall_soft_guard_pct_of_start"]
            ),
        ),
        Scenario(
            name="firm_rules_only",
            daily_loss_pct=float(primary["daily_loss_pct"]),
            min_trading_days=int(primary["min_trading_days"]),
            carry_phase_2=False,
            internal_soft_guards=False,
            daily_soft_guard_pct=float(
                primary["internal_daily_soft_guard_pct_of_start"]
            ),
            overall_soft_guard_pct=float(
                primary["internal_overall_soft_guard_pct_of_start"]
            ),
        ),
    ]

    costs_cfg = loaded.config["costs"]
    cost_cases = [
        CostCase(
            name="fp10_base",
            fp_commission_round_turn_usd=float(
                costs_cfg["primary_fundingpips_commission_round_turn_usd"]
            ),
            stress=1.0,
        ),
        CostCase(
            name="fp10_stress1.5",
            fp_commission_round_turn_usd=float(
                costs_cfg["primary_fundingpips_commission_round_turn_usd"]
            ),
            stress=float(costs_cfg["cost_stress_multiplier"]),
        ),
        CostCase(
            name="fp5_base",
            fp_commission_round_turn_usd=float(
                costs_cfg["sensitivity_fundingpips_commission_round_turn_usd"]
            ),
            stress=1.0,
        ),
    ]
    return scenarios, cost_cases


@dataclass(frozen=True)
class _WorkerSettings:
    bars: Bars
    starts: list[pd.Timestamp]
    risk_fractions: list[float]
    scenarios: list[Scenario]
    cost_cases: list[CostCase]
    rules: ChallengeRules
    horizon: pd.Timestamp


_WORKER_SETTINGS: _WorkerSettings | None = None


def _init_worker(settings: _WorkerSettings) -> None:
    global _WORKER_SETTINGS
    _WORKER_SETTINGS = settings


def _run_config_task(
    task: tuple[str, list[TradePath]],
) -> list[AttemptResult]:
    if _WORKER_SETTINGS is None:
        raise RuntimeError("Worker settings not initialized")
    settings = _WORKER_SETTINGS
    config_id, paths = task
    entry_times = [path.entry_time for path in paths]
    start_indices = [
        bisect.bisect_left(entry_times, start) for start in settings.starts
    ]
    out: list[AttemptResult] = []
    for risk in settings.risk_fractions:
        for scenario in settings.scenarios:
            for cost in settings.cost_cases:
                for start_ts, start_index in zip(settings.starts, start_indices, strict=True):
                    out.append(
                        simulate_attempt(
                            paths,
                            start_index,
                            settings.bars,
                            start_ts,
                            settings.horizon,
                            risk,
                            scenario,
                            cost,
                            settings.rules,
                            config_id=config_id,
                        )
                    )
    return out


def run_matrix(
    loaded: LoadedInputs, *, workers: int = 1, verbose: bool = True
) -> tuple[dict[str, Any], list[AttemptResult]]:
    sim = loaded.config["challenge_simulation"]
    starts = _build_start_list(loaded)
    scenarios, cost_cases = _build_scenarios(loaded)
    risk_fractions = [float(x) for x in sim["risk_fractions"]]
    horizon = _utc(sim["evaluation_end_utc"])
    settings = _WorkerSettings(
        bars=loaded.bars,
        starts=starts,
        risk_fractions=risk_fractions,
        scenarios=scenarios,
        cost_cases=cost_cases,
        rules=loaded.rules,
        horizon=horizon,
    )
    config_ids = sorted(loaded.trades_by_config)
    tasks = [(config_id, loaded.trades_by_config[config_id]) for config_id in config_ids]
    total_cells = len(config_ids) * len(risk_fractions) * len(scenarios) * len(cost_cases)

    results: list[AttemptResult] = []
    if workers > 1:
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(settings,)
        ) as executor:
            for index, batch in enumerate(executor.map(_run_config_task, tasks), 1):
                results.extend(batch)
                if verbose:
                    print(
                        f"[{index}/{len(tasks)}] {tasks[index - 1][0]} "
                        f"({len(batch)} attempts)",
                        flush=True,
                    )
    else:
        _init_worker(settings)
        for index, task in enumerate(tasks, 1):
            batch = _run_config_task(task)
            results.extend(batch)
            if verbose:
                print(
                    f"[{index}/{len(tasks)}] {task[0]} ({len(batch)} attempts)",
                    flush=True,
                )

    matrix = {
        "attempts": len(results),
        "cells": total_cells,
        "rolling_starts": len(starts),
        "configs": len(config_ids),
        "scenarios": [asdict(s) for s in scenarios],
        "cost_cases": [asdict(c) for c in cost_cases],
        "risk_fractions": risk_fractions,
        "initial_balance": loaded.rules.initial_balance,
        "phase_2_resets_to": loaded.rules.phase_2_resets_to,
    }
    return matrix, results


def _quantiles(values: Iterable[float]) -> dict[str, float | None]:
    data = sorted(v for v in values if v is not None and math.isfinite(v))
    if not data:
        return {"median": None, "p25": None, "p75": None, "p95": None}

    def pick(q: float) -> float:
        index = min(len(data) - 1, max(0, int(round(q * (len(data) - 1)))))
        return float(data[index])

    return {
        "median": pick(0.50),
        "p25": pick(0.25),
        "p75": pick(0.75),
        "p95": pick(0.95),
    }


def aggregate(results: list[AttemptResult]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float, str, str], list[AttemptResult]] = defaultdict(list)
    for result in results:
        groups[
            (result.config_id, result.risk_fraction, result.scenario, result.cost_case)
        ].append(result)

    rows: list[dict[str, Any]] = []
    for (config_id, risk, scenario, cost), items in sorted(groups.items()):
        n = len(items)
        phase1 = sum(1 for r in items if r.phase1_passed)
        passes = sum(1 for r in items if r.phase2_passed)
        firm_any = sum(1 for r in items if r.breach_daily or r.breach_overall)
        soft = sum(1 for r in items if r.soft_guard)
        inactivity = sum(1 for r in items if r.inactivity)
        incomplete = sum(1 for r in items if r.incomplete)
        near_overall = sum(1 for r in items if r.near_overall)
        near_daily = sum(1 for r in items if r.near_daily)
        pass_days = [r.days_to_pass for r in items if r.days_to_pass is not None]
        quantiles = _quantiles(pass_days)
        rows.append(
            {
                "environment": ENVIRONMENT,
                "config_id": config_id,
                "risk_fraction": risk,
                "scenario": scenario,
                "cost_case": cost,
                "attempts": n,
                "phase1_pass": phase1,
                "phase1_pass_rate": phase1 / n if n else None,
                "both_phase_pass": passes,
                "both_phase_pass_rate": passes / n if n else None,
                "phase2_pass_given_phase1": passes / phase1 if phase1 else None,
                "firm_daily_breach": sum(1 for r in items if r.breach_daily),
                "firm_overall_breach": sum(1 for r in items if r.breach_overall),
                "firm_breach_any": firm_any,
                "firm_breach_rate": firm_any / n if n else None,
                "soft_guard": soft,
                "soft_guard_rate": soft / n if n else None,
                "inactivity": inactivity,
                "inactivity_rate": inactivity / n if n else None,
                "incomplete": incomplete,
                "incomplete_rate": incomplete / n if n else None,
                "near_overall": near_overall,
                "near_overall_rate": near_overall / n if n else None,
                "near_daily": near_daily,
                "median_days_to_pass": quantiles["median"],
                "p25_days_to_pass": quantiles["p25"],
                "p75_days_to_pass": quantiles["p75"],
                "median_max_dd_usd": _quantiles(r.max_dd_usd for r in items)["median"],
                "p95_max_dd_usd": _quantiles(r.max_dd_usd for r in items)["p95"],
                "median_max_dd_pct": _quantiles(r.max_dd_pct for r in items)["median"],
                "p95_max_dd_pct": _quantiles(r.max_dd_pct for r in items)["p95"],
                "median_trades_taken": _quantiles(r.trades_taken for r in items)["median"],
                "total_skipped_min_lot": sum(r.skipped_min_lot for r in items),
                "total_skipped_room": sum(r.skipped_room for r in items),
                "outcome_counts": dict(Counter(r.outcome for r in items)),
            }
        )
    return rows


def summarize(
    results: list[AttemptResult], aggregates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[tuple[float, str, str], list[AttemptResult]] = defaultdict(list)
    for result in results:
        groups[(result.risk_fraction, result.scenario, result.cost_case)].append(result)
    cells_by_key: dict[tuple[float, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in aggregates:
        cells_by_key[
            (row["risk_fraction"], row["scenario"], row["cost_case"])
        ].append(row)

    rows: list[dict[str, Any]] = []
    for (risk, scenario, cost), items in sorted(groups.items()):
        n = len(items)
        passes = sum(1 for r in items if r.phase2_passed)
        phase1 = sum(1 for r in items if r.phase1_passed)
        firm_any = sum(1 for r in items if r.breach_daily or r.breach_overall)
        soft = sum(1 for r in items if r.soft_guard)
        incomplete = sum(1 for r in items if r.incomplete)
        inactivity = sum(1 for r in items if r.inactivity)
        near_overall = sum(1 for r in items if r.near_overall)
        pass_days = [r.days_to_pass for r in items if r.days_to_pass is not None]
        quantiles = _quantiles(pass_days)
        cells = cells_by_key[(risk, scenario, cost)]
        config_pass_rates = [
            cell["both_phase_pass_rate"]
            for cell in cells
            if cell["both_phase_pass_rate"] is not None
        ]
        rows.append(
            {
                "environment": ENVIRONMENT,
                "scenario": scenario,
                "cost_case": cost,
                "risk_fraction": risk,
                "configs": len(cells),
                "attempts": n,
                "phase1_pass": phase1,
                "phase1_pass_rate": phase1 / n if n else None,
                "both_phase_pass": passes,
                "both_phase_pass_rate": passes / n if n else None,
                "firm_breach": firm_any,
                "firm_breach_rate": firm_any / n if n else None,
                "soft_guard": soft,
                "soft_guard_rate": soft / n if n else None,
                "incomplete": incomplete,
                "incomplete_rate": incomplete / n if n else None,
                "inactivity": inactivity,
                "inactivity_rate": inactivity / n if n else None,
                "near_overall": near_overall,
                "near_overall_rate": near_overall / n if n else None,
                "median_config_pass_rate": (
                    _quantiles(config_pass_rates)["median"]
                    if config_pass_rates
                    else None
                ),
                "max_config_pass_rate": max(config_pass_rates) if config_pass_rates else None,
                "configs_with_zero_firm_breach": sum(
                    1 for cell in cells if cell["firm_breach_any"] == 0
                ),
                "median_days_to_pass": quantiles["median"],
                "p25_days_to_pass": quantiles["p25"],
                "p75_days_to_pass": quantiles["p75"],
                "median_max_dd_pct": _quantiles(r.max_dd_pct for r in items)["median"],
                "p95_max_dd_pct": _quantiles(r.max_dd_pct for r in items)["p95"],
                "total_skipped_min_lot": sum(r.skipped_min_lot for r in items),
                "total_skipped_room": sum(r.skipped_room for r in items),
            }
        )
    return rows


def _code_sha256() -> str:
    return sha256_file(Path(__file__).resolve())


def _git_commit() -> str | None:
    try:
        import subprocess

        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip() or None
    except Exception:
        return None
    return None


def run(
    config_path: Path,
    output_dir: Path,
    *,
    workers: int = 1,
    verbose: bool = True,
) -> dict[str, Any]:
    loaded = load_inputs(config_path)
    matrix, results = run_matrix(loaded, workers=workers, verbose=verbose)
    rows = aggregate(results)
    summary = summarize(results, rows)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "environment": ENVIRONMENT,
        "challenge_model": (
            f"FundingPips 2 Step Standard, "
            f"${loaded.rules.initial_balance:,.0f}"
        ),
        "holdout_accessed": False,
        "simulated_pass_not_real_pass": True,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "code_sha256": _code_sha256(),
        "git_commit": _git_commit(),
        "config": {
            "path": str(config_path),
            "sha256": loaded.config_sha256,
        },
        "data": {
            "path": loaded.config["challenge_simulation"]["market_data"],
            "sha256": loaded.data_sha256,
        },
        "trade_log": {
            "path": loaded.config["challenge_simulation"]["trade_log"],
            "sha256": loaded.trade_log_sha256,
        },
        "matrix": matrix,
        "summary": summary,
        "aggregates": rows,
        "caveats": [
            "A simulated pass is not a FundingPips pass.",
            "Rolling attempts overlap and are not independent samples.",
            "Fills and costs are the frozen v5 simulations, not FundingPips fills.",
            "FundingPips swap and the commission side basis are unverified.",
            "The daily reset is fixed at 21:00 UTC; the winter DST offset is unresolved.",
            "Floating marks use H1 bid OHLC with the observed spread applied to shorts.",
            "The holdout 2023-2026 was not accessed.",
        ],
    }
    (output_dir / "challenge_report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    pd.DataFrame([asdict(r) for r in results]).to_csv(
        output_dir / "attempts.csv", index=False
    )
    pd.DataFrame(rows).to_csv(output_dir / "aggregates.csv", index=False)
    pd.DataFrame(summary).to_csv(output_dir / "summary.csv", index=False)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/fundingpips.yaml")
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports_fundingpips/challenge_2026-10-22"),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
    )
    args = parser.parse_args(argv)
    report = run(
        args.config,
        args.out,
        workers=max(1, args.workers),
        verbose=True,
    )
    print(
        json.dumps(
            {
                "attempts": report["matrix"]["attempts"],
                "cells": report["matrix"]["cells"],
                "rolling_starts": report["matrix"]["rolling_starts"],
                "output": str(args.out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
