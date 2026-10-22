"""Research runner — executes experiments with real data.

Per master prompt: hypothesis-first research with train/val/test separation,
walk-forward validation, DSR/PSR/PBO, and robustness testing.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.validation import ValidationFramework, WalkForwardResult
from engine.backtester_v2 import EventDrivenBacktester, OrderSide, CostModel
from engine.safety_gate import RiskContext
from engine.logger import AuditLogger
from data.pipeline import DataPipeline, DataSource
from libs.quant_math import (
    sharpe_ratio, sortino_ratio, position_size,
    hurst_exponent, realized_volatility,
)
from strategies import BaseStrategy, SignalAction, get_strategy

EXECUTION_MODES = frozenset({"legacy_same_close", "causal_next_open"})


@dataclass
class ExperimentConfig:
    """Configuration for a research experiment."""
    experiment_id: str
    hypothesis: str
    strategy_name: str
    strategy_version: str = "v001"
    symbol: str = ""
    timeframe: str = "1h"
    train_start: str = ""
    train_end: str = ""
    val_start: str = ""
    val_end: str = ""
    test_start: str = ""
    test_end: str = ""
    cost_model: dict[str, float] = field(default_factory=lambda: {
        "maker_fee": 0.0002,
        "taker_fee": 0.0004,
        "slippage_bps": 2.0,
        "spread_bps": 1.0,
    })
    parameters: dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "legacy_same_close"


@dataclass
class ExperimentResult:
    """Complete experiment result."""
    config: ExperimentConfig
    train_metrics: dict[str, float] = field(default_factory=dict)
    val_metrics: dict[str, float] = field(default_factory=dict)
    test_metrics: dict[str, float] = field(default_factory=dict)
    walk_forward: dict[str, float] = field(default_factory=dict)
    validation_report: dict[str, float] = field(default_factory=dict)
    robustness: dict[str, float] = field(default_factory=dict)
    verdict: str = ""
    reasons: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.config.experiment_id,
            "hypothesis": self.config.hypothesis,
            "strategy": self.config.strategy_name,
            "version": self.config.strategy_version,
            "symbol": self.config.symbol,
            "timeframe": self.config.timeframe,
            "train_range": f"{self.config.train_start} to {self.config.train_end}",
            "val_range": f"{self.config.val_start} to {self.config.val_end}",
            "test_range": f"{self.config.test_start} to {self.config.test_end}",
            "parameters": self.config.parameters,
            "execution_mode": self.config.execution_mode,
            "train_metrics": self.train_metrics,
            "val_metrics": self.val_metrics,
            "test_metrics": self.test_metrics,
            "walk_forward": self.walk_forward,
            "validation": self.validation_report,
            "robustness": self.robustness,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "timestamp": self.timestamp,
        }


class ResearchRunner:
    """Runs research experiments with proper train/val/test separation."""

    def __init__(self, data: pd.DataFrame):
        self.data = data
        self.validation = ValidationFramework()
        self.backtester = EventDrivenBacktester()
        self.cost_model = CostModel()
        self.audit = AuditLogger()
        self.results: list[ExperimentResult] = []

    def _split_data(self, config: ExperimentConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Split data into train/val/test chronologically."""
        train = self.data[
            (self.data["timestamp"] >= config.train_start) &
            (self.data["timestamp"] < config.train_end)
        ].copy()

        val = self.data[
            (self.data["timestamp"] >= config.val_start) &
            (self.data["timestamp"] < config.val_end)
        ].copy()

        test = self.data[
            (self.data["timestamp"] >= config.test_start) &
            (self.data["timestamp"] < config.test_end)
        ].copy()

        return train, val, test

    def _compute_metrics(self, returns: np.ndarray) -> dict[str, float]:
        """Compute comprehensive performance metrics."""
        if len(returns) == 0:
            return {"error": "No returns"}

        total = float(np.sum(returns))
        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 1e-8

        wins = returns[returns > 0]
        losses = returns[returns <= 0]

        win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.0
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

        # Profit factor
        gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 1e-8
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        # Expectancy
        expectancy = mean

        # Max drawdown
        cumulative = np.cumsum(returns)
        peak = np.maximum.accumulate(cumulative)
        drawdown = cumulative - peak
        max_dd = float(abs(min(drawdown))) if len(drawdown) > 0 else 0.0

        # Consecutive losses
        max_consec_loss = 0
        current_loss = 0
        for r in returns:
            if r <= 0:
                current_loss += 1
                max_consec_loss = max(max_consec_loss, current_loss)
            else:
                current_loss = 0

        return {
            "total_pnl": total,
            "mean_return": mean,
            "std_return": std,
            "sharpe": float(sharpe_ratio(returns.tolist())) if len(returns) > 1 else 0.0,
            "sortino": float(sortino_ratio(returns.tolist())) if len(returns) > 1 else 0.0,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "max_drawdown": max_dd,
            "n_trades": len(returns),
            "max_consecutive_losses": max_consec_loss,
        }

    def _run_backtest_on_data(
        self, data: pd.DataFrame, strategy: BaseStrategy, cost_model: CostModel
    ) -> np.ndarray:
        """Run backtest with proper position tracking."""
        if len(data) < 50:
            return np.array([])

        returns = []
        position = 0  # 0=none, 1=long, -1=short
        entry_price = 0.0
        entry_bar = 0

        for i in range(50, len(data)):
            window = data.iloc[:i+1]
            signal = strategy.generate_signal(window)

            price = float(data.iloc[i]["close"])

            if signal.action == SignalAction.TAKE:
                if position == 0:
                    # Open new position
                    position = 1 if signal.side == "BUY" else -1
                    entry_price = price
                    entry_bar = i
                elif (signal.side == "BUY" and position == -1) or (signal.side == "SELL" and position == 1):
                    # Close existing position
                    if position == 1:
                        ret = (price - entry_price) / entry_price
                    else:
                        ret = (entry_price - price) / entry_price

                    # Subtract costs
                    fee = cost_model.taker_fee * 2
                    slippage = cost_model.expected_slippage_bps / 10000 * 2
                    ret -= fee + slippage

                    returns.append(ret)

                    # Open new position in opposite direction
                    position = 1 if signal.side == "BUY" else -1
                    entry_price = price
                    entry_bar = i

            # Exit logic: close after N bars
            if position != 0 and i - entry_bar > 20:
                if position == 1:
                    ret = (price - entry_price) / entry_price
                else:
                    ret = (entry_price - price) / entry_price

                fee = cost_model.taker_fee * 2
                slippage = cost_model.expected_slippage_bps / 10000 * 2
                ret -= fee + slippage

                returns.append(ret)
                position = 0
                entry_price = 0.0

        return np.array(returns)

    def _run_causal_backtest_on_data(
        self, data: pd.DataFrame, strategy: BaseStrategy, cost_model: CostModel
    ) -> np.ndarray:
        """Run signals at bar close and fill at the next bar open.

        This is deliberately separate from the historical same-close method so
        old experiment results remain reproducible while new runs cannot use a
        price that was only known after the signal bar closed.
        """
        required = {"open", "close", "high", "low"}
        if len(data) < 50 or not required.issubset(data.columns):
            return np.array([])
        frame = data.reset_index(drop=True)
        returns: list[float] = []
        position = 0
        entry_price = 0.0
        entry_bar = 0

        def execution_price(raw: float, side: int) -> float:
            half_spread = cost_model.spread_bps / 20000
            slip = cost_model.expected_slippage_bps / 10000
            return raw * (1 + side * (half_spread + slip))

        def close_position(raw: float, side: int) -> None:
            nonlocal position, entry_price
            exit_price = execution_price(raw, -position)
            gross = (
                (exit_price - entry_price) / entry_price
                if position == 1
                else (entry_price - exit_price) / entry_price
            )
            round_trip_cost = cost_model.taker_fee * 2
            returns.append(gross - round_trip_cost)
            position = 0
            entry_price = 0.0

        for i in range(50, len(frame) - 1):
            signal = strategy.generate_signal(frame.iloc[: i + 1])
            next_open = float(frame.iloc[i + 1]["open"])
            side = 1 if signal.side == "BUY" else -1
            if signal.action == SignalAction.TAKE:
                if position == 0:
                    position = side
                    entry_price = execution_price(next_open, position)
                    entry_bar = i + 1
                elif side != position:
                    close_position(next_open, side)
                    position = side
                    entry_price = execution_price(next_open, position)
                    entry_bar = i + 1
            if position and i + 1 - entry_bar >= 20:
                close_position(next_open, side)

        if position:
            close_position(float(frame.iloc[-1]["close"]), side)
        return np.array(returns)

    def run_experiment(self, config: ExperimentConfig) -> ExperimentResult:
        """Run a complete research experiment."""
        if config.execution_mode not in EXECUTION_MODES:
            raise ValueError(
                f"execution_mode must be one of {sorted(EXECUTION_MODES)}, "
                f"got {config.execution_mode!r}"
            )
        result = ExperimentResult(config=config)

        try:
            # Split data
            train, val, test = self._split_data(config)

            if len(train) < 100 or len(val) < 50 or len(test) < 50:
                result.verdict = "REJECT"
                result.reasons.append(f"Insufficient data: train={len(train)}, val={len(val)}, test={len(test)}")
                return result

            # Get strategy
            strategy = get_strategy(config.strategy_name)
            if strategy is None:
                result.verdict = "REJECT"
                result.reasons.append(f"Strategy not found: {config.strategy_name}")
                return result

            # Run backtest on each split
            runner = (self._run_causal_backtest_on_data
                      if config.execution_mode == "causal_next_open"
                      else self._run_backtest_on_data)
            train_returns = runner(train, strategy, self.cost_model)
            val_returns = runner(val, strategy, self.cost_model)
            test_returns = runner(test, strategy, self.cost_model)

            result.train_metrics = self._compute_metrics(train_returns)
            result.val_metrics = self._compute_metrics(val_returns)
            result.test_metrics = self._compute_metrics(test_returns)

            # Walk-forward validation
            wf_returns = np.concatenate([train_returns, val_returns, test_returns])
            wf_result = self.validation.walk_forward.validate(wf_returns, config.strategy_name)
            result.walk_forward = {
                "folds": wf_result.folds,
                "is_sharpe": wf_result.in_sample_sharpe,
                "oos_sharpe": wf_result.out_of_sample_sharpe,
                "stability": wf_result.stability_score,
                "all_passed": wf_result.all_passed,
            }

            # Full validation report
            val_report = self.validation.validate(wf_returns, config.strategy_name)
            result.validation_report = {
                "dsr": val_report.dsr,
                "psr": val_report.psr,
                "pbo": val_report.pbo,
                "passed": val_report.validation_passed,
            }

            # Robustness: cost stress
            stressed_cost = CostModel(
                maker_fee=self.cost_model.maker_fee * 2,
                taker_fee=self.cost_model.taker_fee * 2,
                expected_slippage_bps=self.cost_model.expected_slippage_bps * 2,
            )
            stressed_returns = runner(test, strategy, stressed_cost)
            stressed_metrics = self._compute_metrics(stressed_returns)
            result.robustness = {
                "cost_stress_pnl": stressed_metrics.get("total_pnl", 0),
                "cost_stress_sharpe": stressed_metrics.get("sharpe", 0),
                "cost_stress_drawdown": stressed_metrics.get("max_drawdown", 0),
            }

            # Verdict
            result.verdict, result.reasons = self._issue_verdict(result)

        except Exception as e:
            result.verdict = "REJECT"
            result.reasons.append(f"Error: {str(e)}")

        self.results.append(result)
        return result

    def _issue_verdict(self, result: ExperimentResult) -> tuple[str, list[str]]:
        """Issue evidence-based verdict."""
        reasons = []
        test_metrics = result.test_metrics
        validation = result.validation_report
        robustness = result.robustness

        # Check trade count
        n_trades = test_metrics.get("n_trades", 0)
        if n_trades < 10:
            reasons.append(f"Insufficient trades in test: {n_trades}")
            return "REJECT", reasons

        # Check Sharpe
        sharpe = test_metrics.get("sharpe", 0)
        if sharpe < 0:
            reasons.append(f"Negative Sharpe: {sharpe:.3f}")
            return "REJECT", reasons

        # Check max drawdown
        max_dd = test_metrics.get("max_drawdown", 0)
        if max_dd > 0.20:
            reasons.append(f"Excessive drawdown: {max_dd:.1%}")
            return "REJECT", reasons

        # Check validation
        if not validation.get("passed", False):
            reasons.append(f"Validation failed: DSR={validation.get('dsr', 0):.3f}, PSR={validation.get('psr', 0):.3f}")

        # Check cost stress
        stress_pnl = robustness.get("cost_stress_pnl", 0)
        base_pnl = test_metrics.get("total_pnl", 0)
        if base_pnl > 0 and stress_pnl < 0:
            reasons.append(f"Cost stress turns profit negative: {base_pnl:.3f} -> {stress_pnl:.3f}")

        # Check walk-forward stability
        stability = result.walk_forward.get("stability", 0)
        if stability < 0.5:
            reasons.append(f"Low walk-forward stability: {stability:.3f}")

        # Determine verdict
        if len(reasons) == 0 and sharpe > 0.5 and test_metrics.get("win_rate", 0) > 0.45:
            return "PROMISING", ["All metrics within acceptable ranges"]
        elif sharpe > 0:
            return "REVIEW", reasons
        else:
            return "REJECT", reasons


class ResearchDatabase:
    """Persistent research inventory."""

    def __init__(self, path: str = "data/research_db.json"):
        self.path = Path(path)
        self.experiments: list[dict[str, Any]] = []
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path) as f:
                self.experiments = json.load(f)

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.experiments, f, indent=2)

    def add_experiment(self, result: ExperimentResult):
        self.experiments.append(result.to_dict())
        self.save()

    def get_history(self) -> list[dict[str, Any]]:
        return self.experiments

    def summary(self) -> dict[str, Any]:
        """Research inventory summary."""
        total = len(self.experiments)
        verdicts = {}
        for exp in self.experiments:
            v = exp.get("verdict", "UNKNOWN")
            verdicts[v] = verdicts.get(v, 0) + 1

        return {
            "total_experiments": total,
            "verdict_distribution": verdicts,
            "recent_experiments": self.experiments[-5:] if self.experiments else [],
        }
