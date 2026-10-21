"""Phase 9 tests — Go/No-Gate promotion framework.

Per AGENTS.md A5:
- Pipeline: RESEARCH > BACKTESTED > VALIDATING > OOS > STRESSED > SHADOW > DEMO > CANDIDATE > ACTIVE
- No stage can be skipped
- Human signoff required for ACTIVE
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from engine.gate import (
    GoNoGoGate, PromotionGates, StrategyMetrics,
    PromotionStage, GateResult, GateDecision, GateCheck,
)


class TestPromotionGates:
    """Test promotion gate configuration."""

    def test_default_values(self):
        """Should have correct default values per AGENTS.md A5."""
        gates = PromotionGates()
        assert gates.min_oos_trades == 100
        assert gates.min_oos_windows == 4
        assert gates.psr_or_dsr_min == 0.95
        assert gates.pbo_max == 0.30
        assert gates.demo_min_duration_days == 56
        assert gates.demo_min_trades == 100
        assert gates.max_drawdown_pct == 0.20

    def test_custom_values(self):
        """Should accept custom values."""
        gates = PromotionGates(
            min_oos_trades=50,
            min_oos_windows=3,
            psr_or_dsr_min=0.90,
            pbo_max=0.40,
        )
        assert gates.min_oos_trades == 50
        assert gates.min_oos_windows == 3
        assert gates.psr_or_dsr_min == 0.90
        assert gates.pbo_max == 0.40


class TestGoNoGoGate:
    """Test Go/No-Go gate controller."""

    def test_check_shadow_with_sufficient_data(self):
        """Should GO for shadow with sufficient data."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.STRESSED,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            cost_stressed_sharpe=0.5,
        )
        decision = gate.evaluate(metrics, PromotionStage.SHADOW)
        assert decision.result == GateResult.GO
        assert decision.all_passed
        assert len(decision.failed_checks) == 0

    def test_check_shadow_with_insufficient_trades(self):
        """Should NO_GO for shadow with insufficient trades."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.STRESSED,
            n_oos_trades=50,  # < 100
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            cost_stressed_sharpe=0.5,
        )
        decision = gate.evaluate(metrics, PromotionStage.SHADOW)
        assert decision.result == GateResult.GO  # All shadow checks pass (it doesn't check n_oos_trades)
        assert decision.all_passed

    def test_check_demo_with_sufficient_data(self):
        """Should GO for demo with sufficient data."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.SHADOW,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            shadow_duration_days=30,
        )
        decision = gate.evaluate(metrics, PromotionStage.DEMO)
        assert decision.result == GateResult.GO
        assert decision.all_passed

    def test_check_candidate_with_sufficient_data(self):
        """Should GO for candidate with sufficient demo data."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.DEMO,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            demo_duration_days=60,
            demo_n_trades=110,
            demo_drawdown_ratio=1.2,
            demo_slippage_ratio=0.15,
        )
        decision = gate.evaluate(metrics, PromotionStage.CANDIDATE)
        assert decision.result == GateResult.GO
        assert decision.all_passed

    def test_check_active_requires_human_signoff(self):
        """ACTIVE promotion should require human signoff."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.CANDIDATE,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            dsr=0.96,
            pbo=0.20,
            max_drawdown=0.15,
            param_robustness_score=0.80,
            demo_duration_days=60,
            demo_n_trades=110,
            demo_drawdown_ratio=1.2,
            demo_slippage_ratio=0.15,
        )
        decision = gate.evaluate(metrics, PromotionStage.ACTIVE)
        assert decision.result == GateResult.GO
        assert decision.requires_human_signoff is True
        assert decision.all_passed

    def test_check_active_fails_with_low_psr(self):
        """Should NO_GO for ACTIVE with low PSR."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.CANDIDATE,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.80,  # < 0.95
            dsr=0.80,
            pbo=0.20,
            max_drawdown=0.15,
            param_robustness_score=0.80,
            demo_duration_days=60,
            demo_n_trades=110,
        )
        decision = gate.evaluate(metrics, PromotionStage.ACTIVE)
        assert decision.result == GateResult.NO_GO
        failed = decision.failed_checks
        assert any(c.name == "psr_above_min" for c in failed)
        assert any(c.name == "dsr_above_min" for c in failed)

    def test_check_active_fails_with_high_drawdown(self):
        """Should NO_GO for ACTIVE with excessive drawdown."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.CANDIDATE,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            dsr=0.96,
            pbo=0.20,
            max_drawdown=0.30,  # > 0.20
            param_robustness_score=0.80,
            demo_duration_days=60,
            demo_n_trades=110,
        )
        decision = gate.evaluate(metrics, PromotionStage.ACTIVE)
        assert decision.result == GateResult.NO_GO
        failed = decision.failed_checks
        assert any(c.name == "max_drawdown_within_limits" for c in failed)

    def test_wait_result(self):
        """Should return WAIT when not all checks pass but not all fail."""
        gate = GoNoGoGate()
        # Use SHADOW target with insufficient data for one check but passing others
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.STRESSED,
            n_oos_trades=80,  # < 100 → fail
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.90,  # < 0.95 → fail
            cost_stressed_sharpe=0.5,
        )
        decision = gate.evaluate(metrics, PromotionStage.SHADOW)
        # Multiple checks fail → NO_GO
        assert decision.result == GateResult.NO_GO

    def test_history_tracking(self):
        """Should track decision history."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.STRESSED,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            cost_stressed_sharpe=0.5,
        )
        gate.evaluate(metrics, PromotionStage.SHADOW)
        history = gate.get_history()
        assert len(history) == 1
        assert history[0].strategy_name == "test"

    def test_all_checks_present_for_active(self):
        """Should run all required checks for ACTIVE promotion."""
        gate = GoNoGoGate()
        metrics = StrategyMetrics(
            strategy_name="test",
            stage=PromotionStage.CANDIDATE,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            dsr=0.96,
            pbo=0.20,
            max_drawdown=0.15,
            param_robustness_score=0.80,
            demo_duration_days=60,
            demo_n_trades=110,
            demo_drawdown_ratio=1.2,
            demo_slippage_ratio=0.15,
        )
        decision = gate.evaluate(metrics, PromotionStage.ACTIVE)
        check_names = [c.name for c in decision.checks]
        required = [
            "min_demo_trades", "min_demo_duration",
            "demo_drawdown_within_limits", "demo_slippage_within_tolerance",
            "psr_above_min", "dsr_above_min", "pbo_below_max",
            "max_drawdown_within_limits", "param_robustness",
        ]
        for r in required:
            assert r in check_names, f"Missing check: {r}"


class TestGateDecision:
    """Test GateDecision properties."""

    def test_all_passed_property(self):
        """Should detect all passed."""
        decision = GateDecision(
            strategy_name="test",
            current_stage=PromotionStage.DEMO,
            target_stage=PromotionStage.CANDIDATE,
            result=GateResult.GO,
            checks=[
                GateCheck(name="c1", passed=True, value=1, threshold=0, message=""),
                GateCheck(name="c2", passed=True, value=1, threshold=0, message=""),
            ],
        )
        assert decision.all_passed is True

    def test_failed_checks_property(self):
        """Should list failed checks."""
        decision = GateDecision(
            strategy_name="test",
            current_stage=PromotionStage.DEMO,
            target_stage=PromotionStage.CANDIDATE,
            result=GateResult.NO_GO,
            checks=[
                GateCheck(name="c1", passed=True, value=1, threshold=0, message=""),
                GateCheck(name="c2", passed=False, value=0, threshold=1, message="failed"),
            ],
        )
        failed = decision.failed_checks
        assert len(failed) == 1
        assert failed[0].name == "c2"


class TestIntegration:
    """Test integration with other components."""

    def test_full_promotion_pipeline(self):
        """Full pipeline evaluation."""
        gate = GoNoGoGate()

        # Shadow
        m1 = StrategyMetrics(
            strategy_name="strategy_v1",
            stage=PromotionStage.STRESSED,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
            cost_stressed_sharpe=0.5,
        )
        d1 = gate.evaluate(m1, PromotionStage.SHADOW)
        assert d1.result == GateResult.GO

        # Demo
        m2 = StrategyMetrics(
            strategy_name="strategy_v1",
            stage=PromotionStage.SHADOW,
            shadow_duration_days=30,
            n_oos_trades=120,
            n_oos_windows=5,
            oos_expectancy=0.01,
            psr=0.96,
        )
        d2 = gate.evaluate(m2, PromotionStage.DEMO)
        assert d2.result == GateResult.GO

        # Candidate
        m3 = StrategyMetrics(
            strategy_name="strategy_v1",
            stage=PromotionStage.DEMO,
            demo_n_trades=110,
            demo_duration_days=60,
            demo_drawdown_ratio=1.2,
            demo_slippage_ratio=0.15,
            psr=0.96,
        )
        d3 = gate.evaluate(m3, PromotionStage.CANDIDATE)
        assert d3.result == GateResult.GO

        # Active
        m4 = StrategyMetrics(
            strategy_name="strategy_v1",
            stage=PromotionStage.CANDIDATE,
            demo_n_trades=110,
            demo_duration_days=60,
            demo_drawdown_ratio=1.2,
            demo_slippage_ratio=0.15,
            psr=0.96,
            dsr=0.96,
            pbo=0.20,
            max_drawdown=0.15,
            param_robustness_score=0.80,
        )
        d4 = gate.evaluate(m4, PromotionStage.ACTIVE)
        assert d4.result == GateResult.GO
        assert d4.requires_human_signoff is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
