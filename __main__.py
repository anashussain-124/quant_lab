"""Main entry point for the Quant Research Lab.

Usage:
    python -m quant_lab run-demo --days 7
    python -m quant_lab run-backtest --signals signals.json
    python -m quant_lab status
    python -m quant_lab report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine.orchestrator import QuantLabOrchestrator, SystemConfig, SystemState
from engine.pipeline import AutonomousPipeline, VerdictEngine
from engine.research_agent import AgentLevel


def cmd_run_demo(args: argparse.Namespace) -> int:
    """Run demo pipeline for N days."""
    config = SystemConfig(
        environment="SIMULATED",
        demo_only=True,
        autonomy_level=AgentLevel.L0,
    )
    orch = QuantLabOrchestrator(config)
    if not orch.initialize():
        print("System initialization failed", file=sys.stderr)
        return 1

    pipeline = AutonomousPipeline(
        orchestrator=orch,
        verdict_engine=VerdictEngine(),
        output_dir="reports",
        n_demo_days=args.days,
    )

    reports = pipeline.run()
    summary = pipeline.generate_summary_report()

    print(f"\n{'='*60}")
    print(f"  DEMO PIPELINE COMPLETE — {len(reports)} days")
    print(f"{'='*60}")
    print(f"  Total P&L: ${summary.get('total_pnl', 0):.2f}")
    print(f"  Total Trades: {summary.get('total_trades', 0)}")
    print(f"  Avg Sharpe: {summary.get('avg_sharpe', 0):.3f}")
    print(f"  Max Drawdown: {summary.get('max_drawdown', 0):.2%}")
    print(f"  Verdicts: {summary.get('verdict_distribution', {})}")
    print(f"{'='*60}\n")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Get system status."""
    config = SystemConfig(environment="SIMULATED", demo_only=True)
    orch = QuantLabOrchestrator(config)
    orch.initialize()

    status = orch.get_status()
    print(json.dumps(status, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate system report."""
    config = SystemConfig(environment="SIMULATED", demo_only=True)
    orch = QuantLabOrchestrator(config)
    orch.initialize()

    report = orch.generate_report("System Report")
    print(report.to_markdown())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant Research Lab")
    subparsers = parser.add_subparsers(dest="command")

    # run-demo
    demo_parser = subparsers.add_parser("run-demo", help="Run demo pipeline")
    demo_parser.add_argument("--days", type=int, default=7, help="Number of demo days")

    # status
    subparsers.add_parser("status", help="Get system status")

    # report
    subparsers.add_parser("report", help="Generate system report")

    args = parser.parse_args()

    if args.command == "run-demo":
        return cmd_run_demo(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "report":
        return cmd_report(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
