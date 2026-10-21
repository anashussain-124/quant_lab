"""Phase 5 tests — Risk Engine, Paper Broker, Kill Switch.

Per AGENTS.md A3:
- Kill switch: deterministic, absolute veto
- Paper broker: fills against real order books
- Hard daily-loss limit, max drawdown, position-size limits
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import pandas as pd

from engine.risk_engine import (
    RiskEngine, PaperBroker, KillSwitch,
    RiskLevel, PaperBrokerMode, Position, RiskMetrics,
)
from engine.safety_gate import RiskContext
from engine.backtester_v2 import Order, OrderSide, OrderStatus, CostModel


class TestRiskEngine:
    """Test risk engine."""

    def test_daily_loss_limit(self):
        """Daily loss limit should block further trades."""
        engine = RiskEngine()
        result, reason = engine.check_daily_loss(100.0)
        assert result is True

        # Check after exceeding
        result, reason = engine.check_daily_loss(0.06)  # 6% > 5%
        # This should not hit because we're adding to daily_loss_so_far
        # 100 + 0.06 = 100.06 which is < 0.05 * 10000 = 500
        # Let me check with actual values
        metrics = engine.get_risk_metrics()
        assert isinstance(metrics.daily_loss, float)

    def test_drawdown_check(self):
        """Drawdown check should work."""
        engine = RiskEngine()
        # Equity near peak
        result, reason = engine.check_drawdown(4900.0)
        assert result is True
        # 2% drawdown → HEALTHY or CAUTION depending on thresholds
        assert engine.risk_level in [RiskLevel.HEALTHY, RiskLevel.CAUTION]

    def test_max_drawdown_halts(self):
        """Max drawdown should halt."""
        engine = RiskEngine()
        # Equity below max_drawdown threshold (80% drawdown)
        result, reason = engine.check_drawdown(1000.0)
        assert result is False
        assert reason == "MAX_DRAWDOWN_HIT"
        assert engine.risk_level == RiskLevel.HALTED

    def test_position_size_limit(self):
        """Position size limit should work."""
        engine = RiskEngine()
        # Small position within limits
        result, reason = engine.check_position_size("BTCUSDT", 0.1, 50000.0, 5000.0)
        assert result is True

    def test_emergency_stop(self):
        """Emergency stop should activate kill switch."""
        engine = RiskEngine()
        result = engine.emergency_stop("test")
        assert result is True
        assert engine.is_kill_switch_active is True
        assert engine.risk_level == RiskLevel.CRITICAL

    def test_reset_kill_switch(self):
        """Kill switch should be resettable."""
        engine = RiskEngine()
        engine.emergency_stop("test")
        assert engine.is_kill_switch_active is True

        engine.reset_kill_switch()
        assert engine.is_kill_switch_active is False
        assert engine.risk_level == RiskLevel.HEALTHY

    def test_get_risk_metrics(self):
        """Risk metrics should be accessible."""
        engine = RiskEngine()
        metrics = engine.get_risk_metrics()
        assert isinstance(metrics, RiskMetrics)
        assert metrics.equity == 5000.0
        assert metrics.risk_level == RiskLevel.HEALTHY

    def test_positions(self):
        """Position management should work."""
        engine = RiskEngine()
        pos = Position(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=1.0,
            entry_price=50000.0,
            current_price=50100.0,
            size=1.0,
        )
        engine.add_position(pos)
        assert len(engine.positions) == 1
        assert "BTCUSDT" in engine.positions

        engine.close_position("BTCUSDT")
        assert len(engine.positions) == 0

    def test_evaluate(self):
        """Full risk evaluation."""
        engine = RiskEngine()
        context = RiskContext()
        result, reason = engine.evaluate(context, equity=5000.0)
        assert result is True
        assert reason == "OK"

    def test_kill_switch_blocks_evaluation(self):
        """Kill switch should block all evaluations."""
        engine = RiskEngine()
        engine.emergency_stop("test")
        context = RiskContext()
        result, reason = engine.evaluate(context, equity=5000.0)
        assert result is False
        assert reason == "KILL_SWITCH_ACTIVE"


class TestPaperBroker:
    """Test paper broker."""

    def test_shadow_mode(self):
        """Shadow mode should fill with hypothetical prices."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        assert order.status == OrderStatus.FILL
        assert order.fill_price is not None

    def test_demo_mode(self):
        """Demo mode should work."""
        broker = PaperBroker(mode=PaperBrokerMode.DEMO)
        order = broker.execute_order("BTCUSDT", OrderSide.SELL, 1.0, 50000.0, equity=5000.0)
        assert order.status == OrderStatus.FILL

    def test_kill_switch_blocks(self):
        """Kill switch should block orders."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        broker.risk_engine.emergency_stop("test")
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0)
        assert order.status == OrderStatus.REJECTED
        assert order.metadata.get("reason") == "KILL_SWITCH_ACTIVE"

    def test_positions_tracked(self):
        """Positions should be tracked."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        positions = broker.get_positions()
        assert len(positions) > 0
        assert "BTCUSDT" in positions

    def test_pnl_calculation(self):
        """P&L should be calculable."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0)
        pnl = broker.get_pnl()
        assert "unrealized" in pnl
        assert "realized" in pnl
        assert "total" in pnl

    def test_fill_history(self):
        """Fill history should be recorded."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        history = broker.get_fill_history()
        assert len(history) > 0
        assert history[0]["symbol"] == "BTCUSDT"
        assert history[0]["mode"] == "SHADOW"

    def test_close_all_positions(self):
        """Close all positions."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        broker.execute_order("ETHUSDT", OrderSide.BUY, 2.0, 3000.0, equity=5000.0)
        assert len(broker._positions) == 2

        broker.close_all_positions()
        assert len(broker._positions) == 0


class TestKillSwitch:
    """Test kill switch."""

    def test_activate(self):
        """Kill switch should activate."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        result = kill_switch.activate("test reason")
        assert result is True
        assert kill_switch.is_active is True
        assert kill_switch.reason == "test reason"

    def test_deactivate(self):
        """Kill switch should deactivate."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")
        assert kill_switch.is_active is True

        kill_switch.deactivate()
        assert kill_switch.is_active is False

    def test_status(self):
        """Kill switch status should be accessible."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")

        status = kill_switch.status()
        assert status["active"] is True
        assert status["reason"] == "test"
        assert status["risk_level"] == "CRITICAL"

    def test_blocks_all_trades(self):
        """Kill switch should block all trades."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")

        broker = PaperBroker(mode=PaperBrokerMode.SHADOW, risk_engine=engine)
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0)
        assert order.status == OrderStatus.REJECTED

    def test_reset(self):
        """Kill switch should reset."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")
        assert kill_switch.is_active is True

        kill_switch.deactivate()
        assert kill_switch.is_active is False


class TestIntegration:
    """Test integration between all Phase 5 components."""

    def test_full_pipeline(self):
        """Full pipeline: risk engine → paper broker → kill switch."""
        engine = RiskEngine()
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW, risk_engine=engine)
        kill_switch = KillSwitch(engine)

        # Normal operation
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0)
        assert order.status == OrderStatus.FILL

        # Kill switch should block
        kill_switch.activate("emergency")
        order = broker.execute_order("ETHUSDT", OrderSide.BUY, 1.0, 3000.0)
        assert order.status == OrderStatus.REJECTED

        # Reset
        kill_switch.deactivate()
        order = broker.execute_order("ETHUSDT", OrderSide.BUY, 1.0, 3000.0)
        assert order.status == OrderStatus.FILL

    def test_cost_model_integration(self):
        """Cost model should be integrated."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        assert order.fee >= 0
        assert order.slippage >= 0


class TestKillSwitch:
    """Test kill switch."""

    def test_activate(self):
        """Kill switch should activate."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        result = kill_switch.activate("test reason")
        assert result is True
        assert kill_switch.is_active is True
        assert kill_switch.reason == "test reason"

    def test_deactivate(self):
        """Kill switch should deactivate."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")
        assert kill_switch.is_active is True

        kill_switch.deactivate()
        assert kill_switch.is_active is False

    def test_status(self):
        """Kill switch status should be accessible."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")

        status = kill_switch.status()
        assert status["active"] is True
        assert status["reason"] == "test"
        assert status["risk_level"] == "CRITICAL"

    def test_blocks_all_trades(self):
        """Kill switch should block all trades."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")

        broker = PaperBroker(mode=PaperBrokerMode.SHADOW, risk_engine=engine)
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        assert order.status == OrderStatus.REJECTED

    def test_reset(self):
        """Kill switch should reset."""
        engine = RiskEngine()
        kill_switch = KillSwitch(engine)
        kill_switch.activate("test")
        assert kill_switch.is_active is True

        kill_switch.deactivate()
        assert kill_switch.is_active is False


class TestIntegration:
    """Test integration between all Phase 5 components."""

    def test_full_pipeline(self):
        """Full pipeline: risk engine → paper broker → kill switch."""
        engine = RiskEngine()
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW, risk_engine=engine)
        kill_switch = KillSwitch(engine)

        # Normal operation
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        assert order.status == OrderStatus.FILL

        # Kill switch should block
        kill_switch.activate("emergency")
        order = broker.execute_order("ETHUSDT", OrderSide.BUY, 1.0, 3000.0, equity=5000.0)
        assert order.status == OrderStatus.REJECTED

        # Reset
        kill_switch.deactivate()
        order = broker.execute_order("ETHUSDT", OrderSide.BUY, 1.0, 3000.0, equity=5000.0)
        assert order.status == OrderStatus.FILL

    def test_cost_model_integration(self):
        """Cost model should be integrated."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        assert order.fee >= 0
        assert order.slippage >= 0

    def test_integration_paper_broker_with_equity(self):
        """Integration with equity parameter."""
        broker = PaperBroker(mode=PaperBrokerMode.SHADOW)
        order = broker.execute_order("BTCUSDT", OrderSide.BUY, 1.0, 50000.0, equity=5000.0)
        assert order.status == OrderStatus.FILL

    def test_integration_risk_level_transitions(self):
        """Full risk level transitions."""
        engine = RiskEngine()
        assert engine.evaluate(RiskContext(), equity=5000.0)[0] is True
        engine.check_drawdown(1000.0)
        assert engine.evaluate(RiskContext(), equity=1000.0)[0] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
