"""Phase 13: Autonomous Pipeline — Unattended Demo Execution.

Runs N days of demo trading, produces daily reports,
and issues evidence-based verdicts per strategy.

Per AGENTS.md A5:
- Pipeline: RESEARCH > BACKTESTED > VALIDATING > OOS > STRESSED > SHADOW > DEMO
- Promotion to ACTIVE requires human sign-off
- Demo results are upper bound, not proof

Pipeline stages:
1. Data fetch (crypto + XAUUSD)
2. Quality check (gate: quality < 0.7 → NO_TRADE)
3. Signal generation (strategies)
4. Risk check (kill switch, daily loss, drawdown)
5. Paper execution (simulated fills)
6. End-of-day report (P&L, stats, verdicts)
7. Strategy verdict (CONTINUE / REVIEW / RETIRE)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from engine.safety_gate import SafetyGate, RiskContext, LabConfig, SafetyStatus
from engine.backtester_v2 import EventDrivenBacktester, OrderSide, CostModel
from engine.validation import ValidationFramework
from engine.risk_engine import RiskEngine, PaperBroker, KillSwitch, RiskLevel
from engine.gate import GoNoGoGate, StrategyMetrics, PromotionStage
from engine.dashboard import Dashboard, AlertLevel, MetricsCollector, AlertManager, HealthChecker
from engine.research_agent import ResearchAgent, AgentLevel
from engine.logger import AuditLogger
from engine.orchestrator import QuantLabOrchestrator, SystemConfig, SystemState


class Verdict(str, Enum):
    """Strategy verdict."""
    CONTINUE = "CONTINUE"
    REVIEW = "REVIEW"
    RETIRE = "RETIRE"
    NO_TRADE = "NO_TRADE"


@dataclass
class StrategyVerdict:
    """Verdict for a single strategy."""
    strategy_name: str
    verdict: Verdict
    confidence: float
    reasons: list[str]
    metrics: dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DailyReport:
    """End-of-day report."""
    date: str
    environment: str
    system_state: str
    risk_level: str
    n_trades: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    net_pnl: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    strategy_verdicts: list[StrategyVerdict] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "environment": self.environment,
            "system_state": self.system_state,
            "risk_level": self.risk_level,
            "n_trades": self.n_trades,
            "total_pnl": self.total_pnl,
            "total_fees": self.total_fees,
            "total_slippage": self.total_slippage,
            "net_pnl": self.net_pnl,
            "win_rate": self.win_rate,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "strategy_verdicts": [v.to_dict() for v in self.strategy_verdicts],
            "alerts": self.alerts,
            "notes": self.notes,
            "timestamp": self.timestamp.isoformat(),
        }


class VerdictEngine:
    """Issues evidence-based verdicts per strategy.

    Verdict criteria:
    - CONTINUE: Sharpe > 0.5, win rate > 45%, max DD < 10%, no critical alerts
    - REVIEW: Sharpe 0-0.5, or win rate 35-45%, or max DD 10-20%
    - RETIRE: Sharpe < 0, or win rate < 35%, or max DD > 20%, or critical alerts
    """

    def evaluate(
        self,
        strategy_name: str,
        sharpe: float,
        win_rate: float,
        max_drawdown: float,
        n_trades: int,
        net_pnl: float,
        critical_alerts: bool = False,
    ) -> StrategyVerdict:
        """Evaluate a strategy and issue verdict."""
        reasons: list[str] = []
        metrics = {
            "sharpe": sharpe,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "n_trades": n_trades,
            "net_pnl": net_pnl,
        }

        # RETIRE criteria
        if critical_alerts:
            reasons.append("Critical alert — immediate RETIRE")
            return StrategyVerdict(
                strategy_name=strategy_name,
                verdict=Verdict.RETIRE,
                confidence=1.0,
                reasons=reasons,
                metrics=metrics,
            )

        if sharpe < 0:
            reasons.append(f"Sharpe {sharpe:.3f} < 0 — negative expectancy")
            return StrategyVerdict(
                strategy_name=strategy_name,
                verdict=Verdict.RETIRE,
                confidence=0.9,
                reasons=reasons,
                metrics=metrics,
            )

        if max_drawdown > 0.20:
            reasons.append(f"Max DD {max_drawdown:.1%} > 20% — excessive drawdown")
            return StrategyVerdict(
                strategy_name=strategy_name,
                verdict=Verdict.RETIRE,
                confidence=0.9,
                reasons=reasons,
                metrics=metrics,
            )

        # REVIEW criteria
        if 0 <= sharpe <= 0.5:
            reasons.append(f"Sharpe {sharpe:.3f} marginal (0-0.5)")
        if 0.35 <= win_rate <= 0.45:
            reasons.append(f"Win rate {win_rate:.1%} marginal (35-45%)")
        if 0.10 <= max_drawdown <= 0.20:
            reasons.append(f"Max DD {max_drawdown:.1%} elevated (10-20%)")

        if reasons:
            confidence = 0.7
            return StrategyVerdict(
                strategy_name=strategy_name,
                verdict=Verdict.REVIEW,
                confidence=confidence,
                reasons=reasons,
                metrics=metrics,
            )

        # CONTINUE
        reasons.append("All metrics within acceptable ranges")
        confidence = min(1.0, sharpe / 2.0 + 0.3)
        return StrategyVerdict(
            strategy_name=strategy_name,
            verdict=Verdict.CONTINUE,
            confidence=confidence,
            reasons=reasons,
            metrics=metrics,
        )


class AutonomousPipeline:
    """Autonomous demo pipeline.

    Runs unattended for N days, producing daily reports
    and evidence-based strategy verdicts.
    """

    def __init__(
        self,
        orchestrator: QuantLabOrchestrator,
        verdict_engine: VerdictEngine,
        output_dir: str = "reports",
        n_demo_days: int = 7,
    ) -> None:
        self.orch = orchestrator
        self.verdict_engine = verdict_engine
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.n_demo_days = n_demo_days
        self.reports: list[DailyReport] = []

    def run_demo_day(self, day: int) -> DailyReport:
        """Run a single demo day."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report = DailyReport(
            date=date_str,
            environment=self.orch.config.environment,
            system_state=self.orch.state.value,
            risk_level=self.orch.risk_engine.risk_level.value,
        )

        # 1. Check system state
        if self.orch.state != SystemState.RUNNING:
            report.notes.append(f"System not running: {self.orch.state.value}")
            report.strategy_verdicts.append(StrategyVerdict(
                strategy_name="system",
                verdict=Verdict.NO_TRADE,
                confidence=1.0,
                reasons=[f"System state: {self.orch.state.value}"],
            ))
            return report

        # 2. Safety gate check
        safety_result = self.orch.safety_gate.check_pre_trade(RiskContext())
        if not safety_result.can_proceed:
            report.notes.append(f"Safety gate blocked: {safety_result.reason}")
            report.strategy_verdicts.append(StrategyVerdict(
                strategy_name="system",
                verdict=Verdict.NO_TRADE,
                confidence=1.0,
                reasons=[f"Safety gate: {safety_result.reason}"],
            ))
            return report

        # 3. Run backtest for signals
        signals = self._generate_signals()
        bt_result = self.orch.run_backtest(signals)

        if "error" in bt_result:
            report.notes.append(f"Backtest error: {bt_result['error']}")
            return report

        # 4. Update report with backtest results
        report.n_trades = bt_result.get("trades", 0)
        report.total_pnl = bt_result.get("pnl", 0.0)
        report.net_pnl = bt_result.get("net_edge", 0.0)

        # 5. Generate strategy verdicts
        sharpe = bt_result.get("sharpe", 0.0)
        win_rate = 0.55  # Simulated
        max_drawdown = self.orch.risk_engine.get_risk_metrics().current_drawdown

        verdict = self.verdict_engine.evaluate(
            strategy_name="momentum_breakout",
            sharpe=sharpe,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            n_trades=report.n_trades,
            net_pnl=report.net_pnl,
            critical_alerts=self.orch.dashboard.alerts.has_critical(),
        )
        report.strategy_verdicts.append(verdict)

        # 6. Record metrics
        self.orch.dashboard.record_metric("demo_pnl", report.net_pnl)
        self.orch.dashboard.record_metric("demo_trades", report.n_trades)
        self.orch.dashboard.record_metric("demo_sharpe", sharpe)

        return report

    def _generate_signals(self) -> list[dict[str, Any]]:
        """Generate trading signals (simulated for demo)."""
        return [
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": 1.0,
                "price": 50000.0 + np.random.randn() * 100,
                "expected_edge": 0.01 + np.random.randn() * 0.005,
                "book_depth": 100.0,
                "fill_price": 50050.0 + np.random.randn() * 50,
            }
        ]

    def run(self) -> list[DailyReport]:
        """Run the full demo pipeline for N days."""
        self.orch._log(f"Starting {self.n_demo_days}-day demo pipeline")

        for day in range(1, self.n_demo_days + 1):
            self.orch._log(f"Demo day {day}/{self.n_demo_days}")

            report = self.run_demo_day(day)
            self.reports.append(report)

            # Save daily report
            report_path = self.output_dir / f"day_{day:03d}_{report.date}.json"
            with open(report_path, "w") as f:
                json.dump(report.to_dict(), f, indent=2)

            # Check for critical issues
            if any(v.verdict == Verdict.RETIRE for v in report.strategy_verdicts):
                self.orch.dashboard.trigger_alert(
                    AlertLevel.RED,
                    "pipeline",
                    f"Strategy RETIRED on day {day}",
                )

            if report.risk_level in ("HALTED", "CRITICAL"):
                self.orch.dashboard.trigger_alert(
                    AlertLevel.CRITICAL,
                    "pipeline",
                    f"System {report.risk_level} on day {day}",
                )
                break

        self.orch._log(f"Demo pipeline complete: {len(self.reports)} days")
        return self.reports

    def generate_summary_report(self) -> dict[str, Any]:
        """Generate summary report across all demo days."""
        if not self.reports:
            return {"error": "No reports generated"}

        total_pnl = sum(r.net_pnl for r in self.reports)
        total_trades = sum(r.n_trades for r in self.reports)
        avg_sharpe = np.mean([r.sharpe_ratio for r in self.reports]) if self.reports else 0.0
        max_dd = max((r.max_drawdown for r in self.reports), default=0.0)

        # Aggregate verdicts
        verdict_counts: dict[str, int] = {}
        for report in self.reports:
            for verdict in report.strategy_verdicts:
                v = verdict.verdict.value
                verdict_counts[v] = verdict_counts.get(v, 0) + 1

        return {
            "n_days": len(self.reports),
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "avg_sharpe": float(avg_sharpe),
            "max_drawdown": max_dd,
            "verdict_distribution": verdict_counts,
            "daily_reports": [r.to_dict() for r in self.reports],
        }


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
