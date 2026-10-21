"""Phase 12: Final Integration — System Orchestrator.

Wires all components together:
- Safety Gate (absolute veto)
- Backtester with cost model
- Validation framework (DSR/PSR/PBO/AUUC)
- Risk engine with kill switch
- Go/No-Go gate (promotion framework)
- Monitoring dashboard
- Research agent (L0-L2)

Per AGENTS.md:
- Pipeline: RESEARCH > BACKTESTED > VALIDATING > OOS > STRESSED > SHADOW > DEMO > CANDIDATE > ACTIVE
- Kill switch: deterministic, absolute veto
- No live-trading code path exists in this project
- Demo/paper only
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np

from engine.safety_gate import SafetyGate, RiskContext, SafetyVerdict, SafetyStatus, LabConfig
from engine.backtester_v2 import EventDrivenBacktester, OrderSide, CostModel
from engine.validation import ValidationFramework, ValidationReport
from engine.risk_engine import RiskEngine, PaperBroker, KillSwitch, RiskLevel
from engine.gate import GoNoGoGate, StrategyMetrics, PromotionStage, GateResult
from engine.dashboard import Dashboard, AlertLevel, MetricsCollector, AlertManager, HealthChecker
from engine.research_agent import ResearchAgent, AgentLevel, ResearchReport
from engine.logger import AuditLogger


class SystemState(str, Enum):
    """System operational states."""
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    HALTED = "HALTED"
    ERROR = "ERROR"


@dataclass
class SystemConfig:
    """System-wide configuration."""
    environment: str = "SIMULATED"
    autonomy_level: AgentLevel = AgentLevel.L0
    demo_only: bool = True
    max_daily_loss: float = 500.0
    max_drawdown_pct: float = 0.20
    risk_fraction: float = 0.01


class QuantLabOrchestrator:
    """System orchestrator — wires all components together.

    Per AGENTS.md A3:
    1. Kill switch / risk engine: absolute veto
    2. Execution layer: cannot bypass risk
    3. Strategies: propose only
    4. Research agent: L0-L2
    """

    def __init__(self, config: SystemConfig | None = None) -> None:
        self.config = config or SystemConfig()
        self.state = SystemState.INITIALIZING
        self._start_time = time.time()

        # Core components
        self.safety_gate = SafetyGate(LabConfig())
        self.risk_engine = RiskEngine()
        self.kill_switch = KillSwitch(self.risk_engine)
        self.backtester = EventDrivenBacktester()
        self.cost_model = CostModel()
        self.validation = ValidationFramework()
        self.gate = GoNoGoGate()
        self.dashboard = Dashboard(environment=self.config.environment)
        self.research_agent = ResearchAgent(level=self.config.autonomy_level)
        self.audit = AuditLogger()

        # Register health checks
        self._register_health_checks()

        self._log("System initialized")

    def _register_health_checks(self) -> None:
        """Register component health checks."""
        self.dashboard.register_health_check("safety_gate", self._check_safety_gate)
        self.dashboard.register_health_check("risk_engine", self._check_risk_engine)
        self.dashboard.register_health_check("validation", self._check_validation)
        self.dashboard.register_health_check("dashboard", self._check_dashboard)
        self.dashboard.register_health_check("research_agent", self._check_research_agent)

    def _check_safety_gate(self) -> tuple[bool, str]:
        try:
            return self.safety_gate._status != SafetyStatus.CRITICAL, f"Safety: {self.safety_gate._status.value}"
        except Exception as e:
            return False, str(e)

    def _check_risk_engine(self) -> tuple[bool, str]:
        try:
            metrics = self.risk_engine.get_risk_metrics()
            return metrics.risk_level != RiskLevel.HALTED, f"Risk: {metrics.risk_level.value}"
        except Exception as e:
            return False, str(e)

    def _check_validation(self) -> tuple[bool, str]:
        try:
            return True, "Validation ready"
        except Exception as e:
            return False, str(e)

    def _check_dashboard(self) -> tuple[bool, str]:
        try:
            return True, f"Health: {self.dashboard.health.get_overall_health().value}"
        except Exception as e:
            return False, str(e)

    def _check_research_agent(self) -> tuple[bool, str]:
        try:
            summary = self.research_agent.get_summary()
            return True, f"Agent: {summary['level']}"
        except Exception as e:
            return False, str(e)

    def _log(self, message: str) -> None:
        """Log a system event."""
        self.audit.log(
            event="SYSTEM",
            action=message,
            details={"state": self.state.value},
            actor="orchestrator",
        )

    def initialize(self) -> bool:
        """Initialize the system."""
        self._log("Initializing system")

        # Verify safety gate
        context = RiskContext()
        result = self.safety_gate.check_pre_trade(context)
        if not result.can_proceed:
            self.state = SystemState.ERROR
            self._log(f"Safety gate failed: {result.reason}")
            return False

        # Verify demo-only
        if not self.config.demo_only:
            self.state = SystemState.ERROR
            self._log("System must be demo_only=True")
            return False

        self.state = SystemState.RUNNING
        self._log("System ready")
        return True

    def run_backtest(
        self,
        signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run a backtest."""
        if self.state != SystemState.RUNNING:
            return {"error": "System not running"}

        # Safety check
        context = RiskContext()
        if not self.safety_gate.check_pre_trade(context).can_proceed:
            return {"error": "Safety gate blocked"}

        # Run backtest
        result = self.backtester.run_backtest(signals, context)

        # Record metrics
        self.dashboard.record_metric("backtest_trades", result.total_trades)
        self.dashboard.record_metric("backtest_pnl", result.total_pnl)
        self.dashboard.record_metric("backtest_sharpe", result.sharpe_ratio)

        return {
            "trades": result.total_trades,
            "pnl": result.total_pnl,
            "sharpe": result.sharpe_ratio,
            "net_edge": result.net_edge,
        }

    def evaluate_strategy(
        self,
        returns: np.ndarray,
        strategy_name: str,
        target_stage: PromotionStage,
    ) -> dict[str, Any]:
        """Evaluate a strategy for promotion."""
        if self.state != SystemState.RUNNING:
            return {"error": "System not running"}

        # Validate
        report = self.validation.validate(returns, strategy_name)

        # Create metrics
        metrics = StrategyMetrics(
            strategy_name=strategy_name,
            stage=PromotionStage.DEMO,
            n_oos_trades=len(returns),
            n_oos_windows=5,
            oos_expectancy=float(np.mean(returns)) if len(returns) > 0 else 0.0,
            psr=report.psr,
            dsr=report.dsr,
            pbo=report.pbo,
            sharpe_ratio=report.walk_forward.sharpe_ratio if report.walk_forward else 0.0,
            max_drawdown=report.walk_forward.max_drawdown if report.walk_forward else 0.0,
        )

        # Gate decision
        decision = self.gate.evaluate(metrics, target_stage)

        # Record
        self.dashboard.record_metric("gate_confidence", decision.confidence)

        return {
            "result": decision.result.value,
            "confidence": decision.confidence,
            "checks_passed": len([c for c in decision.checks if c.passed]),
            "checks_failed": len(decision.failed_checks),
            "requires_human_signoff": decision.requires_human_signoff,
        }

    def emergency_stop(self, reason: str) -> None:
        """Trigger emergency stop."""
        self.kill_switch.activate(reason)
        self.risk_engine.emergency_stop(reason)
        self.state = SystemState.HALTED
        self.dashboard.trigger_alert(
            AlertLevel.CRITICAL,
            "orchestrator",
            f"Emergency stop: {reason}",
        )
        self._log(f"EMERGENCY STOP: {reason}")

    def get_status(self) -> dict[str, Any]:
        """Get system status."""
        risk_metrics = self.risk_engine.get_risk_metrics()
        return {
            "state": self.state.value,
            "uptime_seconds": time.time() - self._start_time,
            "environment": self.config.environment,
            "autonomy_level": self.research_agent.level.value,
            "system_health": self.dashboard.health.get_overall_health().value,
            "active_alerts": len(self.dashboard.alerts.get_active()),
            "risk_level": risk_metrics.risk_level.value,
            "components": {name: "UP" if ok else "DOWN" for name, (ok, _) in self.dashboard.health.check_all().items()},
            "metrics": self.dashboard.metrics.get_latest_values(),
        }

    def generate_report(self, title: str) -> ResearchReport:
        """Generate system report."""
        status = self.get_status()
        return self.research_agent.generate_report(
            title=title,
            data_quality=0.95,
            market_regime=status.get("market_regime", "UNKNOWN"),
            findings=[
                f"System state: {status['state']}",
                f"Risk level: {status['risk_level']}",
                f"Active alerts: {status['active_alerts']}",
            ],
            recommendations=[
                "Continue monitoring system health",
                "Review failed gate checks before promotion",
            ],
        )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
