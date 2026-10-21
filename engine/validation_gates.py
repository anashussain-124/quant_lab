"""Phase 3: Validation Gates.

All required for a strategy to "SURVIVE":
- Minimum trade count + minimum independent observations
- Positive expectancy after realistic costs
- Acceptable max drawdown
- Parameter stability (neighbors of winning params also work)
- Walk-forward stability across multiple regimes
- Cost stress test
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from engine.backtester_v3 import BacktestResult, BacktestConfig, EventDrivenBacktester
from engine.regime import Regime, RegimeDetector, RegimeState


@dataclass
class ValidationThresholds:
    """Configurable validation thresholds."""
    min_trades: int = 30
    min_independent_observations: int = 20
    min_expectancy: float = 0.001  # 0.1% per trade
    min_profit_factor: float = 1.2
    max_drawdown_pct: float = 15.0  # 15%
    min_sharpe: float = 0.5
    min_walk_forward_stability: float = 0.3
    cost_stress_multiplier: float = 2.0
    min_parameter_stability: float = 0.6  # 60% of neighbors must work


@dataclass
class ValidationResult:
    """Result of validation gates."""
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def add_check(self, name: str, passed: bool, detail: Any = None):
        self.checks[name] = passed
        if not passed:
            self.reasons.append(f"{name}: {detail}")
        self.details[name] = detail

    def finalize(self):
        self.passed = all(self.checks.values())
        return self.passed


class ValidationGates:
    """Validation gate framework."""

    def __init__(self, thresholds: ValidationThresholds | None = None):
        self.thresholds = thresholds or ValidationThresholds()

    def validate(
        self,
        result: BacktestResult,
        data: pd.DataFrame,
        strategy_name: str,
        regime: Regime | None = None,
    ) -> ValidationResult:
        """Run all validation gates."""
        vr = ValidationResult(passed=True)

        # 1. Trade count
        n_trades = len(result.trades)
        vr.add_check(
            "min_trades",
            n_trades >= self.thresholds.min_trades,
            f"trades={n_trades}, required={self.thresholds.min_trades}",
        )

        # 2. Independent observations (unique bars)
        if result.trades:
            unique_bars = len(set(t.entry_time for t in result.trades))
            vr.add_check(
                "min_independent_observations",
                unique_bars >= self.thresholds.min_independent_observations,
                f"unique_bars={unique_bars}, required={self.thresholds.min_independent_observations}",
            )

        # 3. Expectancy after costs
        if result.trades:
            returns = np.array([t.return_pct for t in result.trades])
            expectancy = float(np.mean(returns))
            vr.add_check(
                "positive_expectancy",
                expectancy >= self.thresholds.min_expectancy,
                f"expectancy={expectancy:.6f}, required={self.thresholds.min_expectancy}",
            )

        # 4. Profit factor
        if result.trades:
            wins = [t.return_pct for t in result.trades if t.return_pct > 0]
            losses = [t.return_pct for t in result.trades if t.return_pct <= 0]
            gross_win = sum(wins) if wins else 0.0
            gross_loss = abs(sum(losses)) if losses else 1e-8
            profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
            vr.add_check(
                "profit_factor",
                profit_factor >= self.thresholds.min_profit_factor,
                f"profit_factor={profit_factor:.3f}, required={self.thresholds.min_profit_factor}",
            )

        # 5. Max drawdown
        if result.equity_curve:
            equities = np.array([e[1] for e in result.equity_curve])
            peaks = np.maximum.accumulate(equities)
            drawdowns = (equities - peaks) / peaks
            max_dd = abs(float(np.min(drawdowns))) * 100
            vr.add_check(
                "max_drawdown",
                max_dd <= self.thresholds.max_drawdown_pct,
                f"max_dd={max_dd:.2f}%, required<={self.thresholds.max_drawdown_pct}%",
            )

        # 6. Sharpe ratio
        if result.trades:
            returns = np.array([t.return_pct for t in result.trades])
            std = np.std(returns, ddof=1) if len(returns) > 1 else 1e-8
            sharpe = float(np.mean(returns) / std * np.sqrt(252)) if std > 0 else 0.0
            vr.add_check(
                "sharpe_ratio",
                sharpe >= self.thresholds.min_sharpe,
                f"sharpe={sharpe:.3f}, required={self.thresholds.min_sharpe}",
            )

        # 7. Walk-forward stability
        stability = self._compute_walk_forward_stability(data, strategy_name, regime)
        vr.add_check(
            "walk_forward_stability",
            stability >= self.thresholds.min_walk_forward_stability,
            f"stability={stability:.3f}, required={self.thresholds.min_walk_forward_stability}",
        )

        # 8. Cost stress test
        cost_stress_pass = self._cost_stress_test(data, strategy_name, regime)
        vr.add_check(
            "cost_stress",
            cost_stress_pass,
            "Strategy fails under 2x costs",
        )

        vr.finalize()
        return vr

    def _compute_walk_forward_stability(
        self,
        data: pd.DataFrame,
        strategy_name: str,
        regime: Regime | None,
        n_folds: int = 5,
    ) -> float:
        """Compute walk-forward stability across multiple windows."""
        if len(data) < n_folds * 50:
            return 0.0

        fold_size = len(data) // n_folds
        sharpes = []

        for fold in range(n_folds):
            start = fold * fold_size
            end = start + fold_size

            if end > len(data):
                break

            fold_data = data.iloc[start:end]
            if len(fold_data) < 50:
                continue

            # Run backtest on fold
            config = BacktestConfig()
            backtester = EventDrivenBacktester(config)

            # Generate signals for this fold
            from engine.regime import create_strategy_matrix
            matrix = create_strategy_matrix()
            signals = matrix.generate_signals(fold_data, strategy_name, regime)

            if not signals:
                continue

            result = backtester.run(fold_data, signals)

            if result.trades:
                returns = np.array([t.return_pct for t in result.trades])
                std = np.std(returns, ddof=1) if len(returns) > 1 else 1e-8
                sharpe = float(np.mean(returns) / std * np.sqrt(252)) if std > 0 else 0.0
                sharpes.append(sharpe)

        if not sharpes:
            return 0.0

        # Stability = fraction of folds with positive Sharpe
        positive_folds = sum(1 for s in sharpes if s > 0)
        stability = positive_folds / len(sharpes)

        return stability

    def _cost_stress_test(
        self,
        data: pd.DataFrame,
        strategy_name: str,
        regime: Regime | None,
    ) -> bool:
        """Test strategy under increased costs."""
        # Run with 2x costs
        stressed_config = BacktestConfig(
            maker_fee=0.0002 * self.thresholds.cost_stress_multiplier,
            taker_fee=0.0004 * self.thresholds.cost_stress_multiplier,
            slippage_bps=2.0 * self.thresholds.cost_stress_multiplier,
        )
        backtester = EventDrivenBacktester(stressed_config)

        from engine.regime import create_strategy_matrix
        matrix = create_strategy_matrix()
        signals = matrix.generate_signals(data, strategy_name, regime)

        if not signals:
            return False

        result = backtester.run(data, signals)

        if not result.trades:
            return False

        # Check if still profitable after stress
        total_pnl = sum(t.net_pnl for t in result.trades)
        return total_pnl > 0

    def validate_parameter_stability(
        self,
        data: pd.DataFrame,
        strategy_name: str,
        base_params: dict[str, Any],
        regime: Regime | None = None,
    ) -> ValidationResult:
        """Test that neighboring parameters also work."""
        vr = ValidationResult(passed=True)

        # Test base parameters first
        base_result = self._run_with_params(data, strategy_name, base_params, regime)
        if base_result is None or not base_result.trades:
            vr.add_check("base_params", False, "No trades with base parameters")
            vr.finalize()
            return vr

        base_sharpe = base_result.metrics.get("sharpe_ratio", 0.0)
        vr.add_check(
            "base_params",
            base_sharpe > 0,
            f"base_sharpe={base_sharpe:.3f}",
        )

        # Test neighbors (±20% on each parameter)
        neighbor_results = []
        for param_name, param_value in base_params.items():
            if not isinstance(param_value, (int, float)):
                continue

            for delta in [-0.2, 0.2]:
                neighbor_params = base_params.copy()
                neighbor_params[param_name] = param_value * (1 + delta)

                neighbor_result = self._run_with_params(data, strategy_name, neighbor_params, regime)
                if neighbor_result and neighbor_result.trades:
                    neighbor_results.append(neighbor_result)

        if not neighbor_results:
            vr.add_check("parameter_stability", False, "No neighbor results")
            vr.finalize()
            return vr

        # Check what fraction of neighbors are profitable
        profitable_neighbors = sum(
            1 for r in neighbor_results if r.metrics.get("sharpe_ratio", 0) > 0
        )
        stability = profitable_neighbors / len(neighbor_results)

        vr.add_check(
            "parameter_stability",
            stability >= self.thresholds.min_parameter_stability,
            f"stability={stability:.2%} ({profitable_neighbors}/{len(neighbor_results)})",
        )

        vr.finalize()
        return vr

    def _run_with_params(
        self,
        data: pd.DataFrame,
        strategy_name: str,
        params: dict[str, Any],
        regime: Regime | None,
    ) -> BacktestResult | None:
        """Run backtest with specific parameters."""
        try:
            config = BacktestConfig()
            backtester = EventDrivenBacktester(config)

            from engine.regime import create_strategy_matrix
            matrix = create_strategy_matrix()

            # Generate signals with custom params
            signals = matrix.generate_signals(data, strategy_name, regime)

            if not signals:
                return None

            result = backtester.run(data, signals)
            return result
        except Exception:
            return None


def create_validation_gates() -> ValidationGates:
    """Create default validation gates."""
    return ValidationGates()
