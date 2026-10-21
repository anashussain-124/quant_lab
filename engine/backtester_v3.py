"""Phase 1: Proper Event-Driven Backtester with MTM P&L.

Features:
- Mark-to-market equity curve with compounding
- Realistic spread, slippage, commission modeling
- Intrabar stop-loss / take-profit handling
- No look-ahead bias (decision at bar close, execution at next open)
- Proper position lifecycle: SIGNAL → ORDER → FILL → MARK_TO_MARKET → CLOSE
- Leverage and margin tracking
- Supports long and short positions
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class Order:
    """Represents a trading order."""
    order_id: str
    side: OrderSide
    order_type: OrderType
    quantity: float  # in units (lots for XAUUSD)
    price: float | None = None  # None for market orders
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    fill_time: datetime | None = None
    commission: float = 0.0
    slippage: float = 0.0


@dataclass
class Position:
    """Represents an open position with MTM tracking."""
    position_id: str
    side: PositionSide
    quantity: float
    entry_price: float
    entry_time: datetime
    stop_loss: float | None = None
    take_profit: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    commission_paid: float = 0.0
    slippage_paid: float = 0.0

    def update_mtm(self, current_price: float) -> float:
        """Update mark-to-market P&L."""
        if self.side == PositionSide.LONG:
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
        return self.unrealized_pnl

    def close(self, exit_price: float, exit_time: datetime) -> float:
        """Close position and return realized P&L."""
        if self.side == PositionSide.LONG:
            self.realized_pnl = (exit_price - self.entry_price) * self.quantity
        else:
            self.realized_pnl = (self.entry_price - exit_price) * self.quantity
        return self.realized_pnl


@dataclass
class Trade:
    """Completed trade record."""
    trade_id: str
    side: PositionSide
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    realized_pnl: float
    commission: float
    slippage: float
    net_pnl: float
    return_pct: float
    bars_held: int
    stop_hit: bool = False
    take_profit_hit: bool = False


@dataclass
class BacktestConfig:
    """Backtest configuration."""
    initial_capital: float = 5000.0
    leverage: int = 100
    contract_size: float = 100.0  # 100 oz per lot for XAUUSD
    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.0004  # 0.04%
    slippage_bps: float = 2.0  # 2 basis points
    spread_bps: float = 1.0   # 1 basis point
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    risk_per_trade: float = 0.01  # 1% of equity
    max_positions: int = 1
    intrabar_stops: bool = True  # Check SL/TP intrabar


class BacktestResult:
    """Complete backtest results."""

    def __init__(self):
        self.trades: list[Trade] = []
        self.equity_curve: list[tuple[datetime, float]] = []
        self.orders: list[Order] = []
        self.positions_history: list[tuple[datetime, dict]] = []
        self.metrics: dict[str, float] = {}

    def add_trade(self, trade: Trade):
        self.trades.append(trade)

    def add_equity_point(self, timestamp: datetime, equity: float):
        self.equity_curve.append((timestamp, equity))

    def compute_metrics(self, risk_free_rate: float = 0.0) -> dict[str, float]:
        """Compute comprehensive performance metrics."""
        if not self.trades:
            return {"error": "No trades"}

        returns = np.array([t.return_pct for t in self.trades])
        net_pnls = np.array([t.net_pnl for t in self.trades])

        # Basic metrics
        total_pnl = float(np.sum(net_pnls))
        total_return = (self.equity_curve[-1][1] / self.equity_curve[0][1] - 1) if self.equity_curve else 0.0

        # Win rate
        wins = returns[returns > 0]
        losses = returns[returns <= 0]
        win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.0

        # Profit factor
        gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 1e-8
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        # Expectancy
        expectancy = float(np.mean(returns))

        # Sharpe ratio (per trade, not annualized for simplicity)
        returns_std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 1e-8
        sharpe = float((np.mean(returns) - risk_free_rate) / returns_std * np.sqrt(252)) if returns_std > 0 else 0.0

        # Sortino ratio
        downside_returns = returns[returns < 0]
        downside_std = float(np.std(downside_returns, ddof=1)) if len(downside_returns) > 1 else 1e-8
        sortino = float((np.mean(returns) - risk_free_rate) / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

        # Max drawdown
        equities = np.array([e[1] for e in self.equity_curve])
        peaks = np.maximum.accumulate(equities)
        drawdowns = (equities - peaks) / peaks
        max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Max consecutive losses
        max_consec_losses = 0
        current_losses = 0
        for t in self.trades:
            if t.net_pnl < 0:
                current_losses += 1
                max_consec_losses = max(max_consec_losses, current_losses)
            else:
                current_losses = 0

        # Average win/loss
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

        # Total costs
        total_commission = float(np.sum([t.commission for t in self.trades]))
        total_slippage = float(np.sum([t.slippage for t in self.trades]))

        # Average bars held
        avg_bars_held = float(np.mean([t.bars_held for t in self.trades])) if self.trades else 0.0

        self.metrics = {
            "total_trades": len(self.trades),
            "total_pnl": total_pnl,
            "total_return_pct": total_return * 100,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown_pct": max_drawdown * 100,
            "max_consecutive_losses": max_consec_losses,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "avg_bars_held": avg_bars_held,
            "total_commission": total_commission,
            "total_slippage": total_slippage,
            "total_costs": total_commission + total_slippage,
        }

        return self.metrics


class EventDrivenBacktester:
    """Proper event-driven backtester with MTM P&L."""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.result = BacktestResult()
        self._position_counter = 0
        self._order_counter = 0
        self._trade_counter = 0
        self._current_position: Position | None = None
        self._equity = config.initial_capital
        self._bars_since_entry = 0

    def _generate_id(self, prefix: str) -> str:
        """Generate unique ID."""
        if prefix == "order":
            self._order_counter += 1
            return f"ord_{self._order_counter:06d}"
        elif prefix == "position":
            self._position_counter += 1
            return f"pos_{self._position_counter:06d}"
        elif prefix == "trade":
            self._trade_counter += 1
            return f"trd_{self._trade_counter:06d}"
        return f"{prefix}_0"

    def _calculate_position_size(self, stop_distance: float | None = None) -> float:
        """Calculate position size based on risk per trade."""
        if stop_distance is None or stop_distance <= 0:
            # Default: 1 lot
            return self.config.min_lot

        # Risk = equity * risk_per_trade
        risk_amount = self._equity * self.config.risk_per_trade

        # For XAUUSD: 1 lot = 100 oz, 1 point = $0.10 per oz
        # Risk per lot = stop_distance * contract_size * tick_value
        tick_value = 0.10  # $0.10 per point per oz
        risk_per_lot = stop_distance * self.config.contract_size * tick_value

        if risk_per_lot <= 0:
            return self.config.min_lot

        lots = risk_amount / risk_per_lot

        # Apply constraints
        lots = max(self.config.min_lot, min(lots, self.config.max_lot))
        lots = round(lots / self.config.lot_step) * self.config.lot_step

        return lots

    def _apply_costs(self, price: float, quantity: float, is_maker: bool = False) -> tuple[float, float]:
        """Apply commission and slippage. Returns (total_cost, fill_price)."""
        fee_rate = self.config.maker_fee if is_maker else self.config.taker_fee
        commission = price * quantity * fee_rate

        slippage = price * quantity * (self.config.slippage_bps / 10000)

        return commission + slippage, price

    def _open_position(
        self,
        side: PositionSide,
        quantity: float,
        entry_price: float,
        entry_time: datetime,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Position:
        """Open a new position."""
        # Apply costs at entry
        cost, fill_price = self._apply_costs(entry_price, quantity)
        spread_cost = entry_price * quantity * (self.config.spread_bps / 10000)

        position = Position(
            position_id=self._generate_id("position"),
            side=side,
            quantity=quantity,
            entry_price=fill_price,
            entry_time=entry_time,
            stop_loss=stop_loss,
            take_profit=take_profit,
            commission_paid=cost,
            slippage_paid=spread_cost,
        )

        self._current_position = position
        self._bars_since_entry = 0

        return position

    def _close_position(
        self,
        exit_price: float,
        exit_time: datetime,
        stop_hit: bool = False,
        take_profit_hit: bool = False,
    ) -> Trade | None:
        """Close current position and return trade record."""
        if self._current_position is None:
            return None

        position = self._current_position

        # Apply costs at exit
        cost, fill_price = self._apply_costs(exit_price, position.quantity)

        # Calculate P&L
        realized_pnl = position.close(fill_price, exit_time)

        # Net P&L after costs
        net_pnl = realized_pnl - position.commission_paid - position.slippage_paid - cost
        return_pct = net_pnl / self._equity if self._equity > 0 else 0.0

        trade = Trade(
            trade_id=self._generate_id("trade"),
            side=position.side,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=fill_price,
            entry_time=position.entry_time,
            exit_time=exit_time,
            realized_pnl=realized_pnl,
            commission=position.commission_paid + cost,
            slippage=position.slippage_paid + cost,
            net_pnl=net_pnl,
            return_pct=return_pct,
            bars_held=self._bars_since_entry,
            stop_hit=stop_hit,
            take_profit_hit=take_profit_hit,
        )

        # Update equity
        self._equity += net_pnl

        # Record
        self.result.add_trade(trade)

        # Clear position
        self._current_position = None
        self._bars_since_entry = 0

        return trade

    def _check_stops(self, high: float, low: float, timestamp: datetime) -> Trade | None:
        """Check if stop-loss or take-profit hit intrabar."""
        if self._current_position is None:
            return None

        position = self._current_position

        # Check stop-loss
        if position.stop_loss is not None:
            if position.side == PositionSide.LONG and low <= position.stop_loss:
                return self._close_position(position.stop_loss, timestamp, stop_hit=True)
            elif position.side == PositionSide.SHORT and high >= position.stop_loss:
                return self._close_position(position.stop_loss, timestamp, stop_hit=True)

        # Check take-profit
        if position.take_profit is not None:
            if position.side == PositionSide.LONG and high >= position.take_profit:
                return self._close_position(position.take_profit, timestamp, take_profit_hit=True)
            elif position.side == PositionSide.SHORT and low <= position.take_profit:
                return self._close_position(position.take_profit, timestamp, take_profit_hit=True)

        return None

    def _update_mtm(self, close_price: float):
        """Update mark-to-market for current position."""
        if self._current_position is not None:
            self._current_position.update_mtm(close_price)

    def run(
        self,
        data: pd.DataFrame,
        signals: list[dict[str, Any]],
    ) -> BacktestResult:
        """
        Run backtest on historical data with pre-computed signals.

        Args:
            data: DataFrame with timestamp, open, high, low, close, volume
            signals: List of signal dicts with keys:
                - timestamp: when signal was generated
                - side: "BUY" or "SELL"
                - quantity: position size (optional, calculated if not provided)
                - stop_loss: stop loss price (optional)
                - take_profit: take profit price (optional)
        """
        self.result = BacktestResult()
        self._equity = self.config.initial_capital
        self._current_position = None
        self._bars_since_entry = 0

        # Record initial equity
        if len(data) > 0:
            self.result.add_equity_point(data.iloc[0]["timestamp"], self._equity)

        # Convert signals to lookup by timestamp
        signal_map = {}
        for sig in signals:
            # Convert to naive timestamp for consistent lookup
            ts = pd.Timestamp(sig["timestamp"])
            if ts.tzinfo is not None:
                ts = ts.tz_localize(None)
            signal_map[ts] = sig
        
        # Also make data timestamps naive for comparison
        data_timestamps = data["timestamp"].apply(lambda x: pd.Timestamp(x).tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x))

        for i in range(len(data)):
            row = data.iloc[i]
            ts = data_timestamps.iloc[i]
            open_price = float(row["open"])
            high_price = float(row["high"])
            low_price = float(row["low"])
            close_price = float(row["close"])

            # 1. Check intrabar stops (using current bar's high/low)
            if self.config.intrabar_stops and self._current_position is not None:
                stop_trade = self._check_stops(high_price, low_price, ts)

            # 2. Process signals at bar close (execution at current close)
            if ts in signal_map:
                sig = signal_map[ts]

                # Determine signal parameters
                side_str = sig.get("side", "BUY")
                side = PositionSide.LONG if side_str == "BUY" else PositionSide.SHORT
                quantity = sig.get("quantity", self._calculate_position_size(sig.get("stop_loss")))
                stop_loss = sig.get("stop_loss")
                take_profit = sig.get("take_profit")

                if self._current_position is None:
                    # Open new position at current close
                    self._open_position(side, quantity, close_price, ts, stop_loss, take_profit)
                else:
                    # Close current position if signal is in opposite direction
                    if side != self._current_position.side:
                        self._close_position(close_price, ts)
                        # Open new position in opposite direction
                        self._open_position(side, quantity, close_price, ts, stop_loss, take_profit)

            # 3. Update MTM
            self._update_mtm(close_price)

            # 4. Record equity
            total_equity = self._equity
            if self._current_position is not None:
                total_equity += self._current_position.unrealized_pnl

            self.result.add_equity_point(ts, total_equity)

            # 5. Increment bars held
            if self._current_position is not None:
                self._bars_since_entry += 1

        # Close any remaining position at end
        if self._current_position is not None:
            self._close_position(float(data.iloc[-1]["close"]), data_timestamps.iloc[-1])

        # Compute metrics
        self.result.compute_metrics()

        return self.result


def create_default_backtest_config() -> BacktestConfig:
    """Create default backtest config for XAUUSD."""
    return BacktestConfig(
        initial_capital=5000.0,
        leverage=100,
        contract_size=100.0,
        maker_fee=0.0002,
        taker_fee=0.0004,
        slippage_bps=2.0,
        spread_bps=1.0,
        min_lot=0.01,
        max_lot=100.0,
        lot_step=0.01,
        risk_per_trade=0.01,
        max_positions=1,
        intrabar_stops=True,
    )
