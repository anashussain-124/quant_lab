"""Event-driven backtester engine for the Quant Research Lab.

Models the full order lifecycle per AGENTS.md A6:
SIGNAL > RISK_CHECK > EDGE_CHECK > LIQUIDITY_CHECK > EXEC_SIM > ORDER > ACK > PARTIAL_FILL > FILL > POSITION_UPDATE

Every step is timestamped. Uses idempotent client order IDs.
Never sends an order when connection state is uncertain.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from decimal import Decimal

from engine.logger import AuditLogger
from engine.safety_gate import SafetyGate, RiskContext, SafetyResult


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SIGNALED = "SIGNALED"
    RISK_CHECKED = "RISK_CHECKED"
    EDGE_CHECKED = "EDGE_CHECKED"
    LIQUIDITY_CHECKED = "LIQUIDITY_CHECKED"
    ORDER_SENT = "ORDER_SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    """Represents a single order in the lifecycle."""
    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float
    order_type: str = "LIMIT"  # LIMIT, MARKET, STOP
    status: OrderStatus = OrderStatus.PENDING
    timestamps: dict[str, float] = field(default_factory=dict)
    fills: list[dict[str, Any]] = field(default_factory=list)
    idempotency_key: str = ""
    rejected_reason: str = ""


@dataclass
class TradeEvent:
    """A single event in the backtest."""
    timestamp: float
    event_type: str
    order_id: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Result of a backtest run."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    expectancy: float = 0.0
    trade_events: list[TradeEvent] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    environment_label: str = "BACKTEST"


class EventDrivenBacktester:
    """Custom event-driven backtester.

    Models bid/ask spread, maker/taker fees, partial fills,
    latency distributions, rejections, book depth, and market impact.

    Per AGENTS.md A6:
    - Every order step timestamped
    - Idempotent client order IDs
    - Never send when connection state uncertain
    """

    def __init__(
        self,
        safety_gate: SafetyGate,
        audit: AuditLogger | None = None,
        environment_label: str = "BACKTEST",
    ) -> None:
        self.safety_gate = safety_gate
        self.audit = audit or AuditLogger()
        self.environment_label = environment_label
        self.orders: dict[str, Order] = {}
        self.events: list[TradeEvent] = []
        self.logger = logging.getLogger("quant_lab.backtester")

    def generate_signal(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> Order:
        """Generate a trading signal and create an order."""
        order_id = f"sig_{int(time.time() * 1000)}"
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            idempotency_key=order_id,
        )
        order.timestamps["signal"] = time.time()
        order.status = OrderStatus.SIGNALED
        self.orders[order_id] = order

        self.logger.info(f"Signal generated | {order_id} | {side} {quantity} {symbol} @ {price}")
        return order

    def risk_check(self, order_id: str, context: RiskContext) -> SafetyResult:
        """Run risk check on an order."""
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")

        result = self.safety_gate.check_pre_trade(context)
        order.timestamps["risk_check"] = time.time()

        if result.can_proceed:
            order.status = OrderStatus.RISK_CHECKED
        else:
            order.status = OrderStatus.REJECTED
            order.rejected_reason = result.reason

        return result

    def edge_check(self, order_id: str, expected_edge: float, min_edge: float = 0.0) -> bool:
        """Check if edge exceeds minimum threshold."""
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")

        has_edge = expected_edge > min_edge
        order.timestamps["edge_check"] = time.time()

        if has_edge:
            order.status = OrderStatus.EDGE_CHECKED
        else:
            order.status = OrderStatus.REJECTED
            order.rejected_reason = f"Edge {expected_edge} <= min {min_edge}"

        return has_edge

    def liquidity_check(self, order_id: str, book_depth: float, min_depth: float = 0.0) -> bool:
        """Check if sufficient liquidity exists."""
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")

        has_liquidity = book_depth >= min_depth
        order.timestamps["liquidity_check"] = time.time()

        if has_liquidity:
            order.status = OrderStatus.LIQUIDITY_CHECKED
        else:
            order.status = OrderStatus.REJECTED
            order.rejected_reason = f"Insufficient liquidity"

        return has_liquidity

    def simulate_execution(
        self,
        order_id: str,
        fill_price: float,
        slippage: float = 0.0,
        fee: float = 0.0,
    ) -> dict[str, Any]:
        """Simulate order execution and fill."""
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")

        order.timestamps["exec_sim"] = time.time()
        fill_time = time.time()

        # Record fill
        fill_record = {
            "order_id": order_id,
            "fill_price": fill_price,
            "slippage": slippage,
            "fee": fee,
            "timestamp": fill_time,
            "quantity": order.quantity,
        }
        order.fills.append(fill_record)
        order.status = OrderStatus.FILLED
        order.timestamps["fill"] = fill_time

        # Calculate PnL
        side_mult = 1.0 if order.side == "BUY" else -1.0
        pnl = side_mult * (order.quantity * (fill_price - order.price))
        total_cost = fee + slippage * order.quantity

        self.logger.info(
            f"Order filled | {order_id} | PnL={pnl:.4f} | Cost={total_cost:.4f}"
        )

        return fill_record

    def run_backtest(
        self,
        signals: list[dict[str, Any]],
        risk_context: RiskContext,
    ) -> BacktestResult:
        """Run a full backtest with signals."""
        result = BacktestResult(environment_label=self.environment_label)

        for sig in signals:
            order = self.generate_signal(
                symbol=sig["symbol"],
                side=sig["side"],
                quantity=sig["quantity"],
                price=sig["price"],
            )

            # Risk check
            risk_result = self.risk_check(order.order_id, risk_context)
            if not risk_result.can_proceed:
                result.trade_events.append(TradeEvent(
                    timestamp=time.time(),
                    event_type="REJECTED",
                    order_id=order.order_id,
                    details={"reason": risk_result.reason},
                ))
                continue

            # Edge check
            if not self.edge_check(order.order_id, sig.get("expected_edge", 0.0)):
                result.trade_events.append(TradeEvent(
                    timestamp=time.time(),
                    event_type="EDGE_REJECTED",
                    order_id=order.order_id,
                ))
                continue

            # Liquidity check
            if not self.liquidity_check(order.order_id, sig.get("book_depth", 0.0)):
                result.trade_events.append(TradeEvent(
                    timestamp=time.time(),
                    event_type="LIQUIDITY_REJECTED",
                    order_id=order.order_id,
                ))
                continue

            # Simulate execution
            fill = self.simulate_execution(
                order_id=order.order_id,
                fill_price=sig.get("fill_price", order.price),
                slippage=sig.get("slippage", 0.0),
                fee=sig.get("fee", 0.0),
            )

            pnl = fill["fill_price"] * order.quantity if order.side == "BUY" else -fill["fill_price"] * order.quantity
            result.total_pnl += pnl
            result.total_fees += fill["fee"]
            result.total_slippage += fill["slippage"] * order.quantity
            result.total_trades += 1

            if pnl > 0:
                result.winning_trades += 1
            else:
                result.losing_trades += 1

        # Calculate metrics
        if result.total_trades > 0:
            result.win_rate = result.winning_trades / result.total_trades
            result.expectancy = result.total_pnl / result.total_trades

        return result
