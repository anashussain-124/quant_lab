"""Legacy MT5 read-only bridge — NOT the sanctioned execution path.

Demo order execution lives in ``execution/mt5_demo_runner.py``. This module
attaches to a running terminal for research reads and refuses DEMO sends.

Per AGENTS.md A2 & A6:
- Demo/paper only — NO live trading
- Must verify trade_mode == ACCOUNT_TRADE_MODE_DEMO before any order
- Shadow mode: simulated signals only
- DEMO mode: refused here; use the safe runner
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from engine.logger import AuditLogger
from engine.risk_engine import OrderSide, RiskEngine

# MT5Adapter imported lazily to avoid import errors in tests


class MT5OrderMode(StrEnum):
    """MT5 order execution modes."""
    SHADOW = "SHADOW"  # Simulated signals only, no MT5 orders
    DEMO = "DEMO"  # Refused by this bridge; use execution/mt5_demo_runner.py
    TEST = "TEST"  # Verification mode


@dataclass
class MT5Config:
    """MT5 connection configuration."""
    account: int = 5056172579
    server: str = "MetaQuotes-Demo"
    password: str = ""  # Loaded from env
    path: str = ""  # MT5 terminal path
    leverage: int = 100
    balance: float = 5000.0
    symbol: str = "XAUUSD"
    contract_size: float = 100.0  # oz per lot
    tick_size: float = 0.01
    tick_value: float = 0.10
    spread: float = 0.02  # typical spread in USD
    swap_long: float = -12.6
    swap_short: float = -4.6


@dataclass
class MT5Position:
    """MT5 position data."""
    ticket: int = 0
    symbol: str = "XAUUSD"
    type: OrderSide = OrderSide.BUY
    volume: float = 1.0
    price_open: float = 0.0
    price_current: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    time_open: datetime = field(default_factory=lambda: datetime.now(UTC))
    time_update: datetime = field(default_factory=lambda: datetime.now(UTC))


class MT5Bridge:
    """Legacy MT5 read-only bridge.

    Per AGENTS.md A2:
    - Must verify trade_mode == ACCOUNT_TRADE_MODE_DEMO before any order
    - Shadow mode: simulated signals only
    - DEMO mode: order sending is refused here
    - NO live trading
    """

    def __init__(
        self,
        config: MT5Config | None = None,
        risk_engine: RiskEngine | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.config = config or MT5Config()
        self.risk_engine = risk_engine or RiskEngine()
        self.audit = audit or AuditLogger()
        self.mode = MT5OrderMode.SHADOW
        self._mt5: Any = None
        self._positions: dict[int, MT5Position] = {}
        self._orders: list[dict[str, Any]] = []
        self._connected = False
        self._account_info: dict[str, Any] = {}

    def connect(self) -> bool:
        """Attach to the running MT5 terminal and verify DEMO mode.

        This legacy bridge is read-only for research. It never transmits an
        order; demo execution lives in ``execution/mt5_demo_runner.py``.
        """
        try:
            import MetaTrader5 as mt5

            if not mt5.initialize(path=self.config.path or None):
                raise RuntimeError(f"initialize failed: {mt5.last_error()}")
            info = mt5.account_info()
            if info is None:
                raise RuntimeError("account_info() returned None")

            trade_mode = int(info.trade_mode)
            if trade_mode != 0:  # ACCOUNT_TRADE_MODE_DEMO
                mt5.shutdown()
                self._connected = False
                self.audit.log(
                    event="MT5_REJECTED",
                    action=f"Non-DEMO account: trade_mode={trade_mode}",
                    details={"trade_mode": trade_mode},
                    actor="mt5_bridge",
                )
                return False
            if int(info.login) != self.config.account or info.server != self.config.server:
                mt5.shutdown()
                self._connected = False
                self.audit.log(
                    event="MT5_REJECTED",
                    action="Connected account does not match configuration",
                    details={
                        "expected_account": self.config.account,
                        "actual_account": int(info.login),
                        "expected_server": self.config.server,
                        "actual_server": info.server,
                    },
                    actor="mt5_bridge",
                )
                return False

            self._mt5 = mt5
            self._connected = True
            self.audit.log(
                event="MT5_CONNECTED",
                action="Connected to MT5 demo (read-only legacy bridge)",
                details={"account": int(info.login), "server": info.server},
                actor="mt5_bridge",
            )
        except Exception as e:
            self._connected = False
            self.audit.log(
                event="MT5_CONNECTION_FAILED",
                action=f"Connection failed: {e}",
                details={"error": str(e)},
                actor="mt5_bridge",
            )
        return self._connected

    def disconnect(self) -> None:
        """Disconnect from MT5."""
        mt5 = getattr(self, "_mt5", None)
        if mt5 is not None:
            mt5.shutdown()
            self._mt5 = None
        self._connected = False
        self.audit.log(
            event="MT5_DISCONNECTED",
            action="Disconnected from MT5",
            details={},
            actor="mt5_bridge",
        )

    def verify_demo_mode(self) -> tuple[bool, str]:
        """Re-query the terminal and verify DEMO mode. Fails closed."""
        if not self._connected:
            return False, "NOT_CONNECTED"
        try:
            import MetaTrader5 as mt5

            info = mt5.account_info()
            if info is None:
                return False, "ACCOUNT_INFO_UNAVAILABLE"
            if int(info.trade_mode) != 0:
                return False, f"NON_DEMO_TRADE_MODE:{int(info.trade_mode)}"
            if int(info.login) != self.config.account:
                return False, "ACCOUNT_MISMATCH"
            return True, "DEMO_MODE_CONFIRMED"
        except Exception as exc:
            return False, f"DEMO_VERIFICATION_ERROR:{exc}"

    def send_order(
        self,
        symbol: str,
        side: OrderSide,
        volume: float,
        price: float,
        order_type: str = "MARKET",
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict[str, Any]:
        """Send order to MT5.

        Per AGENTS.md A2:
        - Verify DEMO mode before every order
        - Shadow mode: no actual orders
        - Demo mode: real MT5 demo orders
        - NO live trading
        """
        # CRITICAL: Verify DEMO mode (bypassed for shadow mode since no MT5 needed)
        if self.mode != MT5OrderMode.SHADOW:
            demo_ok, demo_reason = self.verify_demo_mode()
            if not demo_ok:
                return {
                    "ticket": 0,
                    "status": "REJECTED",
                    "reason": demo_reason,
                    "order_id": f"rej_{int(time.time())}",
                }

        if self.mode == MT5OrderMode.SHADOW:
            # Shadow mode: no actual orders, hypothetical fill
            return self._shadow_order(symbol, side, volume, price, order_type, sl, tp)

        if self.mode == MT5OrderMode.DEMO:
            # This legacy bridge has no verified order path. Order sending is
            # intentionally refused; use execution/mt5_demo_runner.py.
            self.audit.log(
                event="MT5_DEMO_ORDER_REFUSED",
                action="Legacy bridge refused DEMO order; use execution/mt5_demo_runner.py",
                details={"symbol": symbol, "side": side.value, "volume": volume},
                actor="mt5_bridge",
            )
            return {
                "ticket": 0,
                "status": "REJECTED",
                "reason": "LEGACY_BRIDGE_NO_EXECUTION_PATH",
                "order_id": f"rej_{int(time.time())}",
            }

        return {
            "ticket": 0,
            "status": "REJECTED",
            "reason": "INVALID_MODE",
            "order_id": f"rej_{int(time.time())}",
        }

    def _shadow_order(
        self,
        symbol: str,
        side: OrderSide,
        volume: float,
        price: float,
        order_type: str,
        sl: float,
        tp: float,
    ) -> dict[str, Any]:
        """Shadow order — no actual MT5 order, hypothetical fill."""
        ticket = int(time.time())
        fill_price = price * (1 + np.random.randn() * 0.001)

        order = {
            "ticket": ticket,
            "symbol": symbol,
            "side": side.value,
            "volume": volume,
            "price_open": fill_price,
            "price_current": fill_price,
            "order_type": order_type,
            "status": "FILLED",
            "sl": sl,
            "tp": tp,
            "profit": 0.0,  # Will be updated
            "swap": 0.0,
            "commission": 0.0,
            "time_open": datetime.now(UTC),
            "mode": "SHADOW",
            "environment": "SIMULATED",
            "simulated_fill": True,
        }

        self._orders.append(order)
        self.audit.log(
            event="MT5_SHADOW_ORDER",
            action=f"Shadow {side.value} {volume} {symbol} @ {fill_price}",
            details=order,
            actor="mt5_bridge",
        )

        return order

    def get_positions(self) -> list[MT5Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def get_order_history(self) -> list[dict[str, Any]]:
        """Get filled order history."""
        return list(self._orders)

    def get_pnl(self) -> dict[str, float]:
        """Get P&L summary."""
        total_profit = sum(p.profit for p in self._positions.values())
        total_swap = sum(p.swap for p in self._positions.values())
        total_commission = sum(p.commission for p in self._positions.values())
        return {
            "profit": total_profit,
            "swap": total_swap,
            "commission": total_commission,
            "net": total_profit + total_swap - total_commission,
        }

    def set_mode(self, mode: MT5OrderMode) -> None:
        """Set MT5 order mode."""
        if mode == MT5OrderMode.DEMO and not self._connected:
            raise RuntimeError("Must connect to MT5 before switching to DEMO mode")
        self.mode = mode


class XAUUSDStrategy:
    """XAUUSD-specific strategy for MT5 demo.

    Per AGENTS.md A6:
    - XAUUSD: contract size 100oz, tick 0.01, tick value $0.10
    - Typical spread: ~2 ticks
    - Swap: long -12.6, short -4.6
    """

    def __init__(self, config: MT5Config | None = None) -> None:
        self.config = config or MT5Config()

    def compute_tick_value(self, volume: float) -> float:
        """Compute tick value for XAUUSD."""
        # tick_value = volume * contract_size * tick_size / tick_size
        # Actually: $0.10 per tick per lot, so:
        return self.config.tick_value * volume

    def compute_spread_cost(self, volume: float) -> float:
        """Compute spread cost."""
        return self.config.spread * volume * 10  # spread in USD per lot

    def compute_swap(self, volume: float, days: float, side: OrderSide) -> float:
        """Compute swap cost."""
        rate = self.config.swap_long if side == OrderSide.BUY else self.config.swap_short
        return rate * volume * days

    def compute_position_size(
        self,
        equity: float,
        risk_fraction: float = 0.01,
        stop_distance: float = 10.0,  # $ per oz
    ) -> float:
        """Compute position size based on risk."""
        # Risk $ amount
        risk_amount = equity * risk_fraction
        # Volume = risk_amount / (stop_distance * contract_size)
        volume = risk_amount / (stop_distance * self.config.contract_size)
        return max(0.01, min(volume, 10.0))  # Min 0.01 lot, max 10 lots

    def generate_signal(
        self,
        price_data: pd.DataFrame,
        lookback: int = 20,
    ) -> dict[str, Any] | None:
        """Generate trading signal from price data."""
        if len(price_data) < lookback:
            return None

        close = price_data["close"].values
        sma_fast = np.mean(close[-10:])
        sma_slow = np.mean(close[-lookback:])
        current_price = close[-1]

        if sma_fast > sma_slow * 1.001:  # Slight threshold
            return {
                "symbol": "XAUUSD",
                "side": OrderSide.BUY,
                "volume": 1.0,
                "price": current_price,
                "signal_type": "SMA_CROSSOVER",
                "confidence": 0.7,
            }
        elif sma_fast < sma_slow * 0.999:
            return {
                "symbol": "XAUUSD",
                "side": OrderSide.SELL,
                "volume": 1.0,
                "price": current_price,
                "signal_type": "SMA_CROSSOVER",
                "confidence": 0.7,
            }

        return None
