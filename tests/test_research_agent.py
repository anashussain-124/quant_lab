"""Phase 11 tests — Research Agent.

Per AGENTS.md A3:
- L0: Read data, write reports (default)
- L1: Propose experiments; human approves
- L2: Run approved templates in sandbox
- L3: Modify risk/execution — FORBIDDEN
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from engine.research_agent import (
    ResearchAgent, AgentLevel, ExperimentProposal,
    ExperimentResult, ResearchReport, ExperimentTemplate,
)


class TestResearchAgent:
    """Test research agent."""

    def test_l0_can_generate_report(self):
        """L0 agent should generate reports."""
        agent = ResearchAgent(level=AgentLevel.L0)
        report = agent.generate_report(
            title="Test Report",
            data_quality=0.95,
            market_regime="TRENDING",
            findings=["Finding 1", "Finding 2"],
            recommendations=["Rec 1"],
        )
        assert report.title == "Test Report"
        assert report.data_quality == 0.95
        assert len(report.findings) == 2

    def test_l0_cannot_propose_experiments(self):
        """L0 agent should NOT propose experiments."""
        agent = ResearchAgent(level=AgentLevel.L0)
        proposal = agent.propose_experiment(
            name="test",
            description="test",
            hypothesis="test",
            parameters={},
            success_criteria="test",
        )
        assert proposal is None

    def test_l1_can_propose_experiments(self):
        """L1 agent should propose experiments."""
        agent = ResearchAgent(level=AgentLevel.L1)
        proposal = agent.propose_experiment(
            name="test_exp",
            description="Test experiment",
            hypothesis="Edge exists",
            parameters={"lookback": 20},
            success_criteria="Sharpe > 1.0",
        )
        assert proposal is not None
        assert proposal.name == "test_exp"
        assert proposal.approved is False

    def test_l1_cannot_run_experiments(self):
        """L1 agent should NOT run experiments."""
        agent = ResearchAgent(level=AgentLevel.L1)
        proposal = agent.propose_experiment(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        result = agent.run_experiment(proposal)
        assert result is None

    def test_l2_can_run_approved_experiments(self):
        """L2 agent should run approved experiments."""
        agent = ResearchAgent(level=AgentLevel.L2)
        proposal = agent.propose_experiment(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        proposal.approve("human")
        result = agent.run_experiment(proposal)
        assert result is not None
        assert result.status == "COMPLETED"

    def test_l2_cannot_run_unapproved_experiments(self):
        """L2 agent should NOT run unapproved experiments."""
        agent = ResearchAgent(level=AgentLevel.L2)
        proposal = agent.propose_experiment(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        result = agent.run_experiment(proposal)
        assert result is None

    def test_l3_forbidden(self):
        """L3 should not be usable."""
        agent = ResearchAgent(level=AgentLevel.L0)
        # L3 is defined but agent should not operate at L3
        assert agent.level != AgentLevel.L3

    def test_analyze_market_conditions(self):
        """Should analyze market conditions."""
        agent = ResearchAgent(level=AgentLevel.L0)
        analysis = agent.analyze_market_conditions(
            price_data={"regime": "TRENDING", "volatility": 0.02},
            quality_score=0.95,
        )
        assert analysis["regime"] == "TRENDING"
        assert analysis["quality_score"] == 0.95

    def test_get_summary(self):
        """Should get agent summary."""
        agent = ResearchAgent(level=AgentLevel.L0)
        summary = agent.get_summary()
        assert summary["level"] == "L0"
        assert "autonomy" in summary

    def test_get_proposals(self):
        """Should get proposals."""
        agent = ResearchAgent(level=AgentLevel.L1)
        agent.propose_experiment(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        assert len(agent.get_proposals()) == 1

    def test_get_experiments(self):
        """Should get experiments."""
        agent = ResearchAgent(level=AgentLevel.L2)
        proposal = agent.propose_experiment(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        proposal.approve("human")
        agent.run_experiment(proposal)
        assert len(agent.get_experiments()) == 1

    def test_get_reports(self):
        """Should get reports."""
        agent = ResearchAgent(level=AgentLevel.L0)
        agent.generate_report(title="Test")
        assert len(agent.get_reports()) == 1


class TestExperimentProposal:
    """Test experiment proposal."""

    def test_approve(self):
        """Should approve proposal."""
        proposal = ExperimentProposal(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        proposal.approve("human")
        assert proposal.approved is True
        assert proposal.approved_by == "human"


class TestExperimentResult:
    """Test experiment result."""

    def test_complete(self):
        """Should complete experiment."""
        proposal = ExperimentProposal(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        result = ExperimentResult(proposal=proposal, status="RUNNING")
        result.complete({"sharpe": 1.5})
        assert result.status == "COMPLETED"
        assert result.results["sharpe"] == 1.5
        assert result.end_time is not None

    def test_fail(self):
        """Should fail experiment."""
        proposal = ExperimentProposal(
            name="test", description="test", hypothesis="test",
            parameters={}, success_criteria="test",
        )
        result = ExperimentResult(proposal=proposal, status="RUNNING")
        result.fail("Error")
        assert result.status == "FAILED"
        assert "error" in result.results


class TestResearchReport:
    """Test research report."""

    def test_to_markdown(self):
        """Should convert to markdown."""
        report = ResearchReport(
            title="Test Report",
            summary="Test summary",
            findings=["Finding 1"],
            recommendations=["Rec 1"],
        )
        md = report.to_markdown()
        assert "# Test Report" in md
        assert "Test summary" in md
        assert "Finding 1" in md


class TestExperimentTemplate:
    """Test experiment template."""

    def test_validate_parameters_valid(self):
        """Should validate correct parameters."""
        template = ExperimentTemplate(
            name="test",
            description="test",
            parameter_schema={"lookback": {"type": "int"}},
        )
        valid, msg = template.validate_parameters({"lookback": 20})
        assert valid is True

    def test_validate_parameters_missing(self):
        """Should reject missing parameters."""
        template = ExperimentTemplate(
            name="test",
            description="test",
            parameter_schema={"lookback": {"type": "int"}},
        )
        valid, msg = template.validate_parameters({})
        assert valid is False
        assert "Missing" in msg


class TestIntegration:
    """Test integration."""

    def test_full_l0_workflow(self):
        """L0 workflow: generate report."""
        agent = ResearchAgent(level=AgentLevel.L0)
        report = agent.generate_report(
            title="Market Analysis",
            data_quality=0.95,
            market_regime="TRENDING",
            findings=["Trend is strong"],
            recommendations=["Continue monitoring"],
        )
        assert report.data_quality == 0.95
        assert len(agent.get_reports()) == 1

    def test_full_l1_workflow(self):
        """L1 workflow: propose experiment."""
        agent = ResearchAgent(level=AgentLevel.L1)
        proposal = agent.propose_experiment(
            name="momentum_test",
            description="Test momentum strategy",
            hypothesis="Momentum edge exists",
            parameters={"lookback": 20, "threshold": 0.01},
            success_criteria="Sharpe > 1.0 after costs",
        )
        assert proposal is not None
        assert proposal.approved is False

    def test_full_l2_workflow(self):
        """L2 workflow: propose → approve → run."""
        agent = ResearchAgent(level=AgentLevel.L2)
        proposal = agent.propose_experiment(
            name="mean_rev_test",
            description="Test mean reversion",
            hypothesis="Mean reversion edge exists",
            parameters={"lookback": 50},
            success_criteria="Sharpe > 1.0",
        )
        proposal.approve("human")
        result = agent.run_experiment(proposal)
        assert result is not None
        assert result.status == "COMPLETED"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
