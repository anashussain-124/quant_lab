"""Phase 6 tests — MT5 XAUUSD Demo Trading.

Per AGENTS.md A2:
- MT5 verified: account 5056172579, MetaQuotes-Demo
- Must verify trade_mode == ACCOUNT_TRADE_MODE_DEMO before every order
- Demo/paper only, NO live trading
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import pandas as pd

from engine.mt5_bridge import (
    MT5Bridge, MT5Config, MT5OrderMode, MT5Position,
    XAUUSDStrategy,
)
from engine.risk_engine import RiskEngine, OrderSide
from engine.safety_gate import RiskContext

# MT5Adapter import handled at runtime to avoid import errors in tests


class TestMT5Bridge:
    """Test MT5 bridge."""

    def test_config_defaults(self):
        """MT5 config should have correct defaults."""
        config = MT5Config()
        assert config.account == 5056172579
        assert config.server == "MetaQuotes-Demo"
        assert config.symbol == "XAUUSD"
        assert config.contract_size == 100.0
        assert config.tick_size == 0.01
        assert config.tick_value == 0.10

    def test_bridge_creation(self):
        """MT5 bridge should be created."""
        bridge = MT5Bridge()
        assert bridge.config.account == 5056172579
        assert bridge.mode == MT5OrderMode.SHADOW
        assert bridge._connected is False

    def test_shadow_order(self):
        """Shadow mode should create orders without MT5 connection."""
        bridge = MT5Bridge()
        order = bridge.send_order("XAUUSD", OrderSide.BUY, 1.0, 2000.0)
        assert order["status"] == "FILLED"
        assert order["mode"] == "SHADOW"
        assert order["symbol"] == "XAUUSD"
        assert order["side"] == OrderSide.BUY.value

    def test_shadow_order_records(self):
        """Shadow orders should be recorded."""
        bridge = MT5Bridge()
        bridge.send_order("XAUUSD", OrderSide.BUY, 1.0, 2000.0)
        bridge.send_order("XAUUSD", OrderSide.SELL, 0.5, 2001.0)
        assert len(bridge._orders) == 2

    def test_bridge_disconnect(self):
        """Bridge should disconnect cleanly."""
        bridge = MT5Bridge()
        bridge.disconnect()  # Should not error even if not connected
        assert bridge._connected is False

    def test_set_mode_shadow(self):
        """Should set shadow mode."""
        bridge = MT5Bridge()
        bridge.set_mode(MT5OrderMode.SHADOW)
        assert bridge.mode == MT5OrderMode.SHADOW

    def test_bridge_pnl(self):
        """P&L should be calculable."""
        bridge = MT5Bridge()
        bridge.send_order("XAUUSD", OrderSide.BUY, 1.0, 2000.0)
        pnl = bridge.get_pnl()
        assert "profit" in pnl
        assert "net" in pnl


class TestXAUUSDStrategy:
    """Test XAUUSD-specific strategy."""

    def test_tick_value(self):
        """Tick value should be correct."""
        strategy = XAUUSDStrategy()
        value = strategy.compute_tick_value(1.0)
        assert value == 0.10  # $0.10 per tick per lot

    def test_tick_value_multiple_lots(self):
        """Tick value should scale with volume."""
        strategy = XAUUSDStrategy()
        value = strategy.compute_tick_value(2.0)
        assert value == 0.20  # 2 * $0.10

    def test_spread_cost(self):
        """Spread cost should be correct."""
        strategy = XAUUSDStrategy()
        cost = strategy.compute_spread_cost(1.0)
        # spread * volume * 10 (spread in USD per lot)
        assert cost == 0.20  # 0.02 * 1 * 10

    def test_swap_long(self):
        """Swap for long positions."""
        strategy = XAUUSDStrategy()
        swap = strategy.compute_swap(1.0, 1.0, OrderSide.BUY)
        assert swap == -12.6

    def test_swap_short(self):
        """Swap for short positions."""
        strategy = XAUUSDStrategy()
        swap = strategy.compute_swap(1.0, 1.0, OrderSide.SELL)
        assert swap == -4.6

    def test_position_size_calculation(self):
        """Position size should be calculable."""
        strategy = XAUUSDStrategy()
        size = strategy.compute_position_size(5000.0, risk_fraction=0.01, stop_distance=10.0)
        assert size > 0
        assert size <= 10.0

    def test_generate_signal_requires_data(self):
        """Signal requires sufficient data."""
        strategy = XAUUSDStrategy()
        df = pd.DataFrame({"close": [1, 2, 3]})
        signal = strategy.generate_signal(df, lookback=20)
        assert signal is None

    def test_generate_signal_buy(self):
        """Should generate BUY signal when fast SMA > slow SMA."""
        strategy = XAUUSDStrategy()
        # Uptrend: increasing prices
        prices = list(range(100, 200, 5))
        df = pd.DataFrame({"close": prices})
        signal = strategy.generate_signal(df, lookback=20)
        assert signal is not None
        assert signal["side"] == OrderSide.BUY

    def test_generate_signal_sell(self):
        """Should generate SELL signal when fast SMA < slow SMA."""
        strategy = XAUUSDStrategy()
        # Downtrend: decreasing prices
        prices = list(range(200, 100, -5))
        df = pd.DataFrame({"close": prices})
        signal = strategy.generate_signal(df, lookback=20)
        assert signal is not None
        assert signal["side"] == OrderSide.SELL


class TestIntegration:
    """Test integration with other components."""

    def test_risk_engine_with_mt5(self):
        """Risk engine should work with MT5 bridge."""
        engine = RiskEngine()
        bridge = MT5Bridge(risk_engine=engine)
        order = bridge.send_order("XAUUSD", OrderSide.BUY, 1.0, 2000.0)
        assert order["status"] == "FILLED"

    def test_risk_evaluation(self):
        """Risk evaluation should pass for normal context."""
        engine = RiskEngine()
        context = RiskContext()
        result, reason = engine.evaluate(context, equity=5000.0)
        assert result is True

    def test_shadow_mode_no_live_orders(self):
        """Shadow mode should never create live MT5 orders."""
        bridge = MT5Bridge()
        assert bridge._connected is False
        order = bridge.send_order("XAUUSD", OrderSide.BUY, 1.0, 2000.0)
        assert order["status"] == "FILLED"
        assert order["mode"] == "SHADOW"

    def test_strategy_cost_calculations(self):
        """Strategy cost calculations should be accurate."""
        strategy = XAUUSDStrategy()

        # For 1 lot with 10-tick stop and 1% risk on $5000
        size = strategy.compute_position_size(5000.0, 0.01, 10.0)
        # Expected: $50 risk / ($10/oz * 100oz) = 0.05 lots
        assert 0.04 <= size <= 0.06

        tick_val = strategy.compute_tick_value(size)
        spread_cost = strategy.compute_spread_cost(size)

        assert tick_val > 0
        assert spread_cost > 0
        assert tick_val + spread_cost > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
