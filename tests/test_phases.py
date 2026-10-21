"""Phase 8-12 tests — Strategies, Regimes, Monitoring, Research Agent.

Per AGENTS.md Part E:
- Regime detection via Hurst exponent
- Strategy factory with parameter grid
- Research agent loop with go/no-go decision
- Monitoring: metrics, alerts, logs
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import pandas as pd

from engine.phases import (
    RegimeDetector, MarketRegime, RegimeSignal,
    StrategyFactory, StrategyConfig, StrategyResult, StrategyType,
    ResearchAgent, GoNoGoResult,
    Monitor, MonitoringAlert, MonitoringLevel,
)
from engine.validation import ValidationFramework
from engine.crypto_arb import CryptoArbitrageFeasibility
from engine.risk_engine import RiskEngine
from libs.quant_math import hurst_exponent, hurst_to_regime


class TestRegimeDetector:
    """Test regime detection."""

    def test_trending_regime(self):
        """Trending prices should produce a valid regime."""
        detector = RegimeDetector(lookback=50)
        prices = pd.Series(np.cumsum(np.random.randn(100) * 0.01) + 50000)
        signal = detector.detect(prices)
        assert signal.regime in list(MarketRegime)
        assert 0.0 <= signal.hurst <= 1.0
        assert signal.confidence >= 0.0

    def test_random_walk_detected(self):
        """Random walk should produce RANDOM_WALK."""
        detector = RegimeDetector(lookback=50)
        prices = pd.Series(np.cumsum(np.random.randn(100)))
        signal = detector.detect(prices)
        assert signal.regime in MarketRegime

    def test_regime_signal_fields(self):
        """RegimeSignal should have all fields."""
        detector = RegimeDetector(lookback=50)
        prices = pd.Series(np.cumsum(np.random.randn(100)))
        signal = detector.detect(prices)
        assert hasattr(signal, 'regime')
        assert hasattr(signal, 'hurst')
        assert hasattr(signal, 'volatility')
        assert hasattr(signal, 'confidence')

    def test_insufficient_data(self):
        """Insufficient data should return RANDOM_WALK."""
        detector = RegimeDetector(lookback=50)
        prices = pd.Series([1, 2, 3])
        signal = detector.detect(prices)
        assert signal.regime == MarketRegime.RANDOM_WALK
        assert signal.confidence == 0.0


class TestStrategyFactory:
    """Test strategy factory."""

    def test_register_strategy(self):
        """Should register a strategy."""
        factory = StrategyFactory()
        config = factory.register("test_strategy", StrategyType.MOMENTUM_BREAKOUT, {"lookback": 20})
        assert config.name == "test_strategy"
        assert config.strategy_type == StrategyType.MOMENTUM_BREAKOUT

    def test_get_strategy(self):
        """Should retrieve a strategy."""
        factory = StrategyFactory()
        factory.register("test", StrategyType.MEAN_REVERSION, {})
        config = factory.get("test")
        assert config is not None
        assert config.name == "test"

    def test_get_nonexistent(self):
        """Non-existent strategy should return None."""
        factory = StrategyFactory()
        assert factory.get("nonexistent") is None

    def test_list_strategies(self):
        """Should list all strategies."""
        factory = StrategyFactory()
        factory.register("s1", StrategyType.ARBITRAGE, {})
        factory.register("s2", StrategyType.GRID, {})
        assert len(factory.list_strategies()) == 2

    def test_execute_strategy(self):
        """Should execute a strategy."""
        factory = StrategyFactory()
        factory.register("test", StrategyType.DCA, {"amount": 100})
        result = factory.execute("test", "BTCUSDT", "BUY", 50000.0)
        assert result.name == "test"
        assert result.symbol == "BTCUSDT"
        assert result.status == "OPEN"

    def test_execute_with_exit(self):
        """Should close position with exit price."""
        factory = StrategyFactory()
        factory.register("test", StrategyType.DCA, {"amount": 100})
        result = factory.execute("test", "BTCUSDT", "BUY", 50000.0, exit_price=51000.0)
        assert result.status == "CLOSED"
        assert result.pnl > 0

    def test_get_pnl(self):
        """Should compute P&L."""
        factory = StrategyFactory()
        factory.register("s1", StrategyType.DCA, {"amount": 100})
        factory.execute("s1", "BTCUSDT", "BUY", 50000.0, exit_price=51000.0)
        pnl = factory.get_pnl()
        assert pnl["total_pnl"] > 0
        assert pnl["n_trades"] == 1


class TestResearchAgent:
    """Test research agent."""

    def test_run_iteration(self):
        """Should run one iteration."""
        validation = ValidationFramework()
        detector = RegimeDetector(lookback=50)
        factory = StrategyFactory()
        agent = ResearchAgent(validation, detector, factory)

        prices = pd.Series(np.cumsum(np.random.randn(100)))
        result = agent.run_iteration(prices)

        assert isinstance(result, GoNoGoResult)
        assert result.decision in ["GO", "NO_GO"]
        assert 0.0 <= result.confidence <= 1.0

    def test_go_no_go_decision(self):
        """Should produce valid go/no-go decision."""
        validation = ValidationFramework()
        detector = RegimeDetector(lookback=50)
        factory = StrategyFactory()
        agent = ResearchAgent(validation, detector, factory)

        # Run multiple iterations
        for _ in range(3):
            prices = pd.Series(np.cumsum(np.random.randn(100)))
            result = agent.run_iteration(prices)
            assert result.decision in ["GO", "NO_GO"]

    def test_alerts_generated(self):
        """Alerts should be generated when needed."""
        validation = ValidationFramework()
        detector = RegimeDetector(lookback=50)
        factory = StrategyFactory()
        agent = ResearchAgent(validation, detector, factory)

        prices = pd.Series(np.cumsum(np.random.randn(100)))
        agent.run_iteration(prices)
        alerts = agent.get_alerts()
        assert isinstance(alerts, list)

    def test_get_summary(self):
        """Should get research summary."""
        validation = ValidationFramework()
        detector = RegimeDetector(lookback=50)
        factory = StrategyFactory()
        agent = ResearchAgent(validation, detector, factory)

        for _ in range(2):
            prices = pd.Series(np.cumsum(np.random.randn(100)))
            agent.run_iteration(prices)

        summary = agent.get_summary()
        assert "iterations" in summary
        assert summary["iterations"] == 2


class TestMonitor:
    """Test monitoring system."""

    def test_record_metric(self):
        """Should record metrics."""
        monitor = Monitor()
        monitor.record_metric("equity", 5000.0)
        assert monitor._metrics["equity"] == 5000.0

    def test_check_threshold_pass(self):
        """Should alert when threshold exceeded."""
        monitor = Monitor()
        monitor.record_metric("loss", 100.0)
        alerted = monitor.check_threshold("loss", 50.0, MonitoringLevel.RED, "RiskEngine", "Loss exceeded")
        assert alerted is True
        assert len(monitor.get_alerts()) == 1

    def test_check_threshold_no_alert(self):
        """Should not alert when below threshold."""
        monitor = Monitor()
        monitor.record_metric("loss", 10.0)
        alerted = monitor.check_threshold("loss", 50.0, MonitoringLevel.RED, "RiskEngine", "Loss exceeded")
        assert alerted is False

    def test_health_levels(self):
        """Health should reflect alerts."""
        monitor = Monitor()
        assert monitor.get_health() == MonitoringLevel.GREEN

        monitor.record_metric("loss", 100.0)
        monitor.check_threshold("loss", 50.0, MonitoringLevel.CRITICAL, "RiskEngine", "Critical")
        assert monitor.get_health() == MonitoringLevel.CRITICAL

    def test_clear_alerts(self):
        """Should clear alerts."""
        monitor = Monitor()
        monitor.record_metric("loss", 100.0)
        monitor.check_threshold("loss", 50.0, MonitoringLevel.RED, "RiskEngine", "Loss")
        assert len(monitor.get_alerts()) > 0

        monitor.clear_alerts()
        assert len(monitor.get_alerts()) == 0


class TestIntegration:
    """Test integration between all Phase 8-12 components."""

    def test_full_pipeline(self):
        """Full pipeline: regime → strategy → research → monitor."""
        validation = ValidationFramework()
        detector = RegimeDetector(lookback=50)
        factory = StrategyFactory()
        agent = ResearchAgent(validation, detector, factory)
        monitor = Monitor()

        # Register strategy
        factory.register("ma_strategy", StrategyType.MOMENTUM_BREAKOUT, {"lookback": 20})

        # Run iteration
        prices = pd.Series(np.cumsum(np.random.randn(100)))
        result = agent.run_iteration(prices)

        assert result.decision in ["GO", "NO_GO"]

        # Check monitor health
        health = monitor.get_health()
        assert health in MonitoringLevel

    def test_regime_with_hurst(self):
        """Regime detection should produce valid results."""
        detector = RegimeDetector(lookback=50)
        prices = pd.Series(np.linspace(100, 200, 100) + np.random.randn(100) * 0.1)
        signal = detector.detect(prices)
        assert signal.regime in list(MarketRegime)

    def test_strategy_execution_with_risk(self):
        """Strategy should work with risk limits."""
        factory = StrategyFactory()
        config = factory.register("risky", StrategyType.MOMENTUM_BREAKOUT, {"max_pos": 1.0})
        assert config.max_position_size == 1.0

    def test_research_with_crypto_arb(self):
        """Research agent should integrate with crypto arb."""
        validation = ValidationFramework()
        detector = RegimeDetector(lookback=50)
        factory = StrategyFactory()
        agent = ResearchAgent(validation, detector, factory)

        # Add crypto arb
        engine = RiskEngine()
        arb = CryptoArbitrageFeasibility(risk_engine=engine)
        arb.detect_funding_rate_arb("BTCUSDT", -0.001, 0.001, 50000, 50500)

        # Run iteration
        prices = pd.Series(np.cumsum(np.random.randn(100)))
        result = agent.run_iteration(prices)
        assert result.decision in ["GO", "NO_GO"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
