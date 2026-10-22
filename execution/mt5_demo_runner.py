"""Fail-closed MT5 demo execution runner.

This module is the only sanctioned order-submission path for the FundingPips
work. It has no live-account path.

Safety invariants
-----------------
* ``account_info().trade_mode`` is re-read immediately before every send and
  must equal ``ACCOUNT_TRADE_MODE_DEMO``.
* Terminal AutoTrading (``terminal_info().trade_allowed``) and EA permission
  (``account_info().trade_expert``) must both be enabled.
* The account login/server/starting balance must match the persisted baseline.
* Sizing uses broker-calculated profit per price unit and the broker volume
  step, always rounding DOWN. The minimum lot is rejected when it exceeds the
  approved risk budget.
* Deployment is blocked while any rule or configuration blocker is unresolved.
* The intent is persisted before a single ``order_send``. Sends are never
  retried. An uncertain result halts new entries and activates the kill switch.
* Fills are verified against the broker (volume, SL, TP, price).
* A manual kill switch (file or env var) blocks new entries.

Environment label: ``MT5_DEMO``. Nothing here may be pointed at a live account.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

import yaml

from engine.prop_firm_compliance import PropFirmComplianceEngine
from engine.prop_firm_rules import (
    PropFirmRuleProfile,
    RuleSourceStatus,
    get_profile,
)

logger = logging.getLogger("quant_lab.mt5_demo")

ENVIRONMENT = "MT5_DEMO"

# Constants mirrored locally so a misbehaving wrapper module cannot redefine them.
DEMO_TRADE_MODE = 0
SYMBOL_TRADE_MODE_FULL = 4
TRADE_ACTION_DEAL = 1
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_PLACED = 10008

RETCODE_NAMES: dict[int, str] = {
    0: "DONE",
    10004: "REQUOTE",
    10006: "REJECT",
    10007: "CANCEL",
    10008: "PLACED",
    10009: "DONE",
    10010: "DONE_PARTIAL",
    10011: "ERROR",
    10012: "TIMEOUT",
    10013: "INVALID",
    10014: "INVALID_VOLUME",
    10015: "INVALID_PRICE",
    10016: "INVALID_STOPS",
    10018: "MARKET_CLOSED",
    10019: "NO_MONEY",
    10020: "PRICE_CHANGED",
    10021: "PRICE_OFF",
    10027: "AUTOTRADING_DISABLED",
    10030: "INVALID_FILL",
    10031: "CONNECTION",
    10033: "LIMIT_ORDERS",
    10034: "LIMIT_VOLUME",
    10036: "POSITION_CLOSED",
    10038: "CLOSE_ONLY",
}

# Results where the broker may or may not have received the order.
UNCERTAIN_RETCODES = {10012, 10031}
UNCERTAIN_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)


class RunnerError(RuntimeError):
    """Base class for fail-closed runner errors."""


class SpecError(RunnerError):
    """The broker symbol specification is incomplete or inconsistent."""


class MT5ReadError(RunnerError):
    """A read-only MT5 call failed after bounded retries."""


class RunnerBusy(RunnerError):
    """Another runner instance holds the execution lock."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime | None = None) -> str:
    return (ts or utc_now()).astimezone(UTC).isoformat()


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(UTC)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _floor_to_step(value: float, step: float) -> float:
    if not (math.isfinite(value) and math.isfinite(step)) or step <= 0:
        raise ValueError("value and step must be finite; step must be positive")
    units = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return float(units * Decimal(str(step)))


# ---------------------------------------------------------------------------
# Broker symbol specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolSpec:
    """Validated subset of MT5 symbol properties used for deterministic sizing."""

    symbol: str
    digits: int
    point: float
    tick_size: float
    reported_tick_value: float
    contract_size: float
    profit_per_unit_per_lot: float
    profit_buy_per_unit: float
    profit_sell_per_unit: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int
    freeze_level_points: int
    currency_profit: str
    trade_mode: int
    filling_mode: int
    execution_mode: int
    tick_value_consistent: bool
    warnings: tuple[str, ...] = ()

    @property
    def money_per_point_per_lot(self) -> float:
        """Account-currency P&L per 1 point move per 1.0 lot (adverse max)."""
        return self.profit_per_unit_per_lot * self.point

    def risk_per_lot(self, stop_distance: float, extra_cost_per_lot: float = 0.0) -> float:
        """Worst-case loss in account currency for a stop at ``stop_distance``."""
        if not math.isfinite(stop_distance) or stop_distance <= 0:
            raise ValueError("stop_distance must be finite and positive")
        if not math.isfinite(extra_cost_per_lot) or extra_cost_per_lot < 0:
            raise ValueError("extra_cost_per_lot must be finite and non-negative")
        return stop_distance * self.profit_per_unit_per_lot + extra_cost_per_lot

    def minimum_stop_distance(self) -> float:
        return max(1, self.stops_level_points) * self.point

    def choose_filling(self) -> int:
        if self.filling_mode & SYMBOL_FILLING_IOC:
            return ORDER_FILLING_IOC
        if self.filling_mode & SYMBOL_FILLING_FOK:
            return ORDER_FILLING_FOK
        raise SpecError(
            f"{self.symbol} advertises filling_mode={self.filling_mode}; "
            "no FOK/IOC market filling mode is available"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "digits": self.digits,
            "point": self.point,
            "tick_size": self.tick_size,
            "reported_tick_value": self.reported_tick_value,
            "contract_size": self.contract_size,
            "profit_per_unit_per_lot": self.profit_per_unit_per_lot,
            "profit_buy_per_unit": self.profit_buy_per_unit,
            "profit_sell_per_unit": self.profit_sell_per_unit,
            "money_per_point_per_lot": self.money_per_point_per_lot,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
            "volume_step": self.volume_step,
            "stops_level_points": self.stops_level_points,
            "freeze_level_points": self.freeze_level_points,
            "currency_profit": self.currency_profit,
            "trade_mode": self.trade_mode,
            "filling_mode": self.filling_mode,
            "execution_mode": self.execution_mode,
            "tick_value_consistent": self.tick_value_consistent,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_broker(
        cls,
        info: Any,
        *,
        buy_profit_per_unit: float,
        sell_profit_per_unit: float,
        account_currency: str,
    ) -> SymbolSpec:
        def f(name: str) -> float:
            value = float(getattr(info, name, float("nan")))
            if not math.isfinite(value):
                raise SpecError(f"symbol property {name} is missing or non-finite")
            return value

        symbol = str(getattr(info, "name", "") or "")
        if not symbol:
            raise SpecError("symbol name is missing")

        digits = int(getattr(info, "digits", -1))
        if not 0 <= digits <= 8:
            raise SpecError(f"invalid digits={digits} for {symbol}")

        point = f("point")
        tick_size = f("trade_tick_size")
        reported_tick_value = f("trade_tick_value")
        contract_size = f("trade_contract_size")
        volume_min = f("volume_min")
        volume_max = f("volume_max")
        volume_step = f("volume_step")
        stops_level = int(getattr(info, "trade_stops_level", 0))
        freeze_level = int(getattr(info, "trade_freeze_level", 0))
        currency_profit = str(getattr(info, "currency_profit", "") or "")
        trade_mode = int(getattr(info, "trade_mode", -1))
        filling_mode = int(getattr(info, "filling_mode", -1))
        execution_mode = int(getattr(info, "trade_exemode", -1))

        for label, value in (
            ("point", point),
            ("trade_tick_size", tick_size),
            ("trade_contract_size", contract_size),
            ("volume_min", volume_min),
            ("volume_max", volume_max),
            ("volume_step", volume_step),
        ):
            if value <= 0:
                raise SpecError(f"{label} must be > 0 for {symbol}, got {value!r}")
        if volume_min > volume_max:
            raise SpecError(
                f"volume_min={volume_min} exceeds volume_max={volume_max} for {symbol}"
            )
        if stops_level < 0 or freeze_level < 0:
            raise SpecError(f"negative stops/freeze level for {symbol}")

        buy = float(buy_profit_per_unit)
        sell = float(sell_profit_per_unit)
        if not (math.isfinite(buy) and math.isfinite(sell)) or buy <= 0 or sell <= 0:
            raise SpecError(
                f"order_calc_profit unavailable for {symbol}: buy={buy!r}, sell={sell!r}"
            )
        measured = max(buy, sell)
        if abs(buy - sell) > 0.05 * measured:
            raise SpecError(
                f"asymmetric profit calculation for {symbol}: buy={buy}, sell={sell}"
            )

        warnings: list[str] = []
        implied = contract_size if currency_profit.upper() == account_currency.upper() else None
        if implied is not None:
            if abs(measured - implied) > 0.05 * max(measured, implied):
                raise SpecError(
                    f"contract-size/profit mismatch for {symbol}: "
                    f"order_calc_profit={measured} per price unit per lot, "
                    f"contract_size={implied}"
                )
            profit_per_unit = max(measured, implied)
        else:
            profit_per_unit = measured
            warnings.append(
                f"profit currency {currency_profit!r} != account currency "
                f"{account_currency!r}; using order_calc_profit only"
            )

        expected_tick_value = tick_size * profit_per_unit
        tick_value_consistent = abs(reported_tick_value - expected_tick_value) <= (
            0.01 * abs(expected_tick_value) + 1e-9
        )
        if not tick_value_consistent:
            warnings.append(
                f"reported trade_tick_value={reported_tick_value} disagrees with "
                f"order_calc_profit implied value={expected_tick_value:.6f} per "
                f"{tick_size} move; sizing uses order_calc_profit + contract size"
            )

        if trade_mode != SYMBOL_TRADE_MODE_FULL:
            warnings.append(f"symbol trade_mode={trade_mode} (FULL required to trade)")
        if execution_mode < 0:
            warnings.append("symbol trade_exemode missing")

        return cls(
            symbol=symbol,
            digits=digits,
            point=point,
            tick_size=tick_size,
            reported_tick_value=reported_tick_value,
            contract_size=contract_size,
            profit_per_unit_per_lot=profit_per_unit,
            profit_buy_per_unit=buy,
            profit_sell_per_unit=sell,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
            stops_level_points=stops_level,
            freeze_level_points=freeze_level,
            currency_profit=currency_profit,
            trade_mode=trade_mode,
            filling_mode=filling_mode,
            execution_mode=execution_mode,
            tick_value_consistent=tick_value_consistent,
            warnings=tuple(warnings),
        )


# ---------------------------------------------------------------------------
# Tick validation and position sizing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickSnapshot:
    symbol: str
    bid: float
    ask: float
    spread_price: float
    spread_points: float
    age_seconds: float
    time_utc: str
    raw_time_utc: str
    server_offset_seconds: float
    ok: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "spread_price": self.spread_price,
            "spread_points": self.spread_points,
            "age_seconds": self.age_seconds,
            "time_utc": self.time_utc,
            "raw_time_utc": self.raw_time_utc,
            "server_offset_seconds": self.server_offset_seconds,
            "ok": self.ok,
            "reasons": list(self.reasons),
        }


def validate_tick(
    tick: Any,
    spec: SymbolSpec,
    now: datetime,
    *,
    max_age_seconds: float,
    max_spread_points: float,
    server_utc_offset_seconds: float = 0.0,
    future_tolerance_seconds: float = 2.0,
) -> TickSnapshot:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if max_spread_points <= 0:
        raise ValueError("max_spread_points must be positive")
    if not math.isfinite(server_utc_offset_seconds):
        raise ValueError("server_utc_offset_seconds must be finite")

    bid = float(getattr(tick, "bid", float("nan")))
    ask = float(getattr(tick, "ask", float("nan")))
    raw_time_msc = int(getattr(tick, "time_msc", 0) or 0)
    if raw_time_msc <= 0:
        seconds = int(getattr(tick, "time", 0) or 0)
        raw_time_msc = seconds * 1000

    # MT5 timestamps are broker server time, not UTC. The offset is explicit
    # configuration and is separately reported for audit.
    time_msc = raw_time_msc - int(round(server_utc_offset_seconds * 1000))
    now_ms = _utc(now).timestamp() * 1000.0
    age = (now_ms - time_msc) / 1000.0 if time_msc > 0 else float("inf")
    time_utc = (
        datetime.fromtimestamp(time_msc / 1000.0, tz=UTC).isoformat()
        if time_msc > 0
        else ""
    )
    raw_time_utc = (
        datetime.fromtimestamp(raw_time_msc / 1000.0, tz=UTC).isoformat()
        if raw_time_msc > 0
        else ""
    )

    reasons: list[str] = []
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask <= 0:
        reasons.append("INVALID_PRICES")
    spread = ask - bid
    if math.isfinite(spread) and spread < 0:
        reasons.append("CROSSED_QUOTE")
    if time_msc <= 0:
        reasons.append("MISSING_TIMESTAMP")
    elif age < -future_tolerance_seconds:
        reasons.append(f"FUTURE_TIMESTAMP({age:.1f}s)")
    elif age > max_age_seconds:
        reasons.append(f"STALE_TICK({age:.1f}s>{max_age_seconds:.1f}s)")

    spread_points = spread / spec.point if spec.point > 0 else float("inf")
    if math.isfinite(spread_points) and spread_points > max_spread_points:
        reasons.append(
            f"SPREAD_TOO_WIDE({spread_points:.1f}>{max_spread_points:.1f} points)"
        )

    return TickSnapshot(
        symbol=spec.symbol,
        bid=bid,
        ask=ask,
        spread_price=spread if math.isfinite(spread) else float("nan"),
        spread_points=spread_points,
        age_seconds=age,
        time_utc=time_utc,
        raw_time_utc=raw_time_utc,
        server_offset_seconds=server_utc_offset_seconds,
        ok=not reasons,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class SizeDecision:
    approved: bool
    lots: float
    risk_usd: float
    risk_budget_usd: float
    per_lot_risk_usd: float
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "lots": self.lots,
            "risk_usd": self.risk_usd,
            "risk_budget_usd": self.risk_budget_usd,
            "per_lot_risk_usd": self.per_lot_risk_usd,
            "reason": self.reason,
            "detail": self.detail,
        }


def size_position(
    spec: SymbolSpec,
    *,
    equity: float,
    risk_fraction: float,
    stop_distance: float,
    extra_cost_per_lot_usd: float = 0.0,
    daily_room_usd: float | None = None,
    overall_room_usd: float | None = None,
    max_lots: float | None = None,
) -> SizeDecision:
    """Size a position from broker specs, always rounding volume down.

    ``stop_distance`` is the price distance to the protective stop. The stop is
    not widened; ``extra_cost_per_lot_usd`` covers commission and stop-gap
    slippage in the risk budget.
    """
    if not math.isfinite(equity) or equity <= 0:
        return SizeDecision(False, 0.0, 0.0, 0.0, 0.0, "INVALID_EQUITY")
    if not 0 < risk_fraction <= 0.05:
        return SizeDecision(False, 0.0, 0.0, 0.0, 0.0, "INVALID_RISK_FRACTION")
    if not math.isfinite(stop_distance) or stop_distance <= 0:
        return SizeDecision(False, 0.0, 0.0, 0.0, 0.0, "INVALID_STOP_DISTANCE")

    risk_budget = equity * risk_fraction
    room_caps = [
        room
        for room in (daily_room_usd, overall_room_usd)
        if room is not None
    ]
    if any(not math.isfinite(r) for r in room_caps):
        return SizeDecision(False, 0.0, 0.0, risk_budget, 0.0, "INVALID_ROOM")
    room_cap = min(room_caps) if room_caps else risk_budget
    budget = min(risk_budget, room_cap)
    if budget <= 0:
        return SizeDecision(False, 0.0, 0.0, risk_budget, 0.0, "NO_RISK_ROOM")

    per_lot = spec.risk_per_lot(stop_distance, extra_cost_per_lot_usd)
    min_lot_risk = per_lot * spec.volume_min
    base_detail: dict[str, Any] = {
        "equity": equity,
        "risk_fraction": risk_fraction,
        "stop_distance": stop_distance,
        "extra_cost_per_lot_usd": extra_cost_per_lot_usd,
        "per_lot_risk_usd": per_lot,
        "min_lot_risk_usd": min_lot_risk,
        "risk_budget_usd": risk_budget,
        "room_cap_usd": room_cap,
        "volume_step": spec.volume_step,
        "volume_min": spec.volume_min,
    }

    if budget + 1e-9 < min_lot_risk:
        return SizeDecision(
            False,
            0.0,
            0.0,
            risk_budget,
            per_lot,
            "MIN_LOT_EXCEEDS_BUDGET",
            base_detail,
        )

    lots = _floor_to_step(budget / per_lot, spec.volume_step)
    ceiling = spec.volume_max
    if max_lots is not None:
        ceiling = min(ceiling, max_lots)
    lots = min(lots, _floor_to_step(ceiling, spec.volume_step))
    if lots + 1e-12 < spec.volume_min:
        return SizeDecision(
            False, 0.0, 0.0, risk_budget, per_lot, "BELOW_MIN_VOLUME", base_detail
        )

    while lots >= spec.volume_min and lots * per_lot > budget + 1e-9:
        lots = _floor_to_step(lots - spec.volume_step, spec.volume_step)
    if lots + 1e-12 < spec.volume_min:
        return SizeDecision(
            False, 0.0, 0.0, risk_budget, per_lot, "BELOW_MIN_VOLUME", base_detail
        )

    actual_risk = lots * per_lot
    detail = dict(base_detail)
    detail.update(
        {
            "lots": lots,
            "actual_risk_usd": actual_risk,
            "risk_utilization": actual_risk / risk_budget if risk_budget else None,
        }
    )
    return SizeDecision(True, lots, actual_risk, risk_budget, per_lot, "APPROVED", detail)


# ---------------------------------------------------------------------------
# Demo verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoVerdict:
    ok: bool
    reasons: tuple[str, ...]
    login: int | None = None
    server: str | None = None
    trade_mode: int | None = None
    balance: float | None = None
    equity: float | None = None
    currency: str | None = None
    leverage: int | None = None
    account_trade_allowed: bool | None = None
    account_trade_expert: bool | None = None
    terminal_connected: bool | None = None
    terminal_trade_allowed: bool | None = None
    terminal_company: str | None = None
    terminal_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reasons": list(self.reasons),
            "login": self.login,
            "server": self.server,
            "trade_mode": self.trade_mode,
            "balance": self.balance,
            "equity": self.equity,
            "currency": self.currency,
            "leverage": self.leverage,
            "account_trade_allowed": self.account_trade_allowed,
            "account_trade_expert": self.account_trade_expert,
            "terminal_connected": self.terminal_connected,
            "terminal_trade_allowed": self.terminal_trade_allowed,
            "terminal_company": self.terminal_company,
            "terminal_name": self.terminal_name,
        }


# ---------------------------------------------------------------------------
# MT5 client
# ---------------------------------------------------------------------------


def _result_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    if isinstance(obj, dict):
        return dict(obj)
    keys = (
        "retcode",
        "balance",
        "equity",
        "profit",
        "margin",
        "margin_free",
        "margin_level",
        "comment",
        "order",
        "deal",
        "price",
        "volume",
        "request_id",
    )
    return {key: getattr(obj, key) for key in keys if hasattr(obj, key)}


def _position_dict(obj: Any) -> dict[str, Any]:
    keys = (
        "ticket",
        "symbol",
        "type",
        "volume",
        "price_open",
        "price_current",
        "sl",
        "tp",
        "magic",
        "comment",
        "profit",
        "swap",
        "time",
        "time_msc",
        "identifier",
    )
    return {key: getattr(obj, key) for key in keys if hasattr(obj, key)}


class MT5DemoClient:
    """Thin fail-closed wrapper around the MetaTrader5 module."""

    def __init__(
        self,
        module: Any,
        *,
        expected_login: int | None = None,
        expected_server: str | None = None,
        read_attempts: int = 3,
        retry_delay_seconds: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.module = module
        self.expected_login = expected_login
        self.expected_server = expected_server
        self.read_attempts = max(1, read_attempts)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._sleep = sleep
        self._initialized = False

    def initialize(self) -> tuple[bool, str]:
        try:
            ok = bool(self.module.initialize())
        except Exception as exc:  # noqa: BLE001 - fail closed, surface reason
            return False, f"INITIALIZE_EXCEPTION: {exc}"
        self._initialized = ok
        if not ok:
            return False, f"INITIALIZE_FAILED: {self.last_error()}"
        return True, "INITIALIZED"

    def shutdown(self) -> None:
        try:
            self.module.shutdown()
        except Exception:  # noqa: BLE001 - shutdown must not mask results
            logger.warning("MT5 shutdown raised", exc_info=True, extra={"environment": ENVIRONMENT})
        self._initialized = False

    def last_error(self) -> Any:
        try:
            return self.module.last_error()
        except Exception:  # noqa: BLE001
            return None

    def _read(self, fn: Callable[[], Any], label: str) -> Any:
        last: Any = None
        for attempt in range(self.read_attempts):
            try:
                value = fn()
            except Exception as exc:  # noqa: BLE001
                last = exc
                value = None
            if value is not None:
                return value
            if attempt + 1 < self.read_attempts:
                self._sleep(self.retry_delay_seconds * (attempt + 1))
        raise MT5ReadError(f"{label} unavailable after {self.read_attempts} attempts: {last}")

    def account_info(self) -> Any:
        return self._read(self.module.account_info, "account_info")

    def terminal_info(self) -> Any:
        return self._read(self.module.terminal_info, "terminal_info")

    def symbol_info(self, symbol: str) -> Any:
        selected = self.module.symbol_select(symbol, True)
        if not selected:
            raise MT5ReadError(
                f"symbol_select({symbol!r}) failed: {self.last_error()}"
            )
        info = self._read(lambda: self.module.symbol_info(symbol), f"symbol_info({symbol})")
        return info

    def tick(self, symbol: str) -> Any:
        return self._read(
            lambda: self.module.symbol_info_tick(symbol), f"symbol_info_tick({symbol})"
        )

    def profit_per_unit(
        self, symbol: str, order_type: int, price_open: float, price_close: float
    ) -> float:
        value = self.module.order_calc_profit(
            order_type, symbol, 1.0, float(price_open), float(price_close)
        )
        if value is None:
            raise MT5ReadError(
                f"order_calc_profit({symbol}) failed: {self.last_error()}"
            )
        return float(value)

    def positions(self, symbol: str) -> tuple[Any, ...]:
        value = self._read(
            lambda: self.module.positions_get(symbol=symbol), f"positions_get({symbol})"
        )
        return tuple(value)

    def pending_orders(self, symbol: str) -> tuple[Any, ...]:
        value = self._read(
            lambda: self.module.orders_get(symbol=symbol), f"orders_get({symbol})"
        )
        return tuple(value)

    def order_check(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return _result_dict(self.module.order_check(dict(request)))

    def order_send(self, request: Mapping[str, Any]) -> dict[str, Any]:
        # Exactly one attempt. Never retried: the client may not know whether
        # the request reached the broker.
        return _result_dict(self.module.order_send(dict(request)))

    def build_symbol_spec(self, symbol: str) -> SymbolSpec:
        info = self.symbol_info(symbol)
        tick = self.tick(symbol)
        ask = float(getattr(tick, "ask", float("nan")))
        bid = float(getattr(tick, "bid", float("nan")))
        if not (math.isfinite(ask) and math.isfinite(bid)) or ask <= 0 or bid <= 0:
            raise SpecError(f"cannot build {symbol} spec from invalid tick")
        probe = 1.0 if ask > 2.0 else max(float(getattr(info, "point", 0.01)) * 10, 0.01)
        buy = self.profit_per_unit(symbol, ORDER_TYPE_BUY, ask, ask + probe) / probe
        sell = self.profit_per_unit(symbol, ORDER_TYPE_SELL, bid, bid - probe) / probe
        account = self.account_info()
        currency = str(getattr(account, "currency", "") or "")
        return SymbolSpec.from_broker(
            info,
            buy_profit_per_unit=buy,
            sell_profit_per_unit=sell,
            account_currency=currency,
        )

    def verify_demo(self, *, require_terminal: bool = True) -> DemoVerdict:
        """Re-read the account and terminal. Never trusts a cached result."""
        account = self.account_info()
        terminal = self.terminal_info()

        reasons: list[str] = []
        trade_mode = getattr(account, "trade_mode", None)
        login = getattr(account, "login", None)
        server = getattr(account, "server", None)
        account_trade_allowed = getattr(account, "trade_allowed", None)
        account_trade_expert = getattr(account, "trade_expert", None)
        terminal_connected = getattr(terminal, "connected", None)
        terminal_trade_allowed = getattr(terminal, "trade_allowed", None)

        if trade_mode is None:
            reasons.append("TRADE_MODE_UNKNOWN")
        elif int(trade_mode) != DEMO_TRADE_MODE:
            reasons.append(f"NOT_DEMO(trade_mode={trade_mode})")
        if login is None or int(login) <= 0:
            reasons.append("LOGIN_UNKNOWN")
        elif self.expected_login is not None and int(login) != int(self.expected_login):
            reasons.append(f"LOGIN_MISMATCH(expected={self.expected_login},got={login})")
        if not server:
            reasons.append("SERVER_UNKNOWN")
        elif self.expected_server is not None and str(server) != self.expected_server:
            reasons.append(
                f"SERVER_MISMATCH(expected={self.expected_server!r},got={server!r})"
            )
        if terminal_connected is not True:
            reasons.append("TERMINAL_DISCONNECTED")
        if require_terminal:
            if terminal_trade_allowed is not True:
                reasons.append("TERMINAL_AUTOTRADING_DISABLED")
            if account_trade_expert is not True:
                reasons.append("EA_TRADING_DISABLED")

        return DemoVerdict(
            ok=not reasons,
            reasons=tuple(reasons),
            login=int(login) if login is not None else None,
            server=str(server) if server else None,
            trade_mode=int(trade_mode) if trade_mode is not None else None,
            balance=float(account.balance) if getattr(account, "balance", None) is not None else None,
            equity=float(account.equity) if getattr(account, "equity", None) is not None else None,
            currency=str(account.currency) if getattr(account, "currency", None) else None,
            leverage=int(account.leverage) if getattr(account, "leverage", None) is not None else None,
            account_trade_allowed=(
                bool(account_trade_allowed) if account_trade_allowed is not None else None
            ),
            account_trade_expert=(
                bool(account_trade_expert) if account_trade_expert is not None else None
            ),
            terminal_connected=(
                bool(terminal_connected) if terminal_connected is not None else None
            ),
            terminal_trade_allowed=(
                bool(terminal_trade_allowed) if terminal_trade_allowed is not None else None
            ),
            terminal_company=getattr(terminal, "company", None),
            terminal_name=getattr(terminal, "name", None),
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunnerSettings:
    symbol: str = "XAUUSD"
    account_initial_balance: float = 5000.0
    risk_fraction: float = 0.0025
    max_spread_points: float = 50.0
    max_tick_age_seconds: float = 5.0
    server_utc_offset_hours: float = 0.0
    max_slippage_points: int = 20
    magic: int = 20261022
    commission_round_turn_per_lot_usd: float = 10.0
    risk_buffer_points: float = 5.0
    max_margin_fraction: float = 0.25
    max_open_positions: int = 1
    daily_soft_guard_pct: float = 0.01
    overall_soft_guard_pct: float = 0.04
    safety_buffer_pct: float = 0.005
    state_dir: Path = Path("data/mt5_demo_state")
    expected_login: int | None = None
    expected_server: str | None = None
    config_armed: bool = False
    order_sending_enabled: bool = False
    candidate_id: str | None = None
    candidate_validated: bool = False
    deployment_blockers: tuple[str, ...] = ()

    def validate(self) -> None:
        if not 0 < self.risk_fraction <= 0.005:
            raise ValueError("risk_fraction must be in (0, 0.005] for this mission")
        if not 0 < self.daily_soft_guard_pct < 0.05:
            raise ValueError("daily_soft_guard_pct must be in (0, 0.05)")
        if not 0 < self.overall_soft_guard_pct < 0.10:
            raise ValueError("overall_soft_guard_pct must be in (0, 0.10)")
        if not 0 < self.safety_buffer_pct < 0.05:
            raise ValueError("safety_buffer_pct must be in (0, 0.05)")
        if self.max_spread_points <= 0 or self.max_tick_age_seconds <= 0:
            raise ValueError("spread and tick-age caps must be positive")
        if not -12.0 <= self.server_utc_offset_hours <= 14.0:
            raise ValueError("server_utc_offset_hours out of range")
        if self.max_slippage_points < 0 or self.risk_buffer_points < 0:
            raise ValueError("slippage and risk buffers must be non-negative")
        if not 0 < self.max_margin_fraction < 1:
            raise ValueError("max_margin_fraction must be in (0, 1)")
        if self.max_open_positions < 0:
            raise ValueError("max_open_positions must be non-negative")
        if not 0 <= self.magic < 2**64:
            raise ValueError("magic must fit uint64")
        if self.account_initial_balance <= 0:
            raise ValueError("account_initial_balance must be positive")
        if self.commission_round_turn_per_lot_usd < 0:
            raise ValueError("commission must be non-negative")


@dataclass(frozen=True)
class LoadedRunnerConfig:
    settings: RunnerSettings
    profile: PropFirmRuleProfile
    compliance_state_path: Path
    config_sha256: str


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ValueError(f"missing {where}.{key}")
    return mapping[key]


def load_runner_config(path: str | Path) -> LoadedRunnerConfig:
    config_path = Path(path)
    raw = config_path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{config_path} is not a YAML mapping")

    environment = str(data.get("environment", ""))
    if environment != ENVIRONMENT:
        raise ValueError(
            f"runner requires environment={ENVIRONMENT!r}, got {environment!r}"
        )

    prop = _require(data, "prop_firm", "config")
    rules = _require(prop, "rules", "prop_firm")
    profile = get_profile(str(_require(prop, "profile_id", "prop_firm")))
    initial_balance = float(_require(prop, "initial_balance", "prop_firm"))
    account_sizes = [float(x) for x in _require(rules, "account_sizes_usd", "rules")]
    if initial_balance not in account_sizes:
        raise ValueError(
            f"initial_balance={initial_balance} is not an official account size {account_sizes}"
        )

    profile_checks = (
        ("profit_target_pct", float(_require(rules, "phase_1_target_pct", "rules"))),
        ("max_daily_loss_pct", float(_require(rules, "max_daily_loss_pct", "rules"))),
        ("max_total_loss_pct", float(_require(rules, "max_overall_loss_pct", "rules"))),
        ("min_trading_days", int(_require(rules, "min_trading_days", "rules"))),
        ("inactivity_max_days", int(_require(rules, "inactivity_max_days", "rules"))),
        ("daily_reset_utc_hour", int(_require(rules, "daily_reset_utc_hour", "rules"))),
    )
    for field_name, configured in profile_checks:
        actual = getattr(profile, field_name)
        if actual != configured:
            raise ValueError(
                f"config rules.{field_name}={configured!r} does not match "
                f"verified profile {profile.profile_id} value {actual!r}"
            )
    if profile.source_status is not RuleSourceStatus.EMPIRICAL:
        raise ValueError(
            f"profile {profile.profile_id} source_status={profile.source_status.value}; "
            "only EMPIRICAL rule profiles may be loaded for execution"
        )

    runner = data.get("mt5_demo_runner", {}) or {}
    deployment = data.get("deployment", {}) or {}
    costs = data.get("costs", {}) or {}
    sim = data.get("challenge_simulation", {}) or {}
    primary = sim.get("primary", {}) or {}

    project_root = config_path.resolve().parents[1]
    state_dir = Path(str(runner.get("state_dir", "data/mt5_demo_state")))
    if not state_dir.is_absolute():
        state_dir = project_root / state_dir
    compliance_state = Path(
        str(prop.get("state_file", "data/compliance_state_5k_2026.json"))
    )
    if not compliance_state.is_absolute():
        compliance_state = project_root / compliance_state

    blockers = tuple(str(x) for x in (deployment.get("blockers") or ()))
    if not blockers:
        blockers = ("NO_DEPLOYMENT_BLOCKER_LIST",)

    settings = RunnerSettings(
        symbol=str(runner.get("symbol", "XAUUSD")),
        account_initial_balance=initial_balance,
        risk_fraction=float(runner.get("risk_fraction", 0.0025)),
        max_spread_points=float(runner.get("max_spread_points", 50.0)),
        max_tick_age_seconds=float(runner.get("max_tick_age_seconds", 5.0)),
        server_utc_offset_hours=float(runner.get("server_utc_offset_hours", 0.0)),
        max_slippage_points=int(runner.get("max_slippage_points", 20)),
        magic=int(runner.get("magic", 20261022)),
        commission_round_turn_per_lot_usd=float(
            costs.get("primary_fundingpips_commission_round_turn_usd", 10.0)
        ),
        risk_buffer_points=float(runner.get("risk_buffer_points", 5.0)),
        max_margin_fraction=float(runner.get("max_margin_fraction", 0.25)),
        max_open_positions=int(runner.get("max_open_positions", 1)),
        daily_soft_guard_pct=float(
            primary.get("internal_daily_soft_guard_pct_of_start", 0.01)
        ),
        overall_soft_guard_pct=float(
            primary.get("internal_overall_soft_guard_pct_of_start", 0.04)
        ),
        safety_buffer_pct=float(prop.get("safety_buffer_pct", 0.005)),
        state_dir=state_dir,
        expected_login=(
            int(runner["expected_login"])
            if runner.get("expected_login") not in (None, "")
            else None
        ),
        expected_server=(
            str(runner["expected_server"])
            if runner.get("expected_server") not in (None, "")
            else None
        ),
        config_armed=bool(deployment.get("armed", False)),
        order_sending_enabled=bool(deployment.get("order_sending_enabled", False)),
        candidate_id=(
            str(deployment["candidate_id"])
            if deployment.get("candidate_id") not in (None, "")
            else None
        ),
        candidate_validated=bool(deployment.get("candidate_validated", False)),
        deployment_blockers=blockers,
    )
    settings.validate()

    return LoadedRunnerConfig(
        settings=settings,
        profile=replace(profile, initial_balance=initial_balance),
        compliance_state_path=compliance_state,
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )


# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------


@dataclass
class IntentRecord:
    intent_id: str
    state: str
    symbol: str
    side: str
    lots: float
    stop_distance: float
    target_distance: float
    request: dict[str, Any]
    comment: str
    created_utc: str
    updated_utc: str
    risk_usd: float = 0.0
    reason: str = ""
    retcode: int | None = None
    order_ticket: int | None = None
    deal_ticket: int | None = None
    position_ticket: int | None = None
    fill_price: float | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    environment: str = ENVIRONMENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "state": self.state,
            "symbol": self.symbol,
            "side": self.side,
            "lots": self.lots,
            "stop_distance": self.stop_distance,
            "target_distance": self.target_distance,
            "request": self.request,
            "comment": self.comment,
            "created_utc": self.created_utc,
            "updated_utc": self.updated_utc,
            "risk_usd": self.risk_usd,
            "reason": self.reason,
            "retcode": self.retcode,
            "order_ticket": self.order_ticket,
            "deal_ticket": self.deal_ticket,
            "position_ticket": self.position_ticket,
            "fill_price": self.fill_price,
            "verification": self.verification,
            "environment": self.environment,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IntentRecord:
        return cls(
            intent_id=str(data["intent_id"]),
            state=str(data["state"]),
            symbol=str(data["symbol"]),
            side=str(data["side"]),
            lots=float(data["lots"]),
            stop_distance=float(data["stop_distance"]),
            target_distance=float(data.get("target_distance", 0.0)),
            request=dict(data.get("request", {})),
            comment=str(data.get("comment", "")),
            created_utc=str(data["created_utc"]),
            updated_utc=str(data["updated_utc"]),
            risk_usd=float(data.get("risk_usd", 0.0)),
            reason=str(data.get("reason", "")),
            retcode=int(data["retcode"]) if data.get("retcode") is not None else None,
            order_ticket=(
                int(data["order_ticket"]) if data.get("order_ticket") is not None else None
            ),
            deal_ticket=(
                int(data["deal_ticket"]) if data.get("deal_ticket") is not None else None
            ),
            position_ticket=(
                int(data["position_ticket"])
                if data.get("position_ticket") is not None
                else None
            ),
            fill_price=(
                float(data["fill_price"]) if data.get("fill_price") is not None else None
            ),
            verification=dict(data.get("verification", {})),
            environment=str(data.get("environment", ENVIRONMENT)),
        )


class StateStore:
    def __init__(self, state_dir: Path | str) -> None:
        self.root = Path(state_dir)
        self.intents_dir = self.root / "intents"
        self.decisions_path = self.root / "decisions.jsonl"
        self.baseline_path = self.root / "account_baseline.json"
        self.kill_switch_path = self.root / "KILL_SWITCH"
        self.lock_path = self.root / "runner.lock"

    def ensure(self) -> None:
        self.intents_dir.mkdir(parents=True, exist_ok=True)

    # -- kill switch ------------------------------------------------------
    def kill_switch_reason(self) -> str | None:
        env = os.environ.get("QUANT_LAB_KILL_SWITCH", "").strip().lower()
        if env in {"1", "true", "yes", "on"}:
            return "ENV:QUANT_LAB_KILL_SWITCH"
        if self.kill_switch_path.exists():
            try:
                text = self.kill_switch_path.read_text(encoding="utf-8").strip()
            except OSError:
                return "KILL_SWITCH_FILE_UNREADABLE"
            return text or "KILL_SWITCH_FILE"
        return None

    def activate_kill_switch(self, reason: str) -> None:
        self.ensure()
        _atomic_write_json(
            self.kill_switch_path,
            {"reason": reason, "activated_utc": iso(), "environment": ENVIRONMENT},
        )

    def clear_kill_switch(self) -> None:
        if self.kill_switch_path.exists():
            self.kill_switch_path.unlink()

    # -- baseline ---------------------------------------------------------
    def load_baseline(self) -> dict[str, Any] | None:
        if not self.baseline_path.exists():
            return None
        try:
            data = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RunnerError(f"account baseline unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise RunnerError("account baseline is not a mapping")
        return data

    def save_baseline(
        self, *, login: int, server: str, currency: str, initial_balance: float
    ) -> dict[str, Any]:
        self.ensure()
        payload = {
            "login": int(login),
            "server": str(server),
            "currency": str(currency),
            "initial_balance": float(initial_balance),
            "created_utc": iso(),
            "environment": ENVIRONMENT,
        }
        _atomic_write_json(self.baseline_path, payload)
        return payload

    # -- intents ----------------------------------------------------------
    def intent_path(self, intent_id: str) -> Path:
        return self.intents_dir / f"{intent_id}.json"

    def save_intent(self, record: IntentRecord) -> None:
        self.ensure()
        _atomic_write_json(self.intent_path(record.intent_id), record.to_dict())

    def load_intents(self) -> list[IntentRecord]:
        if not self.intents_dir.exists():
            return []
        records: list[IntentRecord] = []
        for path in sorted(self.intents_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                records.append(IntentRecord.from_dict(data))
            except Exception as exc:  # noqa: BLE001
                raise RunnerError(f"intent file {path} is unreadable: {exc}") from exc
        return records

    def append_decision(self, event: Mapping[str, Any]) -> None:
        self.ensure()
        line = json.dumps(dict(event), sort_keys=True, default=str)
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # -- concurrency ------------------------------------------------------
    @contextmanager
    def run_lock(self) -> Iterator[None]:
        self.ensure()
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            detail = ""
            with suppress(OSError):
                detail = self.lock_path.read_text(encoding="utf-8").strip()
            raise RunnerBusy(
                f"execution lock exists at {self.lock_path} ({detail}); "
                "another runner may be active. Remove it manually only after "
                "confirming no runner is alive."
            ) from exc
        try:
            os.write(fd, f"pid={os.getpid()} utc={iso()}".encode())
            os.close(fd)
            yield
        finally:
            with suppress(FileNotFoundError):
                self.lock_path.unlink()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    side: str
    stop_distance: float
    target_distance: float = 0.0
    candidate_id: str | None = None

    def validated(self) -> Signal:
        side = self.side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"signal side must be BUY or SELL, got {self.side!r}")
        if not math.isfinite(self.stop_distance) or self.stop_distance <= 0:
            raise ValueError("stop_distance must be finite and positive")
        if not math.isfinite(self.target_distance) or self.target_distance < 0:
            raise ValueError("target_distance must be finite and non-negative")
        return Signal(side, float(self.stop_distance), float(self.target_distance), self.candidate_id)


@dataclass
class Decision:
    action: str
    reason_code: str
    detail: dict[str, Any]
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    intent_id: str | None = None
    timestamp: str = field(default_factory=iso)
    environment: str = ENVIRONMENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "decision_id": self.decision_id,
            "intent_id": self.intent_id,
            "timestamp": self.timestamp,
            "action": self.action,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


@dataclass
class Preflight:
    demo: DemoVerdict | None
    spec: SymbolSpec | None
    tick: TickSnapshot | None
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "demo": self.demo.to_dict() if self.demo else None,
            "symbol_spec": self.spec.to_dict() if self.spec else None,
            "tick": self.tick.to_dict() if self.tick else None,
        }


def choose_filling(spec: SymbolSpec) -> int:
    return spec.choose_filling()


class DemoRunner:
    """Execution safety layer. It never invents prices, fills, or P&L."""

    def __init__(
        self,
        client: MT5DemoClient,
        settings: RunnerSettings,
        profile: PropFirmRuleProfile,
        compliance_state_path: Path | str,
        *,
        config_sha256: str = "",
        now_fn: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        settings.validate()
        self.client = client
        self.settings = settings
        self.profile = profile
        self.compliance_state_path = Path(compliance_state_path)
        self.config_sha256 = config_sha256
        self.store = StateStore(settings.state_dir)
        self._now = now_fn
        self._sleep = sleep

    # -- preflight --------------------------------------------------------
    def preflight(self, *, require_terminal: bool = False) -> Preflight:
        reasons: list[str] = []
        warnings: list[str] = []

        demo: DemoVerdict | None = None
        try:
            demo = self.client.verify_demo(require_terminal=require_terminal)
            reasons.extend(demo.reasons)
        except MT5ReadError as exc:
            reasons.append(f"DEMO_CHECK_UNAVAILABLE: {exc}")

        spec: SymbolSpec | None = None
        try:
            spec = self.client.build_symbol_spec(self.settings.symbol)
            warnings.extend(spec.warnings)
            if spec.trade_mode != SYMBOL_TRADE_MODE_FULL:
                reasons.append(f"SYMBOL_NOT_FULLY_TRADABLE(trade_mode={spec.trade_mode})")
        except (SpecError, MT5ReadError) as exc:
            reasons.append(f"SYMBOL_SPEC_INVALID: {exc}")

        tick: TickSnapshot | None = None
        if spec is not None:
            try:
                raw_tick = self.client.tick(self.settings.symbol)
                tick = validate_tick(
                    raw_tick,
                    spec,
                    self._now(),
                    max_age_seconds=self.settings.max_tick_age_seconds,
                    max_spread_points=self.settings.max_spread_points,
                    server_utc_offset_seconds=self.settings.server_utc_offset_hours * 3600.0,
                )
                reasons.extend(f"TICK_{reason}" for reason in tick.reasons)
            except MT5ReadError as exc:
                reasons.append(f"TICK_UNAVAILABLE: {exc}")

        return Preflight(
            demo=demo,
            spec=spec,
            tick=tick,
            reasons=tuple(reasons),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def deployment_blockers(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.settings.config_armed:
            reasons.append("CONFIG_NOT_ARMED")
        if not self.settings.order_sending_enabled:
            reasons.append("ORDER_SENDING_DISABLED")
        if not self.settings.candidate_id:
            reasons.append("NO_CANDIDATE_ID")
        if not self.settings.candidate_validated:
            reasons.append("CANDIDATE_NOT_VALIDATED")
        if self.settings.deployment_blockers:
            reasons.append("DEPLOYMENT_BLOCKERS_PRESENT")
        if self.profile.source_status is not RuleSourceStatus.EMPIRICAL:
            reasons.append(f"RULES_{self.profile.source_status.value}")
        return tuple(reasons)

    def _baseline_blockers(self, demo: DemoVerdict) -> tuple[str, ...]:
        if demo.balance is None or demo.equity is None:
            return ("ACCOUNT_VALUES_UNAVAILABLE",)
        reasons: list[str] = []
        baseline = self.store.load_baseline()
        if baseline is None:
            if abs(demo.balance - self.settings.account_initial_balance) > 0.01:
                reasons.append(
                    f"ACCOUNT_BALANCE_NOT_INITIAL({demo.balance:.2f}!="
                    f"{self.settings.account_initial_balance:.2f})"
                )
            if abs(demo.equity - demo.balance) > 0.01:
                reasons.append("ACCOUNT_HAS_OPEN_EXPOSURE_AT_BASELINE")
            return tuple(reasons)

        if int(baseline.get("login", -1)) != int(demo.login or -1):
            reasons.append("BASELINE_LOGIN_MISMATCH")
        if str(baseline.get("server", "")) != str(demo.server or ""):
            reasons.append("BASELINE_SERVER_MISMATCH")
        if abs(float(baseline.get("initial_balance", 0.0)) - self.settings.account_initial_balance) > 0.01:
            reasons.append("BASELINE_INITIAL_BALANCE_MISMATCH")
        if str(baseline.get("currency", "")) != str(demo.currency or ""):
            reasons.append("BASELINE_CURRENCY_MISMATCH")
        return tuple(reasons)

    def _ensure_baseline(self, demo: DemoVerdict) -> dict[str, Any]:
        existing = self.store.load_baseline()
        if existing is not None:
            return existing
        if demo.login is None or demo.server is None or demo.currency is None:
            raise RunnerError("cannot establish baseline without login/server/currency")
        return self.store.save_baseline(
            login=demo.login,
            server=demo.server,
            currency=demo.currency,
            initial_balance=self.settings.account_initial_balance,
        )

    def reconcile(self, *, write: bool = False) -> dict[str, Any]:
        positions = self.client.positions(self.settings.symbol)
        orders = self.client.pending_orders(self.settings.symbol)
        intents = self.store.load_intents()

        position_by_comment: dict[str, list[Any]] = {}
        for position in positions:
            comment = str(getattr(position, "comment", "") or "")
            position_by_comment.setdefault(comment, []).append(position)
        order_by_comment: dict[str, list[Any]] = {}
        for order in orders:
            comment = str(getattr(order, "comment", "") or "")
            order_by_comment.setdefault(comment, []).append(order)

        updates: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for intent in intents:
            if intent.state != "PENDING":
                if intent.state in {"UNKNOWN", "PENDING_ORDER", "VERIFICATION_FAILED"}:
                    unresolved.append(intent.intent_id)
                continue
            matches = position_by_comment.get(intent.comment, [])
            matching_orders = order_by_comment.get(intent.comment, [])
            now_text = iso(self._now())
            if len(matches) == 1:
                verification = self._verify_position(intent, matches[0])
                new_state = "FILLED" if verification["ok"] else "VERIFICATION_FAILED"
                intent.state = new_state
                intent.position_ticket = int(getattr(matches[0], "ticket", 0) or 0)
                intent.fill_price = float(getattr(matches[0], "price_open", 0.0) or 0.0)
                intent.verification = verification
                intent.updated_utc = now_text
                if not verification["ok"]:
                    intent.reason = "RECONCILED_POSITION_MISMATCH"
                    unresolved.append(intent.intent_id)
                updates.append({"intent_id": intent.intent_id, "state": new_state})
                if write:
                    self.store.save_intent(intent)
            elif len(matches) > 1:
                intent.state = "VERIFICATION_FAILED"
                intent.reason = f"MULTIPLE_POSITIONS_FOR_INTENT({len(matches)})"
                intent.updated_utc = now_text
                unresolved.append(intent.intent_id)
                updates.append({"intent_id": intent.intent_id, "state": intent.state})
                if write:
                    self.store.save_intent(intent)
            elif len(matching_orders) >= 1:
                intent.state = "PENDING_ORDER"
                intent.reason = "ORDER_STILL_PENDING_AT_BROKER"
                intent.updated_utc = now_text
                unresolved.append(intent.intent_id)
                updates.append({"intent_id": intent.intent_id, "state": intent.state})
                if write:
                    self.store.save_intent(intent)
            else:
                intent.state = "UNKNOWN"
                intent.reason = "INTENT_NOT_FOUND_AT_BROKER"
                intent.updated_utc = now_text
                unresolved.append(intent.intent_id)
                updates.append({"intent_id": intent.intent_id, "state": intent.state})
                if write:
                    self.store.save_intent(intent)

        ours = [
            position
            for position in positions
            if int(getattr(position, "magic", 0) or 0) == self.settings.magic
        ]
        foreign = [
            position
            for position in positions
            if int(getattr(position, "magic", 0) or 0) != self.settings.magic
        ]
        orphan = []
        known_comments = {intent.comment for intent in intents}
        for position in ours:
            comment = str(getattr(position, "comment", "") or "")
            if comment not in known_comments:
                orphan.append(_position_dict(position))

        position_mismatch = []
        for intent in intents:
            if intent.state == "FILLED" and intent.position_ticket:
                for position in ours:
                    if int(getattr(position, "ticket", 0) or 0) == intent.position_ticket:
                        verification = self._verify_position(intent, position)
                        if not verification["ok"]:
                            position_mismatch.append(
                                {"intent_id": intent.intent_id, "verification": verification}
                            )

        open_ours = len(ours)
        ok_for_new_entries = (
            not unresolved
            and not orphan
            and not foreign
            and not position_mismatch
            and open_ours == 0
        )
        reasons: list[str] = []
        if unresolved:
            reasons.append(f"UNRESOLVED_INTENTS({','.join(unresolved)})")
        if orphan:
            reasons.append(f"ORPHAN_POSITIONS({len(orphan)})")
        if foreign:
            reasons.append(f"FOREIGN_POSITIONS({len(foreign)})")
        if position_mismatch:
            reasons.append("OPEN_POSITION_VERIFICATION_MISMATCH")
        if open_ours > self.settings.max_open_positions:
            reasons.append(f"MAX_OPEN_POSITIONS_EXCEEDED({open_ours})")
        elif open_ours > 0:
            reasons.append(f"POSITION_ALREADY_OPEN({open_ours})")

        return {
            "environment": ENVIRONMENT,
            "ok_for_new_entries": ok_for_new_entries,
            "reasons": reasons,
            "open_positions": open_ours,
            "foreign_positions": len(foreign),
            "orphan_positions": orphan,
            "unresolved_intents": unresolved,
            "updates": updates,
            "positions": [_position_dict(p) for p in ours],
        }

    def _verify_position(self, intent: IntentRecord, position: Any) -> dict[str, Any]:
        point = float(intent.verification.get("point", 0.01) or 0.01)
        min_volume = float(intent.verification.get("volume_min", 0.01) or 0.01)
        checks: dict[str, Any] = {}
        sl = float(getattr(position, "sl", 0.0) or 0.0)
        tp = float(getattr(position, "tp", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        price_open = float(getattr(position, "price_open", 0.0) or 0.0)
        position_type = int(getattr(position, "type", -1))
        magic = int(getattr(position, "magic", 0) or 0)
        comment = str(getattr(position, "comment", "") or "")

        requested_volume = float(intent.request.get("volume", 0.0))
        requested_sl = float(intent.request.get("sl", 0.0))
        requested_tp = float(intent.request.get("tp", 0.0))
        requested_price = float(intent.request.get("price", 0.0))
        tolerance = max(point * 0.51, 1e-9)
        expected_type = ORDER_TYPE_BUY if intent.side == "BUY" else ORDER_TYPE_SELL

        checks["magic"] = magic == self.settings.magic
        checks["comment"] = comment.startswith(intent.comment)
        checks["type"] = position_type == expected_type
        checks["sl"] = abs(sl - requested_sl) <= tolerance
        checks["tp"] = abs(tp - requested_tp) <= tolerance if requested_tp > 0 else tp == 0
        checks["price_open"] = abs(price_open - requested_price) <= (
            self.settings.max_slippage_points * point + tolerance
        )
        checks["volume_not_above_request"] = volume <= requested_volume + tolerance
        checks["volume_at_least_min"] = volume > 0 and volume + tolerance >= min_volume
        checks["sl_set"] = sl > 0
        partial = volume + tolerance < requested_volume
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "partial_fill": partial,
            "position_ticket": getattr(position, "ticket", None),
            "point": point,
            "volume_min": min_volume,
        }

    # -- check-only report ------------------------------------------------
    def check_report(self, *, run_order_check: bool = False) -> dict[str, Any]:
        pre = self.preflight(require_terminal=False)
        reasons = list(pre.reasons)
        reasons.extend(self.deployment_blockers())

        baseline_reasons: tuple[str, ...] = ()
        if pre.demo is not None:
            try:
                baseline_reasons = self._baseline_blockers(pre.demo)
            except RunnerError as exc:
                baseline_reasons = (f"BASELINE_UNREADABLE: {exc}",)
            reasons.extend(baseline_reasons)
            if pre.demo.terminal_trade_allowed is not True:
                reasons.append("TERMINAL_AUTOTRADING_DISABLED")
            if pre.demo.account_trade_expert is not True:
                reasons.append("EA_TRADING_DISABLED")

        reconciliation: dict[str, Any] | None = None
        try:
            reconciliation = self.reconcile(write=False)
            reasons.extend(reconciliation["reasons"])
        except (MT5ReadError, RunnerError) as exc:
            reasons.append(f"RECONCILIATION_UNAVAILABLE: {exc}")

        kill_reason = self.store.kill_switch_reason()
        if kill_reason:
            reasons.append(f"KILL_SWITCH:{kill_reason}")

        order_check: dict[str, Any] | None = None
        if run_order_check and pre.spec is not None and pre.tick is not None and pre.tick.ok:
            order_check = self._plumbing_check(pre.spec, pre.tick)

        reasons = list(dict.fromkeys(reasons))
        return {
            "environment": ENVIRONMENT,
            "generated_utc": iso(self._now()),
            "config_sha256": self.config_sha256,
            "account": pre.demo.to_dict() if pre.demo else None,
            "terminal": (
                {
                    "connected": pre.demo.terminal_connected,
                    "trade_allowed": pre.demo.terminal_trade_allowed,
                    "company": pre.demo.terminal_company,
                    "name": pre.demo.terminal_name,
                }
                if pre.demo
                else None
            ),
            "demo_verification": pre.demo.to_dict() if pre.demo else None,
            "symbol_spec": pre.spec.to_dict() if pre.spec else None,
            "tick": pre.tick.to_dict() if pre.tick else None,
            "server_time": {
                "server_utc_offset_hours": self.settings.server_utc_offset_hours,
                "note": "MT5 tick timestamps are broker server time; normalized by this offset.",
            },
            "sizing_preview": self._sizing_preview(pre.spec) if pre.spec else None,
            "runner_settings": {
                "risk_fraction": self.settings.risk_fraction,
                "max_spread_points": self.settings.max_spread_points,
                "max_tick_age_seconds": self.settings.max_tick_age_seconds,
                "max_slippage_points": self.settings.max_slippage_points,
                "commission_round_turn_per_lot_usd": self.settings.commission_round_turn_per_lot_usd,
                "risk_buffer_points": self.settings.risk_buffer_points,
                "max_margin_fraction": self.settings.max_margin_fraction,
                "max_open_positions": self.settings.max_open_positions,
                "magic": self.settings.magic,
            },
            "deployment": {
                "armed": self.settings.config_armed,
                "order_sending_enabled": self.settings.order_sending_enabled,
                "candidate_id": self.settings.candidate_id,
                "candidate_validated": self.settings.candidate_validated,
                "blockers": list(self.settings.deployment_blockers),
                "gate_reasons": list(self.deployment_blockers()),
            },
            "execution_gate": {
                "status": "READY" if not reasons else "BLOCKED",
                "reasons": reasons,
                "baseline_reasons": list(baseline_reasons),
            },
            "reconciliation": reconciliation,
            "kill_switch": kill_reason,
            "order_plumbing": order_check,
            "caveats": [
                "Order plumbing uses order_check only; it never transmits an order.",
                "A READY status would still require a validated candidate and human sign-off.",
                "Simulated/demo results are not proof of live viability.",
            ],
        }

    # -- monitoring (no order path) ---------------------------------------
    def monitor(self) -> Decision:
        """Check floating equity and kill the account without sending orders.

        Intended to run from a scheduler while a demo position is open. It
        updates compliance state from the live account, reconciles intents,
        and activates the local kill switch when an internal or firm limit is
        reached. Closing positions remains a human action; this method cannot
        transmit an order.
        """
        try:
            with self.store.run_lock():
                return self._monitor_locked()
        except RunnerBusy as exc:
            return self._finish(Decision("BLOCKED", "RUNNER_BUSY", {"error": str(exc)}))

    def _monitor_locked(self) -> Decision:
        kill_reason = self.store.kill_switch_reason()
        if kill_reason:
            return self._finish(
                Decision("HALT", "KILL_SWITCH_ACTIVE", {"reason": kill_reason})
            )

        try:
            demo = self.client.verify_demo(require_terminal=True)
        except MT5ReadError as exc:
            return self._finish(
                Decision("BLOCKED", "DEMO_CHECK_UNAVAILABLE", {"error": str(exc)})
            )
        if not demo.ok:
            return self._finish(
                Decision("BLOCKED", "DEMO_VERIFICATION_FAILED", demo.to_dict())
            )

        baseline = self.store.load_baseline()
        if baseline is None:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "NO_BASELINE",
                    {"note": "Monitor never creates the account baseline."},
                )
            )
        baseline_reasons = self._baseline_blockers(demo)
        if baseline_reasons:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "ACCOUNT_BASELINE",
                    {"reasons": list(baseline_reasons), "account": demo.to_dict()},
                )
            )
        if demo.balance is None or demo.equity is None:
            return self._finish(
                Decision("BLOCKED", "ACCOUNT_VALUES_UNAVAILABLE", {})
            )

        try:
            reconciliation = self.reconcile(write=True)
        except (MT5ReadError, RunnerError) as exc:
            self.store.activate_kill_switch(f"MONITOR_RECONCILIATION_ERROR:{exc}")
            return self._finish(
                Decision("HALT", "RECONCILIATION_ERROR", {"error": str(exc)})
            )

        now = self._now()
        try:
            compliance = self._compliance_engine(baseline, now)
            compliance.update_equity(demo.balance, demo.equity, now)
            report = compliance.generate_report()
        except Exception as exc:  # noqa: BLE001 - corrupt state must halt
            self.store.activate_kill_switch(f"MONITOR_COMPLIANCE_ERROR:{exc}")
            return self._finish(
                Decision("HALT", "COMPLIANCE_STATE_ERROR", {"error": str(exc)})
            )

        daily_soft_limit = (
            self.settings.account_initial_balance * self.settings.daily_soft_guard_pct
        )
        overall_soft_limit = (
            self.settings.account_initial_balance * self.settings.overall_soft_guard_pct
        )
        alerts: list[str] = []
        if report["state"] in {"BREACHED", "HALTED"}:
            alerts.append(f"FIRM_LIMIT_STATE:{report['state']}")
        if float(report["daily_exposure_usd"]) >= daily_soft_limit:
            alerts.append(
                f"DAILY_SOFT_LIMIT({report['daily_exposure_usd']:.2f}>={daily_soft_limit:.2f})"
            )
        if float(report["total_exposure_usd"]) >= overall_soft_limit:
            alerts.append(
                f"OVERALL_SOFT_LIMIT({report['total_exposure_usd']:.2f}>={overall_soft_limit:.2f})"
            )
        if not reconciliation["ok_for_new_entries"]:
            alerts.append("RECONCILIATION:" + ",".join(reconciliation["reasons"]))

        detail = {
            "account": demo.to_dict(),
            "compliance": report,
            "limits": {
                "daily_soft_usd": daily_soft_limit,
                "overall_soft_usd": overall_soft_limit,
            },
            "reconciliation": reconciliation,
        }
        if alerts:
            reason = "; ".join(alerts)
            self.store.activate_kill_switch(f"MONITOR_ALERT:{reason}")
            return self._finish(
                Decision("HALT", "MONITOR_ALERT", {"alerts": alerts, **detail})
            )
        return self._finish(Decision("MONITOR_OK", "MONITOR_OK", detail))

    def _sizing_preview(self, spec: SymbolSpec) -> dict[str, Any]:
        stop_distance = 10.0
        extra_per_lot = (
            self.settings.commission_round_turn_per_lot_usd
            + self.settings.risk_buffer_points * spec.point * spec.profit_per_unit_per_lot
        )
        decision = size_position(
            spec,
            equity=self.settings.account_initial_balance,
            risk_fraction=self.settings.risk_fraction,
            stop_distance=stop_distance,
            extra_cost_per_lot_usd=extra_per_lot,
        )
        payload = decision.to_dict()
        payload.update(
            {
                "hypothetical_account_balance": self.settings.account_initial_balance,
                "risk_fraction": self.settings.risk_fraction,
                "stop_distance": stop_distance,
                "commission_plus_buffer_per_lot_usd": extra_per_lot,
                "note": (
                    "Hypothetical sizing on a $5,000 account using live broker "
                    "specs. No order, no baseline, no account-state change."
                ),
            }
        )
        return payload

    def _plumbing_check(self, spec: SymbolSpec, tick: TickSnapshot) -> dict[str, Any]:
        try:
            stop_distance = max(spec.minimum_stop_distance(), 1000 * spec.point)
            filling = choose_filling(spec)
            if tick.ask <= tick.bid:
                return {"performed": False, "reason": "CROSSED_QUOTE"}
            request = {
                "action": TRADE_ACTION_DEAL,
                "symbol": spec.symbol,
                "volume": spec.volume_min,
                "type": ORDER_TYPE_BUY,
                "price": tick.ask,
                "sl": round(tick.ask - stop_distance, spec.digits),
                "tp": 0.0,
                "deviation": self.settings.max_slippage_points,
                "magic": self.settings.magic,
                "comment": "QL-PLUMBING",
                "type_time": ORDER_TIME_GTC,
                "type_filling": filling,
            }
            result = self.client.order_check(request)
            return {
                "performed": True,
                "transmitted": False,
                "request": request,
                "retcode": result.get("retcode"),
                "retcode_name": RETCODE_NAMES.get(int(result.get("retcode", -1)), "UNKNOWN"),
                "margin": result.get("margin"),
                "margin_free": result.get("margin_free"),
                "comment": result.get("comment"),
                "note": "order_check validates margin/price only; no order is placed.",
            }
        except (RunnerError, MT5ReadError) as exc:
            return {"performed": False, "reason": str(exc)}

    # -- execution --------------------------------------------------------
    def execute(self, signal: Signal | None, *, establish_baseline: bool = True) -> Decision:
        try:
            with self.store.run_lock():
                return self._execute_locked(signal, establish_baseline=establish_baseline)
        except RunnerBusy as exc:
            return self._finish(
                Decision("BLOCKED", "RUNNER_BUSY", {"error": str(exc)})
            )

    def _execute_locked(self, signal: Signal | None, *, establish_baseline: bool) -> Decision:
        kill_reason = self.store.kill_switch_reason()
        if kill_reason:
            return self._finish(
                Decision("BLOCKED", "KILL_SWITCH", {"reason": kill_reason})
            )

        if signal is None:
            return self._finish(Decision("BLOCKED", "NO_SIGNAL", {}))

        try:
            signal = signal.validated()
        except ValueError as exc:
            return self._finish(Decision("BLOCKED", "INVALID_SIGNAL", {"error": str(exc)}))

        if not self.settings.candidate_id or not self.settings.candidate_validated:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "NO_VALIDATED_CANDIDATE",
                    {"candidate_id": self.settings.candidate_id},
                )
            )
        if signal.candidate_id and signal.candidate_id != self.settings.candidate_id:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "CANDIDATE_MISMATCH",
                    {
                        "signal_candidate": signal.candidate_id,
                        "deployment_candidate": self.settings.candidate_id,
                    },
                )
            )

        deployment = self.deployment_blockers()
        if deployment:
            return self._finish(
                Decision("BLOCKED", "DEPLOYMENT_GATE", {"reasons": list(deployment)})
            )

        pre = self.preflight(require_terminal=True)
        if not pre.ok or pre.spec is None or pre.tick is None or pre.demo is None:
            return self._finish(
                Decision("BLOCKED", "PREFLIGHT_FAILED", pre.to_dict())
            )

        try:
            baseline_reasons = self._baseline_blockers(pre.demo)
        except RunnerError as exc:
            return self._finish(
                Decision("BLOCKED", "BASELINE_ERROR", {"error": str(exc)})
            )
        if baseline_reasons:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "ACCOUNT_BASELINE",
                    {"reasons": list(baseline_reasons), "account": pre.demo.to_dict()},
                )
            )
        if not establish_baseline:
            return self._finish(
                Decision("BLOCKED", "BASELINE_NOT_ESTABLISHED", {})
            )
        try:
            baseline = self._ensure_baseline(pre.demo)
        except (RunnerError, OSError) as exc:
            return self._finish(Decision("BLOCKED", "BASELINE_ERROR", {"error": str(exc)}))

        try:
            reconciliation = self.reconcile(write=True)
        except (MT5ReadError, RunnerError) as exc:
            return self._finish(Decision("BLOCKED", "RECONCILIATION_ERROR", {"error": str(exc)}))
        if not reconciliation["ok_for_new_entries"]:
            return self._finish(
                Decision("BLOCKED", "RECONCILIATION", {"reconciliation": reconciliation})
            )

        now = self._now()
        demo = pre.demo
        if demo.balance is None or demo.equity is None:
            return self._finish(Decision("BLOCKED", "ACCOUNT_VALUES_UNAVAILABLE", {}))
        if demo.login is None or demo.login != int(baseline["login"]):
            return self._finish(Decision("BLOCKED", "BASELINE_LOGIN_MISMATCH", {}))

        try:
            compliance = self._compliance_engine(baseline, now)
            compliance.update_equity(demo.balance, demo.equity, now)
        except Exception as exc:  # noqa: BLE001 - corrupt state must block
            return self._finish(
                Decision("BLOCKED", "COMPLIANCE_STATE_ERROR", {"error": str(exc)})
            )

        report = compliance.generate_report()
        daily_soft_limit = self.settings.account_initial_balance * self.settings.daily_soft_guard_pct
        overall_soft_limit = self.settings.account_initial_balance * self.settings.overall_soft_guard_pct
        if report["daily_exposure_usd"] >= daily_soft_limit:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "INTERNAL_DAILY_STOP",
                    {"daily_exposure_usd": report["daily_exposure_usd"], "limit": daily_soft_limit},
                )
            )
        if report["total_exposure_usd"] >= overall_soft_limit:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "INTERNAL_OVERALL_STOP",
                    {"total_exposure_usd": report["total_exposure_usd"], "limit": overall_soft_limit},
                )
            )

        preliminary_risk = demo.equity * self.settings.risk_fraction
        verdict = compliance.check_trade_eligible(
            risk_amount=preliminary_risk, open_risk_amount=0.0, now=now
        )
        if not verdict.can_proceed:
            return self._finish(
                Decision(
                    "BLOCKED",
                    f"COMPLIANCE_{verdict.code.value}",
                    {
                        "reason": verdict.reason,
                        "daily_room_remaining": verdict.daily_room_remaining,
                        "total_room_remaining": verdict.total_room_remaining,
                    },
                )
            )

        spec = pre.spec
        try:
            sizing = size_position(
                spec,
                equity=demo.equity,
                risk_fraction=self.settings.risk_fraction,
                stop_distance=signal.stop_distance,
                extra_cost_per_lot_usd=(
                    self.settings.commission_round_turn_per_lot_usd
                    + self.settings.risk_buffer_points * spec.point * spec.profit_per_unit_per_lot
                ),
                daily_room_usd=verdict.daily_room_remaining,
                overall_room_usd=verdict.total_room_remaining,
            )
        except (ValueError, SpecError) as exc:
            return self._finish(Decision("BLOCKED", "SIZING_ERROR", {"error": str(exc)}))
        if not sizing.approved:
            return self._finish(Decision("BLOCKED", f"SIZING_{sizing.reason}", sizing.to_dict()))

        intent_id = uuid.uuid4().hex[:16]
        comment = f"QL{intent_id}"
        try:
            request = self._build_request(spec, pre.tick, signal, sizing.lots, comment)
        except (RunnerError, ValueError) as exc:
            return self._finish(
                Decision("BLOCKED", "ORDER_BUILD_FAILED", {"error": str(exc)}, intent_id=intent_id)
            )

        actual_stop = abs(float(request["price"]) - float(request["sl"]))
        risk_usd = sizing.lots * spec.risk_per_lot(
            actual_stop, self.settings.commission_round_turn_per_lot_usd
        )
        if risk_usd > sizing.risk_budget_usd + 1e-9:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "ROUNDED_STOP_EXCEEDS_BUDGET",
                    {"risk_usd": risk_usd, "budget": sizing.risk_budget_usd},
                    intent_id=intent_id,
                )
            )
        recheck = compliance.check_trade_eligible(
            risk_amount=risk_usd, open_risk_amount=0.0, now=now
        )
        if not recheck.can_proceed:
            return self._finish(
                Decision(
                    "BLOCKED",
                    f"COMPLIANCE_{recheck.code.value}",
                    {"reason": recheck.reason},
                    intent_id=intent_id,
                )
            )

        check_result = self.client.order_check(request)
        check_retcode = int(check_result.get("retcode", -1))
        check_detail = {
            "retcode": check_retcode,
            "retcode_name": RETCODE_NAMES.get(check_retcode, "UNKNOWN_OR_DONE"),
            "margin": check_result.get("margin"),
            "margin_free": check_result.get("margin_free"),
            "comment": check_result.get("comment"),
        }
        if check_retcode not in (0, TRADE_RETCODE_DONE):
            return self._finish(
                Decision("BLOCKED", "ORDER_CHECK_REJECTED", check_detail, intent_id=intent_id)
            )
        margin = check_result.get("margin")
        if margin is None or not math.isfinite(float(margin)) or float(margin) <= 0:
            return self._finish(
                Decision("BLOCKED", "MARGIN_UNKNOWN", check_detail, intent_id=intent_id)
            )
        if float(margin) > demo.equity * self.settings.max_margin_fraction:
            return self._finish(
                Decision(
                    "BLOCKED",
                    "MARGIN_TOO_HIGH",
                    {"margin": float(margin), "cap": demo.equity * self.settings.max_margin_fraction},
                    intent_id=intent_id,
                )
            )
        if check_result.get("margin_free") is not None and float(check_result["margin_free"]) < 0:
            return self._finish(Decision("BLOCKED", "INSUFFICIENT_FREE_MARGIN", check_detail))

        record = IntentRecord(
            intent_id=intent_id,
            state="PENDING",
            symbol=spec.symbol,
            side=signal.side,
            lots=sizing.lots,
            stop_distance=signal.stop_distance,
            target_distance=signal.target_distance,
            request=dict(request),
            comment=comment,
            created_utc=iso(now),
            updated_utc=iso(now),
            risk_usd=risk_usd,
            reason="",
            verification={"point": spec.point, "volume_min": spec.volume_min},
        )
        try:
            self.store.save_intent(record)
        except OSError as exc:
            return self._finish(
                Decision("BLOCKED", "INTENT_PERSIST_FAILED", {"error": str(exc)}, intent_id=intent_id)
            )

        # Re-verify demo and freshness immediately before the single send.
        try:
            demo2 = self.client.verify_demo(require_terminal=True)
            tick2_raw = self.client.tick(spec.symbol)
            tick2 = validate_tick(
                tick2_raw,
                spec,
                self._now(),
                max_age_seconds=self.settings.max_tick_age_seconds,
                max_spread_points=self.settings.max_spread_points,
                server_utc_offset_seconds=self.settings.server_utc_offset_hours * 3600.0,
            )
        except (MT5ReadError, ValueError) as exc:
            record.state = "CANCELLED"
            record.reason = f"PRE_SEND_VERIFICATION_UNAVAILABLE: {exc}"
            record.updated_utc = iso(self._now())
            self.store.save_intent(record)
            return self._finish(
                Decision("BLOCKED", "PRE_SEND_VERIFICATION", {"error": str(exc)}, intent_id=intent_id)
            )
        if not demo2.ok or demo2.login != baseline["login"] or not tick2.ok:
            record.state = "CANCELLED"
            record.reason = f"PRE_SEND_VERIFICATION_FAILED: {demo2.reasons} {tick2.reasons}"
            record.updated_utc = iso(self._now())
            self.store.save_intent(record)
            return self._finish(
                Decision(
                    "BLOCKED",
                    "PRE_SEND_VERIFICATION",
                    {"demo": demo2.to_dict(), "tick": tick2.to_dict()},
                    intent_id=intent_id,
                )
            )

        try:
            result = self.client.order_send(request)
        except UNCERTAIN_EXCEPTIONS as exc:
            record.state = "UNKNOWN"
            record.reason = f"SEND_OUTCOME_UNKNOWN: {exc}"
            record.updated_utc = iso(self._now())
            self.store.save_intent(record)
            self.store.activate_kill_switch(record.reason)
            return self._finish(
                Decision("UNKNOWN", "SEND_OUTCOME_UNKNOWN", {"error": str(exc)}, intent_id=intent_id)
            )
        except Exception as exc:  # noqa: BLE001 - unknown outcome, fail closed
            record.state = "UNKNOWN"
            record.reason = f"SEND_EXCEPTION: {exc}"
            record.updated_utc = iso(self._now())
            self.store.save_intent(record)
            self.store.activate_kill_switch(record.reason)
            return self._finish(
                Decision("UNKNOWN", "SEND_EXCEPTION", {"error": str(exc)}, intent_id=intent_id)
            )

        retcode = int(result.get("retcode", -1))
        record.retcode = retcode
        record.order_ticket = int(result["order"]) if result.get("order") not in (None, 0) else None
        record.deal_ticket = int(result["deal"]) if result.get("deal") not in (None, 0) else None
        record.fill_price = (
            float(result["price"]) if result.get("price") not in (None, 0.0) else None
        )
        record.updated_utc = iso(self._now())

        if retcode in {TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL}:
            verification = self._wait_and_verify(record)
            record.verification = verification
            record.updated_utc = iso(self._now())
            if verification["ok"]:
                record.state = "FILLED"
                self.store.save_intent(record)
                return self._finish(
                    Decision(
                        "FILLED",
                        "FILL_VERIFIED",
                        {
                            "retcode": retcode,
                            "retcode_name": RETCODE_NAMES.get(retcode, "UNKNOWN"),
                            "request": request,
                            "result": {
                                key: result.get(key)
                                for key in ("order", "deal", "price", "volume", "comment")
                            },
                            "verification": verification,
                            "lots": sizing.lots,
                            "risk_usd": risk_usd,
                            "compliance": {
                                "daily_room_remaining": verdict.daily_room_remaining,
                                "total_room_remaining": verdict.total_room_remaining,
                            },
                        },
                        intent_id=intent_id,
                    )
                )
            record.state = "VERIFICATION_FAILED"
            record.reason = "FILL_VERIFICATION_FAILED"
            self.store.save_intent(record)
            self.store.activate_kill_switch(record.reason)
            return self._finish(
                Decision(
                    "VERIFICATION_FAILED",
                    "FILL_VERIFICATION_FAILED",
                    {"retcode": retcode, "verification": verification, "request": request},
                    intent_id=intent_id,
                )
            )

        if retcode in UNCERTAIN_RETCODES or retcode < 0:
            record.state = "UNKNOWN"
            record.reason = f"UNCERTAIN_RETCODE_{retcode}_{RETCODE_NAMES.get(retcode, 'UNKNOWN')}"
            self.store.save_intent(record)
            self.store.activate_kill_switch(record.reason)
            return self._finish(
                Decision("UNKNOWN", "SEND_OUTCOME_UNCERTAIN", {"result": result}, intent_id=intent_id)
            )

        record.state = "REJECTED"
        record.reason = f"RETCODE_{retcode}_{RETCODE_NAMES.get(retcode, 'UNKNOWN')}"
        self.store.save_intent(record)
        return self._finish(
            Decision(
                "REJECTED",
                record.reason,
                {"result": result, "request": request},
                intent_id=intent_id,
            )
        )

    def _compliance_engine(self, baseline: Mapping[str, Any], now: datetime) -> PropFirmComplianceEngine:
        created = baseline.get("created_utc")
        created_ts = datetime.fromisoformat(created) if created else now
        return PropFirmComplianceEngine(
            rule_profile=self.profile,
            state_path=self.compliance_state_path,
            safety_buffer_pct=self.settings.safety_buffer_pct,
            environment=ENVIRONMENT,
            account_created_ts=created_ts,
        )

    def _build_request(
        self,
        spec: SymbolSpec,
        tick: TickSnapshot,
        signal: Signal,
        lots: float,
        comment: str,
    ) -> dict[str, Any]:
        filling = choose_filling(spec)
        if signal.side == "BUY":
            entry = tick.ask
            sl = round(entry - signal.stop_distance, spec.digits)
            tp = round(entry + signal.target_distance, spec.digits) if signal.target_distance > 0 else 0.0
            order_type = ORDER_TYPE_BUY
        else:
            entry = tick.bid
            sl = round(entry + signal.stop_distance, spec.digits)
            tp = round(entry - signal.target_distance, spec.digits) if signal.target_distance > 0 else 0.0
            order_type = ORDER_TYPE_SELL

        if entry <= 0 or sl <= 0:
            raise RunnerError("non-positive entry or stop price")
        if abs(entry - sl) < spec.minimum_stop_distance() - 1e-12:
            raise RunnerError("STOP_INSIDE_MINIMUM_DISTANCE")
        freeze = spec.freeze_level_points * spec.point
        if signal.side == "BUY":
            if sl >= tick.bid - freeze:
                raise RunnerError("STOP_TOO_CLOSE_TO_MARKET")
            if tp > 0 and tp <= tick.ask + freeze:
                raise RunnerError("TARGET_TOO_CLOSE_TO_MARKET")
        else:
            if sl <= tick.ask + freeze:
                raise RunnerError("STOP_TOO_CLOSE_TO_MARKET")
            if tp > 0 and tp >= tick.bid - freeze:
                raise RunnerError("TARGET_TOO_CLOSE_TO_MARKET")

        return {
            "action": TRADE_ACTION_DEAL,
            "symbol": spec.symbol,
            "volume": lots,
            "type": order_type,
            "price": entry,
            "sl": sl,
            "tp": tp,
            "deviation": self.settings.max_slippage_points,
            "magic": self.settings.magic,
            "comment": comment,
            "type_time": ORDER_TIME_GTC,
            "type_filling": filling,
        }

    def _wait_and_verify(self, record: IntentRecord) -> dict[str, Any]:
        last: dict[str, Any] = {"ok": False, "checks": {}, "reason": "NO_POSITION_YET"}
        for attempt in range(4):
            try:
                positions = self.client.positions(record.symbol)
            except MT5ReadError as exc:
                last = {"ok": False, "checks": {}, "reason": f"POSITIONS_UNAVAILABLE: {exc}"}
                break
            matches = [
                p
                for p in positions
                if str(getattr(p, "comment", "") or "").startswith(record.comment)
                and int(getattr(p, "magic", 0) or 0) == self.settings.magic
            ]
            if len(matches) == 1:
                return self._verify_position(record, matches[0])
            if len(matches) > 1:
                return {
                    "ok": False,
                    "checks": {"single_position": False},
                    "reason": f"MULTIPLE_POSITIONS({len(matches)})",
                }
            if attempt < 3:
                self._sleep(0.25 * (attempt + 1))
        return last

    def _finish(self, decision: Decision) -> Decision:
        try:
            self.store.append_decision(decision.to_dict())
        except OSError:
            logger.exception("failed to append decision log", extra={"environment": ENVIRONMENT})
        logger.info(
            "%s %s intent=%s",
            decision.action,
            decision.reason_code,
            decision.intent_id,
            extra={
                "environment": ENVIRONMENT,
                "decision_id": decision.decision_id,
                "action": decision.action,
                "reason_code": decision.reason_code,
            },
        )
        return decision
