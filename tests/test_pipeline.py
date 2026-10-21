"""Phase 13 tests — Autonomous Pipeline."""
from __future__ import annotations

import pytest
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import numpy as np

from engine.pipeline import (
    AutonomousPipeline, VerdictEngine, Verdict,
    StrategyVerdict, DailyReport,
)
from engine.orchestrator import QuantLabOrchestrator, SystemConfig, SystemState


class TestVerdictEngine:
    """Test verdict engine."""

    def test_continue_verdict(self):
        """Should CONTINUE with good metrics."""
        engine = VerdictEngine()
        verdict = engine.evaluate(
            strategy_name="test",
            sharpe=1.5,
            win_rate=0.55,
            max_drawdown=0.05,
            n_trades=100,
            net_pnl=500.0,
        )
        assert verdict.verdict == Verdict.CONTINUE

    def test_review_verdict_marginal_sharpe(self):
        """Should REVIEW with marginal Sharpe."""
        engine = VerdictEngine()
        verdict = engine.evaluate(
            strategy_name="test",
            sharpe=0.3,
            win_rate=0.55,
            max_drawdown=0.05,
            n_trades=100,
            net_pnl=100.0,
        )
        assert verdict.verdict == Verdict.REVIEW

    def test_review_verdict_low_winrate(self):
        """Should REVIEW with low win rate."""
        engine = VerdictEngine()
        verdict = engine.evaluate(
            strategy_name="test",
            sharpe=0.8,
            win_rate=0.38,
            max_drawdown=0.05,
            n_trades=100,
            net_pnl=100.0,
        )
        assert verdict.verdict == Verdict.REVIEW

    def test_retire_verdict_negative_sharpe(self):
        """Should RETIRE with negative Sharpe."""
        engine = VerdictEngine()
        verdict = engine.evaluate(
            strategy_name="test",
            sharpe=-0.5,
            win_rate=0.55,
            max_drawdown=0.05,
            n_trades=100,
            net_pnl=-100.0,
        )
        assert verdict.verdict == Verdict.RETIRE

    def test_retire_verdict_high_drawdown(self):
        """Should RETIRE with high drawdown."""
        engine = VerdictEngine()
        verdict = engine.evaluate(
            strategy_name="test",
            sharpe=0.5,
            win_rate=0.55,
            max_drawdown=0.25,
            n_trades=100,
            net_pnl=100.0,
        )
        assert verdict.verdict == Verdict.RETIRE

    def test_retire_verdict_critical_alerts(self):
        """Should RETIRE with critical alerts."""
        engine = VerdictEngine()
        verdict = engine.evaluate(
            strategy_name="test",
            sharpe=1.5,
            win_rate=0.55,
            max_drawdown=0.05,
            n_trades=100,
            net_pnl=500.0,
            critical_alerts=True,
        )
        assert verdict.verdict == Verdict.RETIRE

    def test_verdict_to_dict(self):
        """Should serialize to dict."""
        verdict = StrategyVerdict(
            strategy_name="test",
            verdict=Verdict.CONTINUE,
            confidence=0.9,
            reasons=["Good"],
            metrics={"sharpe": 1.5},
        )
        d = verdict.to_dict()
        assert d["strategy_name"] == "test"
        assert d["verdict"] == "CONTINUE"


class TestDailyReport:
    """Test daily report."""

    def test_to_dict(self):
        """Should serialize to dict."""
        report = DailyReport(
            date="2026-09-20",
            environment="SIMULATED",
            system_state="RUNNING",
            risk_level="HEALTHY",
            n_trades=5,
            total_pnl=100.0,
            net_pnl=90.0,
        )
        d = report.to_dict()
        assert d["date"] == "2026-09-20"
        assert d["n_trades"] == 5


class TestAutonomousPipeline:
    """Test autonomous pipeline."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for reports."""
        dirpath = tempfile.mkdtemp()
        yield dirpath
        shutil.rmtree(dirpath)

    @pytest.fixture
    def pipeline(self, temp_dir):
        """Create a pipeline instance."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()
        verdict_engine = VerdictEngine()
        return AutonomousPipeline(
            orchestrator=orch,
            verdict_engine=verdict_engine,
            output_dir=temp_dir,
            n_demo_days=3,
        )

    def test_initialization(self):
        """Should initialize."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()
        pipeline = AutonomousPipeline(
            orchestrator=orch,
            verdict_engine=VerdictEngine(),
            n_demo_days=3,
        )
        assert pipeline.n_demo_days == 3

    def test_run_demo_day(self, pipeline):
        """Should run a demo day."""
        report = pipeline.run_demo_day(1)
        assert isinstance(report, DailyReport)
        assert report.system_state == "RUNNING"

    def test_run_pipeline(self, pipeline):
        """Should run full pipeline."""
        reports = pipeline.run()
        assert len(reports) == 3

    def test_reports_saved_to_disk(self, pipeline, temp_dir):
        """Should save reports to disk."""
        pipeline.run()
        report_files = list(Path(temp_dir).glob("day_*.json"))
        assert len(report_files) == 3

    def test_generate_summary_report(self, pipeline):
        """Should generate summary report."""
        pipeline.run()
        summary = pipeline.generate_summary_report()
        assert summary["n_days"] == 3
        assert "total_pnl" in summary
        assert "verdict_distribution" in summary

    def test_pipeline_halts_on_critical(self, temp_dir):
        """Should halt on critical issues."""
        config = SystemConfig(environment="SIMULATED", demo_only=True)
        orch = QuantLabOrchestrator(config)
        orch.initialize()

        # Force critical state
        orch.emergency_stop("test")

        pipeline = AutonomousPipeline(
            orchestrator=orch,
            verdict_engine=VerdictEngine(),
            output_dir=temp_dir,
            n_demo_days=3,
        )
        reports = pipeline.run()
        # System is HALTED, so first day returns early with NO_TRADE
        # and the loop should break after detecting CRITICAL risk level
        assert len(reports) >= 1  # At least one report generated
        assert reports[0].system_state == "HALTED"
        # Check that the report has a NO_TRADE verdict
        assert any(v.verdict == Verdict.NO_TRADE for v in reports[0].strategy_verdicts)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
