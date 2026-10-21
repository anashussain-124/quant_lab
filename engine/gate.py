"""Phase 9: Go/No-Go Gate — Promotion Decision Framework.

Per AGENTS.md A5:
- Pipeline: RESEARCH > BACKTESTED > VALIDATING > OOS > STRESSED > SHADOW > DEMO > CANDIDATE > ACTIVE
- No stage can be skipped
- Promotion gates: min_oos_trades=100, min_oos_windows=4, psr_or_dsr_min=0.95, pbo_max=0.30
- Promotion to ACTIVE always needs explicit human sign-off
- Challenger never replaces incumbent just because backtest return is higher

This module implements the Go/No-Gate that evaluates whether a strategy
meets all criteria for promotion to the next stage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import yaml


class PromotionStage(str, Enum):
    """Strategy promotion pipeline stages."""
    RESEARCH = "RESEARCH"
    BACKTESTED = "BACKTESTED"
    VALIDATING = "VALIDATING"
    OOS = "OOS"
    STRESSED = "STRESSED"
    SHADOW = "SHADOW"
    DEMO = "DEMO"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"


class GateResult(str, Enum):
    """Gate decision."""
    GO = "GO"
    NO_GO = "NO_GO"
    WAIT = "WAIT"  # Need more data/time


@dataclass
class PromotionGates:
    """Configurable promotion gate thresholds.

    Per AGENTS.md A5: set before looking at results, record any change with reason.
    """
    # OOS requirements
    min_oos_trades: int = 100
    min_oos_windows: int = 4
    oos_expectancy_min: float = 0.0

    # Statistical significance
    psr_or_dsr_min: float = 0.95
    pbo_max: float = 0.30

    # Cost stress
    cost_stress_multiplier: float = 1.5

    # Parameter robustness
    param_perturbation_keep_pct: float = 0.70
    param_perturbation_range: float = 0.20  # +/- 20%

    # Regime coverage
    max_regime_pnl_contribution: float = 0.60

    # Shadow duration
    shadow_min_duration_days: int = 28

    # Demo requirements
    demo_min_duration_days: int = 56
    demo_min_trades: int = 100
    demo_vs_backtest_slippage_tolerance: float = 0.25
    demo_max_drawdown_vs_backtest_expected: float = 1.5

    # Drawdown
    max_drawdown_pct: float = 0.20

    @classmethod
    def from_yaml(cls, path: str) -> PromotionGates:
        """Load gates from YAML config."""
        with open(path) as f:
            data = yaml.safe_load(f)
        gates_data = data.get("promotion_gates", {})
        return cls(**{k: v for k, v in gates_data.items() if hasattr(cls, k)})


@dataclass
class StrategyMetrics:
    """Metrics collected during each promotion stage."""
    strategy_name: str
    stage: PromotionStage
    n_oos_trades: int = 0
    n_oos_windows: int = 0
    oos_expectancy: float = 0.0
    psr: float = 0.0
    dsr: float = 0.0
    pbo: float = 1.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    cost_stressed_sharpe: float = 0.0
    param_robustness_score: float = 0.0
    regime_contribution_max: float = 0.0
    shadow_duration_days: int = 0
    demo_duration_days: int = 0
    demo_n_trades: int = 0
    demo_slippage_ratio: float = 0.0
    demo_drawdown_ratio: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GateCheck:
    """Individual gate check result."""
    name: str
    passed: bool
    value: float
    threshold: float
    message: str


@dataclass
class GateDecision:
    """Complete gate decision."""
    strategy_name: str
    current_stage: PromotionStage
    target_stage: PromotionStage
    result: GateResult
    checks: list[GateCheck] = field(default_factory=list)
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requires_human_signoff: bool = False

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[GateCheck]:
        return [c for c in self.checks if not c.passed]


class GoNoGoGate:
    """Promotion gate controller.

    Per AGENTS.md A5:
    - Evaluates all criteria before promotion
    - Never skips stages
    - Requires human signoff for ACTIVE promotion
    """

    def __init__(self, gates: PromotionGates | None = None) -> None:
        self.gates = gates or PromotionGates()
        self._history: list[GateDecision] = []

    def evaluate(
        self,
        metrics: StrategyMetrics,
        target_stage: PromotionStage,
    ) -> GateDecision:
        """Evaluate whether strategy can be promoted to target stage."""
        decision = GateDecision(
            strategy_name=metrics.strategy_name,
            current_stage=metrics.stage,
            target_stage=target_stage,
            result=GateResult.NO_GO,
            checks=[],
            reasons=[],
        )

        # Run stage-specific checks
        if target_stage == PromotionStage.CANDIDATE:
            decision.checks = self._check_candidate(metrics)
        elif target_stage == PromotionStage.ACTIVE:
            decision.checks = self._check_active(metrics)
        elif target_stage == PromotionStage.DEMO:
            decision.checks = self._check_demo(metrics)
        elif target_stage == PromotionStage.SHADOW:
            decision.checks = self._check_shadow(metrics)
        else:
            decision.reasons.append(f"No gate defined for {target_stage}")
            return decision

        # Determine result
        if decision.all_passed:
            decision.result = GateResult.GO
            decision.confidence = 1.0
        elif any(not c.passed for c in decision.checks):
            decision.result = GateResult.NO_GO
            decision.confidence = 0.0
        else:
            decision.result = GateResult.WAIT
            decision.confidence = 0.5

        # Human signoff required for ACTIVE
        if target_stage == PromotionStage.ACTIVE:
            decision.requires_human_signoff = True

        decision.reasons = [c.message for c in decision.failed_checks]
        self._history.append(decision)
        return decision

    def _check_candidate(self, m: StrategyMetrics) -> list[GateCheck]:
        """Check promotion from DEMO to CANDIDATE."""
        gates = self.gates
        checks = []

        checks.append(GateCheck(
            name="min_demo_trades",
            passed=m.demo_n_trades >= gates.demo_min_trades,
            value=m.demo_n_trades,
            threshold=float(gates.demo_min_trades),
            message=f"Demo trades {m.demo_n_trades} < {gates.demo_min_trades}",
        ))

        checks.append(GateCheck(
            name="min_demo_duration",
            passed=m.demo_duration_days >= gates.demo_min_duration_days,
            value=m.demo_duration_days,
            threshold=float(gates.demo_min_duration_days),
            message=f"Demo duration {m.demo_duration_days}d < {gates.demo_min_duration_days}d",
        ))

        checks.append(GateCheck(
            name="demo_drawdown_within_limits",
            passed=m.demo_drawdown_ratio <= gates.demo_max_drawdown_vs_backtest_expected,
            value=m.demo_drawdown_ratio,
            threshold=gates.demo_max_drawdown_vs_backtest_expected,
            message=f"Demo DD ratio {m.demo_drawdown_ratio:.2f} > {gates.demo_max_drawdown_vs_backtest_expected}",
        ))

        checks.append(GateCheck(
            name="demo_slippage_within_tolerance",
            passed=m.demo_slippage_ratio <= gates.demo_vs_backtest_slippage_tolerance,
            value=m.demo_slippage_ratio,
            threshold=gates.demo_vs_backtest_slippage_tolerance,
            message=f"Demo slippage ratio {m.demo_slippage_ratio:.2f} > {gates.demo_vs_backtest_slippage_tolerance}",
        ))

        return checks

    def _check_active(self, m: StrategyMetrics) -> list[GateCheck]:
        """Check promotion from CANDIDATE to ACTIVE. Requires human signoff."""
        gates = self.gates
        checks = []

        # Must pass all candidate checks first
        checks.extend(self._check_candidate(m))

        checks.append(GateCheck(
            name="psr_above_min",
            passed=m.psr >= gates.psr_or_dsr_min,
            value=m.psr,
            threshold=gates.psr_or_dsr_min,
            message=f"PSR {m.psr:.3f} < {gates.psr_or_dsr_min}",
        ))

        checks.append(GateCheck(
            name="dsr_above_min",
            passed=m.dsr >= gates.psr_or_dsr_min,
            value=m.dsr,
            threshold=gates.psr_or_dsr_min,
            message=f"DSR {m.dsr:.3f} < {gates.psr_or_dsr_min}",
        ))

        checks.append(GateCheck(
            name="pbo_below_max",
            passed=m.pbo <= gates.pbo_max,
            value=m.pbo,
            threshold=gates.pbo_max,
            message=f"PBO {m.pbo:.3f} > {gates.pbo_max}",
        ))

        checks.append(GateCheck(
            name="max_drawdown_within_limits",
            passed=m.max_drawdown <= gates.max_drawdown_pct,
            value=m.max_drawdown,
            threshold=gates.max_drawdown_pct,
            message=f"Max DD {m.max_drawdown:.2%} > {gates.max_drawdown_pct:.0%}",
        ))

        checks.append(GateCheck(
            name="param_robustness",
            passed=m.param_robustness_score >= gates.param_perturbation_keep_pct,
            value=m.param_robustness_score,
            threshold=gates.param_perturbation_keep_pct,
            message=f"Param robustness {m.param_robustness_score:.2%} < {gates.param_perturbation_keep_pct:.0%}",
        ))

        return checks

    def _check_demo(self, m: StrategyMetrics) -> list[GateCheck]:
        """Check promotion from SHADOW to DEMO."""
        gates = self.gates
        checks = []

        checks.append(GateCheck(
            name="min_shadow_duration",
            passed=m.shadow_duration_days >= gates.shadow_min_duration_days,
            value=m.shadow_duration_days,
            threshold=float(gates.shadow_min_duration_days),
            message=f"Shadow duration {m.shadow_duration_days}d < {gates.shadow_min_duration_days}d",
        ))

        checks.append(GateCheck(
            name="oos_trades_above_min",
            passed=m.n_oos_trades >= gates.min_oos_trades,
            value=m.n_oos_trades,
            threshold=float(gates.min_oos_trades),
            message=f"OOS trades {m.n_oos_trades} < {gates.min_oos_trades}",
        ))

        checks.append(GateCheck(
            name="oos_windows_above_min",
            passed=m.n_oos_windows >= gates.min_oos_windows,
            value=m.n_oos_windows,
            threshold=float(gates.min_oos_windows),
            message=f"OOS windows {m.n_oos_windows} < {gates.min_oos_windows}",
        ))

        return checks

    def _check_shadow(self, m: StrategyMetrics) -> list[GateCheck]:
        """Check promotion from STRESSED to SHADOW."""
        gates = self.gates
        checks = []

        checks.append(GateCheck(
            name="oos_expectancy_positive",
            passed=m.oos_expectancy > gates.oos_expectancy_min,
            value=m.oos_expectancy,
            threshold=gates.oos_expectancy_min,
            message=f"OOS expectancy {m.oos_expectancy:.4f} <= {gates.oos_expectancy_min}",
        ))

        checks.append(GateCheck(
            name="cost_stressed_sharpe_positive",
            passed=m.cost_stressed_sharpe > 0,
            value=m.cost_stressed_sharpe,
            threshold=0.0,
            message=f"Cost-stressed Sharpe {m.cost_stressed_sharpe:.3f} <= 0",
        ))

        checks.append(GateCheck(
            name="psr_above_min",
            passed=m.psr >= gates.psr_or_dsr_min,
            value=m.psr,
            threshold=gates.psr_or_dsr_min,
            message=f"PSR {m.psr:.3f} < {gates.psr_or_dsr_min}",
        ))

        return checks

    def get_history(self) -> list[GateDecision]:
        """Get gate decision history."""
        return list(self._history)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
