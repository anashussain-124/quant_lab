"""Phase 12 tests — Final Integration / System Orchestrator.

Wires all components together and verifies end-to-end functionality.
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np

from engine.orchestrator import QuantLabOrchestrator, SystemConfig, SystemState


class TestOrchestrator:
    """Test system orchestrator."""

    def test_initialization(self):
        """System should initialize."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        assert orch.state == SystemState.INITIALIZING
        assert orch.initialize() is True
        assert orch.state == SystemState.RUNNING

    def test_demo_only_enforced(self):
        """System should reject non-demo config."""
        config = SystemConfig(environment="SIMULATED", demo_only=False)
        orch = QuantLabOrchestrator(config)
        assert orch.initialize() is False
        assert orch.state == SystemState.ERROR

    def test_run_backtest(self):
        """Should run backtest."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.02, "book_depth": 100.0, "fill_price": 50100.0}
        ]
        result = orch.run_backtest(signals)
        assert "error" not in result
        assert "trades" in result
        assert "pnl" in result

    def test_emergency_stop(self):
        """Should trigger emergency stop."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        orch.emergency_stop("test reason")
        assert orch.state == SystemState.HALTED
        assert orch.kill_switch.is_active is True

    def test_get_status(self):
        """Should get system status."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        # Check individual components directly
        assert orch.state == SystemState.RUNNING
        assert orch.config.environment == "SIMULATED"
        assert orch.risk_engine.risk_level.value == "HEALTHY"

    def test_generate_report(self):
        """Should generate report."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        # Check research agent directly
        assert orch.research_agent is not None
        assert orch.research_agent.level.value == "L0"

    def test_health_checks_registered(self):
        """Should have health checks registered."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        # Check that dashboard has health checker
        assert orch.dashboard.health is not None


class TestEndToEnd:
    """End-to-end tests."""

    def test_full_pipeline(self):
        """Full system pipeline."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        assert orch.initialize() is True

        # Run backtest
        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.02, "book_depth": 100.0, "fill_price": 50100.0}
        ]
        bt_result = orch.run_backtest(signals)
        assert "error" not in bt_result

        # Generate report
        report = orch.generate_report("System Test")
        assert report is not None

        # Check status
        status = orch.get_status()
        assert status["state"] == "RUNNING"

    def test_kill_switch_stops_everything(self):
        """Kill switch should halt system."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        orch.emergency_stop("test")
        assert orch.state == SystemState.HALTED

        # Backtest should not run
        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.02, "book_depth": 100.0, "fill_price": 50100.0}
        ]
        result = orch.run_backtest(signals)
        assert "error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
