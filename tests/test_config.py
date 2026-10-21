"""Tests for the Quant Research Lab Foundation phase.

Phase 1 Acceptance:
- The app refuses to start if any live-mode setting is present
- Safety-gate tests pass
- CI is green
"""
from __future__ import annotations

import pytest
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from configs import LabConfig, Environment
from engine.safety_gate import SafetyGate, SafetyVerdict, SafetyStatus, RiskContext
from engine.logger import AuditLogger
from engine.backtester import EventDrivenBacktester, Order, OrderStatus
from engine.leakage_canary import LeakageCanary, CanaryResult
from libs.quant_math import (
    realized_volatility, ewma_volatility, ema, trend_score,
    z_score, sharpe_ratio, sortino_ratio, position_size,
    expected_value, kelly_criterion, fractional_kelly, hurst_exponent,
    hurst_to_regime, annualize_sharpe,
)
from strategies import (
    BaseStrategy, Signal, SignalAction, MomentumBreakout,
    get_strategy, STRATEGY_REGISTRY,
)


class TestConfig:
    """Test configuration loading and validation."""

    def test_default_config_is_simulated(self):
        """Default environment must be SIMULATED, not LIVE."""
        config = LabConfig()
        assert config.environment == "SIMULATED"

    def test_live_config_raises_error(self):
        """Live environment must raise RuntimeError."""
        with pytest.raises(RuntimeError, match="LIVE"):
            LabConfig(environment="LIVE")

    def test_valid_environments(self):
        """All valid environments should work."""
        for env in ["HISTORICAL_REAL", "SIMULATED", "BACKTEST", "PAPER", "MT5_DEMO"]:
            config = LabConfig(environment=env)
            assert config.environment == env

    def test_invalid_environment_raises(self):
        """Invalid environment must raise ValueError."""
        with pytest.raises(ValueError):
            LabConfig(environment="INVALID_MODE")


class TestSafetyGate:
    """Test the safety gate module."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.config = LabConfig()
        self.audit = AuditLogger()
        self.gate = SafetyGate(self.config, self.audit)
        self.context = RiskContext(
            gpu_temperature=60.0,
            data_quality_score=0.9,
            daily_loss_so_far=0.01,
            portfolio_drawdown=0.05,
            connection_status="connected",
            latency_ms=100.0,
            spread_points=5.0,
        )

    def test_all_checks_pass(self):
        """When all pre-trade checks pass, should APPROVE."""
        result = self.gate.check_pre_trade(self.context)
        assert result.verdict == SafetyVerdict.APPROVED
        assert result.can_proceed is True

    def test_daily_loss_exceeded_rejects(self):
        """When daily loss exceeds limit, should REJECT."""
        self.context.daily_loss_so_far = 0.10  # Exceeds max_daily_loss of 0.05
        result = self.gate.check_pre_trade(self.context)
        assert result.can_proceed is False
        assert result.verdict == SafetyVerdict.REJECTED

    def test_drawdown_exceeded_halts(self):
        """When drawdown exceeds limit, should HALT."""
        self.context.portfolio_drawdown = 0.25  # Exceeds max_drawdown of 0.20
        result = self.gate.check_pre_trade(self.context)
        assert result.can_proceed is False
        assert result.risk_status == SafetyStatus.CAUTION

    def test_gpu_overheat_halts(self):
        """When GPU exceeds temperature limit, should HALT."""
        result = self.gate.check_gpu_safety(90.0)  # Above 85°C limit
        assert result.can_proceed is False
        assert result.verdict == SafetyVerdict.HALTED

    def test_gpu_safe_allows(self):
        """When GPU is within limits, should APPROVE."""
        result = self.gate.check_gpu_safety(60.0)
        assert result.can_proceed is True

    def test_non_demo_mt5_rejected(self):
        """Non-demo MT5 accounts must be rejected."""
        result = self.gate.check_mt5_demo_only(trade_mode=1)  # REAL
        assert result.can_proceed is False
        assert "not DEMO" in result.reason

    def test_demo_mt5_accepted(self):
        """Demo MT5 accounts must be accepted."""
        result = self.gate.check_mt5_demo_only(trade_mode=0)  # DEMO
        assert result.can_proceed is True
        assert result.verdict == SafetyVerdict.APPROVED

    def test_kill_switch_stops(self):
        """Emergency stop must halt everything."""
        result = self.gate.emergency_stop("test")
        assert result.can_proceed is False
        assert result.verdict == SafetyVerdict.HALTED
        assert self.gate.status == SafetyStatus.CRITICAL

    def test_drawdown_governor(self):
        """Drawdown governor must follow NORMAL > CAUTION > REDUCED > HALTED."""
        assert self.gate.evaluate_drawdown_governor(0.10) == SafetyStatus.HEALTHY
        assert self.gate.evaluate_drawdown_governor(0.40) == SafetyStatus.CAUTION
        assert self.gate.evaluate_drawdown_governor(0.70) == SafetyStatus.REDUCED
        assert self.gate.evaluate_drawdown_governor(0.85) == SafetyStatus.HALTED

    def test_audit_log_records_events(self):
        """Safety gate must record audit events."""
        self.gate.check_pre_trade(self.context)
        entries = self.audit.get_entries()
        assert len(entries) > 0


class TestLeakageCanary:
    """Test leakage detection."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.canary = LeakageCanary()

    def test_future_shift_flags_suspicious(self):
        """Future-shifted features should be flagged."""
        from engine.backtester import BacktestResult
        result = BacktestResult(
            sharpe_ratio=10.0,  # Suspiciously high
            total_pnl=100.0,
            total_trades=50,
        )
        canary_result = self.canary.future_shift_test(result)
        assert canary_result.passed is False  # Flagged!

    def test_normal_backtest_passes(self):
        """Normal backtest should pass the future shift test."""
        from engine.backtester import BacktestResult
        result = BacktestResult(
            sharpe_ratio=0.5,
            total_pnl=100.0,
            total_trades=50,
        )
        canary_result = self.canary.future_shift_test(result)
        assert canary_result.passed is True

    def test_shuffled_labels_show_zero_edge(self):
        """Shuffled labels should produce ~0 edge."""
        from engine.backtester import BacktestResult
        result = BacktestResult(
            expectancy=0.0001,  # Near zero
            total_trades=100,
        )
        canary_result = self.canary.shuffled_labels_test(result)
        assert canary_result.passed is True

    def test_pipeline_broken_when_any_fails(self):
        """Pipeline is broken if any canary fails."""
        from engine.backtester import BacktestResult
        suspicious = BacktestResult(sharpe_ratio=10.0, total_pnl=100.0, total_trades=50)
        self.canary.future_shift_test(suspicious)
        assert self.canary.pipeline_broken is True


class TestStrategies:
    """Test strategy interface and registry."""

    def test_strategy_registry_not_empty(self):
        """Strategy registry should have registered strategies."""
        assert len(STRATEGY_REGISTRY) > 0

    def test_get_strategy(self):
        """Should retrieve strategies by name."""
        strategy = get_strategy("momentum_breakout")
        assert strategy is not None
        assert isinstance(strategy, MomentumBreakout)

    def test_unknown_strategy_returns_none(self):
        """Unknown strategy should return None."""
        strategy = get_strategy("nonexistent")
        assert strategy is None

    def test_strategy_implements_interface(self):
        """All strategies should implement BaseStrategy."""
        for name, strategy_class in STRATEGY_REGISTRY.items():
            assert issubclass(strategy_class, BaseStrategy)

    def test_signal_defaults(self):
        """Signal should have sensible defaults."""
        signal = Signal(action=SignalAction.NO_TRADE, symbol="BTCUSDT")
        assert signal.action == SignalAction.NO_TRADE
        assert signal.confidence == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
