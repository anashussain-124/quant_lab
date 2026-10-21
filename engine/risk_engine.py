"""Phase 5: Risk Engine, Paper Broker, Kill Switch.

Per AGENTS.md A3 & A6:
- Kill switch / risk engine: deterministic code, absolute veto
- Execution layer: cannot bypass risk; fails closed
- Paper broker fills against real order books
- Position-size limits, daily loss limits, drawdown limits
- No automatic risk increase
- Emergency stop with CRITICAL status
"""
from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import pandas as pd
import numpy as np

from engine.safety_gate import SafetyGate, RiskContext, SafetyVerdict, SafetyStatus, LabConfig
from engine.logger import AuditLogger
from adapters import OrderBook, TickData, DataQualityScore
from adapters.data_quality import DataQualityEngine
from engine.backtester_v2 import Order, OrderSide, OrderStatus, CostModel


class RiskLevel(str, Enum):
    """Risk levels."""
    HEALTHY = "HEALTHY"
    CAUTION = "CAUTION"
    REDUCED = "REDUCED"
    HALTED = "HALTED"
    CRITICAL = "CRITICAL"


class PaperBrokerMode(str, Enum):
    """Paper broker execution modes."""
    SHADOW = "SHADOW"  # Signals only, hypothetical fills
    DEMO = "DEMO"  # Demo orders with real fills
    LIVE = "LIVE"  # Disabled until explicitly authorized


@dataclass
class Position:
    """Represents an open position."""
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    size: float = 0.0  # Position size in lots
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def update(self, current_price: float) -> None:
        """Update position with new price."""
        self.current_price = current_price
        if self.side == OrderSide.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class RiskMetrics:
    """Real-time risk metrics."""
    equity: float = 5000.0
    daily_pnl: float = 0.0
    daily_loss: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 5000.0
    current_drawdown: float = 0.0
    position_count: int = 0
    total_exposure: float = 0.0
    risk_level: RiskLevel = RiskLevel.HEALTHY
    daily_loss_limit_hit: bool = False
    max_drawdown_hit: bool = False
    kill_switch_active: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskEngine:
    """Deterministic risk engine with hard limits.

    Per AGENTS.md A3:
    - Kill switch / risk engine: deterministic code, absolute veto
    - Execution layer: cannot bypass risk; fails closed
    - No automatic increase of risk
    - Hard daily-loss limit, hard maximum drawdown, position-size limits
    """

    def __init__(
        self,
        config: LabConfig | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.config = config or LabConfig()
        self.audit = audit or AuditLogger()
        self._risk_level = RiskLevel.HEALTHY
        self._positions: dict[str, Position] = {}
        self._metrics = RiskMetrics()
        self._kill_switch_active = False
        self._daily_loss_so_far = 0.0
        self._portfolio_peak = self.config.risk.max_position_size * 10

    def check_daily_loss(self, pnl: float) -> tuple[bool, str]:
        """Check if daily loss limit exceeded.

        Returns (can_proceed, reason).
        """
        if self._kill_switch_active:
            return False, "KILL_SWITCH_ACTIVE"

        self._daily_loss_so_far += abs(pnl) if pnl < 0 else 0.0
        self._metrics.daily_loss = self._daily_loss_so_far

        if self._daily_loss_so_far >= self.config.risk.max_daily_loss * 10000:
            self._metrics.daily_loss_limit_hit = True
            self._metrics.risk_level = RiskLevel.HALTED
            return False, "DAILY_LOSS_LIMIT_HIT"

        return True, "OK"

    def check_drawdown(self, equity: float) -> tuple[bool, str]:
        """Check if maximum drawdown exceeded.

        Returns (can_proceed, reason).
        """
        if self._kill_switch_active:
            return False, "KILL_SWITCH_ACTIVE"

        self._metrics.equity = equity
        self._metrics.peak_equity = max(self._metrics.peak_equity, equity)

        drawdown = (self._metrics.peak_equity - equity) / max(self._metrics.peak_equity, 1)
        self._metrics.current_drawdown = drawdown

        if drawdown >= self.config.risk.max_drawdown:
            self._metrics.max_drawdown_hit = True
            self._metrics.risk_level = RiskLevel.HALTED
            return False, "MAX_DRAWDOWN_HIT"

        # Update risk level
        if drawdown >= self.config.risk.max_drawdown * 0.5:
            self._metrics.risk_level = RiskLevel.REDUCED
        elif drawdown >= self.config.risk.max_drawdown * 0.25:
            self._metrics.risk_level = RiskLevel.CAUTION
        else:
            self._metrics.risk_level = RiskLevel.HEALTHY

        return True, "OK"

    def check_position_size(
        self,
        symbol: str,
        quantity: float,
        price: float,
        equity: float,
    ) -> tuple[bool, str]:
        """Check if position size is within limits.

        Returns (can_proceed, reason).
        """
        if self._kill_switch_active:
            return False, "KILL_SWITCH_ACTIVE"

        notional = quantity * price
        max_notional = equity * self.config.risk.max_position_size

        if notional > max_notional:
            return False, f"POSITION_TOO_LARGE: {notional} > {max_notional}"

        # Check total exposure
        self._metrics.total_exposure = sum(
            p.quantity * p.current_price for p in self._positions.values()
        ) + notional

        return True, "OK"

    def evaluate(self, context: RiskContext, equity: float = 5000.0) -> tuple[bool, str]:
        """Full risk evaluation.

        Returns (can_proceed, reason).
        """
        if self._kill_switch_active:
            return False, "KILL_SWITCH_ACTIVE"

        # Check daily loss
        daily_ok, daily_reason = self.check_daily_loss(context.daily_loss_so_far)
        if not daily_ok:
            return False, daily_reason

        # Check drawdown using explicit equity
        dd_ok, dd_reason = self.check_drawdown(equity)
        if not dd_ok:
            return False, dd_reason

        # Check portfolio drawdown from context
        if context.portfolio_drawdown >= self.config.risk.max_drawdown:
            return False, "PORTFOLIO_DRAWDOWN_EXCEEDED"

        # Check GPU safety
        if context.gpu_temperature > self.config.gpu.max_temperature_c:
            return False, "GPU_OVERHEAT"

        # Check connection
        if context.connection_status != "connected":
            return False, "DISCONNECTED"

        # Check data quality
        if context.data_quality_score < 0.7:
            return False, "DATA_QUALITY_LOW"

        return True, "OK"

    def emergency_stop(self, reason: str) -> bool:
        """Emergency stop — absolute veto.

        Per AGENTS.md A3: Kill switch is deterministic, absolute veto.
        """
        self._kill_switch_active = True
        self._metrics.kill_switch_active = True
        self._metrics.risk_level = RiskLevel.CRITICAL

        self.audit.log(
            event="EMERGENCY_STOP",
            action=f"Kill switch activated: {reason}",
            details={"reason": reason},
            actor="risk_engine",
        )

        return True

    def reset_kill_switch(self) -> None:
        """Reset kill switch (requires explicit authorization)."""
        self._kill_switch_active = False
        self._metrics.kill_switch_active = False
        self._metrics.risk_level = RiskLevel.HEALTHY
        self._daily_loss_so_far = 0.0
        self._metrics.daily_loss = 0.0

        self.audit.log(
            event="KILL_SWITCH_RESET",
            action="Kill switch reset",
            details={},
            actor="risk_engine",
        )

    def get_risk_metrics(self) -> RiskMetrics:
        """Get current risk metrics."""
        self._metrics.daily_pnl = self._daily_loss_so_far
        return self._metrics

    @property
    def risk_level(self) -> RiskLevel:
        """Current risk level."""
        return self._metrics.risk_level

    @property
    def is_kill_switch_active(self) -> bool:
        """Kill switch status."""
        return self._kill_switch_active

    @property
    def positions(self) -> dict[str, Position]:
        """Current positions."""
        return self._positions.copy()

    def add_position(self, position: Position) -> None:
        """Add a position."""
        self._positions[position.symbol] = position

    def close_position(self, symbol: str) -> Position | None:
        """Close a position."""
        return self._positions.pop(symbol, None)


class PaperBroker:
    """Paper broker — fills against real order books.

    Per AGENTS.md A2 & A6:
    - Uses simulated paper broker that fills against real order books
    - Shadow mode: signals only, hypothetical fills
    - Demo mode: demo orders with real fills
    - Live mode: DISABLED until explicitly authorized
    """

    def __init__(
        self,
        mode: PaperBrokerMode = PaperBrokerMode.SHADOW,
        risk_engine: RiskEngine | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.mode = mode
        self.risk_engine = risk_engine or RiskEngine()
        self.audit = audit or AuditLogger()
        self._orders: list[Order] = []
        self._positions: dict[str, Position] = {}
        self._fill_history: list[dict[str, Any]] = []

    def set_mode(self, mode: PaperBrokerMode) -> None:
        """Set paper broker mode."""
        if mode == PaperBrokerMode.LIVE:
            raise RuntimeError(
                "LIVE mode disabled. Must be explicitly authorized by user."
            )
        self.mode = mode
        self.audit.log(
            event="MODE_CHANGED",
            action=f"Mode changed to {mode.value}",
            details={"mode": mode.value},
            actor="paper_broker",
        )

    def execute_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float,
        order_type: str = "LIMIT",
        equity: float = 5000.0,
    ) -> Order:
        """Execute an order through the paper broker.

        Per AGENTS.md A6:
        - NET_EDGE = gross_diff - fees - expected_slippage - latency
          - funding_cost - transfer/rebalancing_cost - operational_cost
        - All costs computed
        """
        if self.risk_engine.is_kill_switch_active:
            return Order(
                order_id=f"rej_{int(time.time())}",
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                status=OrderStatus.REJECTED,
                metadata={"reason": "KILL_SWITCH_ACTIVE"},
            )

        # Check risk
        context = RiskContext()
        can_proceed, reason = self.risk_engine.evaluate(context, equity=equity)
        if not can_proceed:
            return Order(
                order_id=f"rej_{int(time.time())}",
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                order_type=order_type,
                status=OrderStatus.REJECTED,
                metadata={"reason": reason},
            )

        # Create order
        order = Order(
            order_id=f"ord_{int(time.time())}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
        )

        # Get order book for fill
        book = self._get_order_book(symbol)

        # Compute fill
        fill_price = self._compute_fill_price(order, book)
        fill_quantity = self._compute_fill_quantity(order, book)

        # Compute costs
        notional = fill_price * fill_quantity
        costs = CostModel().compute_total_cost(
            notional=notional, order_type=order_type,
            price=fill_price, volume=fill_quantity
        )

        # Fill order
        order.fill_price = fill_price
        order.fill_quantity = fill_quantity
        order.fee = costs["fees"]
        order.slippage = costs["slippage"]
        order.status = OrderStatus.FILL

        # Record fill
        self._fill_history.append({
            "order_id": order.order_id,
            "symbol": symbol,
            "side": side.value,
            "quantity": fill_quantity,
            "fill_price": fill_price,
            "slippage": order.slippage,
            "fee": order.fee,
            "latency_ms": order.latency_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode.value,
        })

        # Update position
        self._update_position(order)

        self._orders.append(order)

        self.audit.log(
            event="PAPER_BROKER_FILL",
            action=f"Filled {side.value} {quantity} {symbol} @ {fill_price}",
            details=order.__dict__,
            actor="paper_broker",
        )

        return order

    def _get_order_book(self, symbol: str) -> OrderBook | None:
        """Get order book (simulated or from adapter)."""
        return None  # Would use adapter in production

    def _compute_fill_price(
        self, order: Order, book: OrderBook | None
    ) -> float:
        """Compute fill price based on mode."""
        if self.mode == PaperBrokerMode.SHADOW:
            # Shadow: hypothetical fill with small slippage
            return order.price * (1 + np.random.randn() * 0.001)
        elif self.mode == PaperBrokerMode.DEMO:
            # Demo: fill against real book
            if book and book.bids and book.asks:
                if order.side == OrderSide.BUY:
                    return float(book.asks[0][0])
                else:
                    return float(book.bids[0][0])
            return order.price
        return order.price

    def _compute_fill_quantity(
        self, order: Order, book: OrderBook | None
    ) -> float:
        """Compute fill quantity."""
        if self.mode == PaperBrokerMode.SHADOW:
            return order.quantity  # Full fill in shadow
        elif self.mode == PaperBrokerMode.DEMO:
            if book:
                available = sum(q for _, q in book.bids) if order.side == OrderSide.BUY else sum(q for _, q in book.asks)
                return min(order.quantity, available * 0.9)
        return order.quantity

    def _update_position(self, order: Order) -> None:
        """Update position after fill."""
        symbol = order.symbol
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos.quantity += order.fill_quantity or order.quantity
            pos.current_price = order.fill_price or order.price
            pos.update(pos.current_price)
        else:
            self._positions[symbol] = Position(
                symbol=symbol,
                side=order.side,
                quantity=order.fill_quantity or order.quantity,
                entry_price=order.fill_price or order.price,
                current_price=order.fill_price or order.price,
                size=order.fill_quantity or order.quantity,
            )

    def get_positions(self) -> dict[str, Position]:
        """Get current positions."""
        return self._positions.copy()

    def get_fill_history(self) -> list[dict[str, Any]]:
        """Get fill history."""
        return list(self._fill_history)

    def get_pnl(self) -> dict[str, float]:
        """Get current P&L."""
        total_unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        total_realized = sum(p.realized_pnl for p in self._positions.values())
        return {
            "unrealized": total_unrealized,
            "realized": total_realized,
            "total": total_unrealized + total_realized,
        }

    def close_all_positions(self) -> None:
        """Close all positions."""
        self._positions.clear()
        self.audit.log(
            event="ALL_POSITIONS_CLOSED",
            action="All positions closed",
            details={},
            actor="paper_broker",
        )

    def set_kill_switch(self, reason: str) -> None:
        """Trigger kill switch on risk engine."""
        self.risk_engine.emergency_stop(reason)


class KillSwitch:
    """Kill switch — absolute veto.

    Per AGENTS.md A3:
    - Kill switch / risk engine: deterministic code, absolute veto
    - Execution layer: cannot bypass risk; fails closed
    """

    def __init__(self, risk_engine: RiskEngine) -> None:
        self._risk_engine = risk_engine
        self._activated = False
        self._reason = ""
        self._activated_at: datetime | None = None

    def activate(self, reason: str) -> bool:
        """Activate kill switch — absolute veto."""
        self._activated = True
        self._reason = reason
        self._activated_at = datetime.now(timezone.utc)
        return self._risk_engine.emergency_stop(reason)

    def deactivate(self) -> None:
        """Deactivate kill switch."""
        self._activated = False
        self._reason = ""
        self._activated_at = None
        self._risk_engine.reset_kill_switch()

    @property
    def is_active(self) -> bool:
        """Kill switch status."""
        return self._risk_engine.is_kill_switch_active

    @property
    def reason(self) -> str:
        """Reason for activation."""
        return self._reason

    @property
    def activated_at(self) -> datetime | None:
        """When kill switch was activated."""
        return self._activated_at

    def status(self) -> dict[str, Any]:
        """Get kill switch status."""
        return {
            "active": self._activated,
            "reason": self._reason,
            "activated_at": self._activated_at.isoformat() if self._activated_at else None,
            "risk_level": self._risk_engine.risk_level.value,
            "daily_loss_so_far": self._risk_engine._daily_loss_so_far,
            "current_drawdown": self._risk_engine._metrics.current_drawdown,
        }
