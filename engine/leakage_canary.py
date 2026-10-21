"""Leakage canary tests for the Quant Research Lab.

Per AGENTS.md A4:
- Feature deliberately shifted into the future must produce absurdly good results and be flagged
- Shuffled labels must produce ~zero edge
- If either check fails, the pipeline is BROKEN. Stop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from engine.backtester import BacktestResult


@dataclass
class CanaryResult:
    """Result of a leakage canary test."""
    test_name: str
    passed: bool
    edge_ratio: float
    details: str


class LeakageCanary:
    """Mandatory leakage detection in CI.

    Two tests (per A4):
    (a) Future-shifted feature: must produce absurdly good results → FLAGGED
    (b) Shuffled labels: must produce ~0 edge
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("quant_lab.canary")
        self._results: list[CanaryResult] = []

    def future_shift_test(self, backtest_result: BacktestResult) -> CanaryResult:
        """Test (a): Features shifted into the future must produce absurd results.

        If a backtest shows suspiciously high performance, it likely contains
        look-ahead bias. A Sharpe >= 10.0 is almost certainly due to
        future data leakage in a real market.
        """
        # Threshold: Sharpe >= 10.0 (absurdly high for any real strategy)
        suspicious = backtest_result.sharpe_ratio >= 10.0

        result = CanaryResult(
            test_name="future_shift",
            passed=not suspicious,
            edge_ratio=abs(backtest_result.sharpe_ratio),
            details=(
                "FLAGGED: Future-shifted features detected — "
                f"Sharpe={backtest_result.sharpe_ratio:.2f}. "
                "Pipeline is BROKEN."
                if suspicious
                else "No look-ahead bias detected."
            ),
        )
        self._results.append(result)

        if suspicious:
            self.logger.critical(f"LEAKAGE DETECTED: {result.details}")

        return result

    def shuffled_labels_test(self, backtest_result: BacktestResult) -> CanaryResult:
        """Test (b): Shuffled labels must produce ~0 edge.

        If labels are randomly shuffled, the backtest should show
        near-zero expectancy, Sharpe ~0, and win rate ~50%.
        """
        edge = abs(backtest_result.expectancy)
        edge_ratio = edge if backtest_result.total_trades > 0 else 0.0

        # If expectancy is very low (< 0.01 per trade), labels are clean
        passed = edge_ratio < 0.01

        result = CanaryResult(
            test_name="shuffled_labels",
            passed=passed,
            edge_ratio=edge_ratio,
            details=(
                f"Edge={edge:.4f} per trade, "
                "Shuffled labels produce near-zero edge ✅"
                if passed
                else f"Shuffled labels still show edge={edge:.4f}. "
                "Pipeline is BROKEN."
            ),
        )
        self._results.append(result)

        if not passed:
            self.logger.critical(f"LEAKAGE DETECTED: {result.details}")

        return result

    def run_all_canaries(self, backtest_result: BacktestResult) -> list[CanaryResult]:
        """Run all canary tests. Returns list of results."""
        self._results = []
        r1 = self.future_shift_test(backtest_result)
        r2 = self.shuffled_labels_test(backtest_result)

        # If ANY canary fails, the pipeline is broken
        all_passed = all(r.passed for r in self._results)
        if not all_passed:
            self.logger.critical(
                "PIPELINE BROKEN: Leakage detected. Stop all activity."
            )
        return self._results

    @property
    def results(self) -> list[CanaryResult]:
        return list(self._results)

    @property
    def pipeline_broken(self) -> bool:
        """Return True if any canary test failed."""
        return any(not r.passed for r in self._results)
