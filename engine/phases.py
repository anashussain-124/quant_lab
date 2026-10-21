"""Phase 8-12: Strategies, Regimes, Monitoring, Research Agent.

Per AGENTS.md Part E:
- Regime detection: trending/mean-reverting/ranging via Hurst exponent
- Strategy factory with parameter grid
- Research agent loop with go/no-go decision
- Monitoring: metrics, alerts, logs
- Go/no-go gate: all checks must pass before live trading
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from engine.validation import ValidationFramework, ValidationReport
from engine.crypto_arb import ArbitrageOpportunity, ArbitrageStatus
from libs.quant_math import (
    hurst_exponent, hurst_to_regime, realized_volatility,
    sharpe_ratio, sortino_ratio, calmar_ratio, information_ratio,
    position_size, kelly_criterion,
)


class MarketRegime(str, Enum):
    """Market regimes."""
    TRENDING = "TRENDING"
    MEAN_REVERTING = "MEAN_REVERTING"
    RANDOM_WALK = "RANDOM_WALK"
    RANGING = "RANGING"


class StrategyType(str, Enum):
    """Strategy types."""
    MOMENTUM_BREAKOUT = "MOMENTUM_BREAKOUT"
    MEAN_REVERSION = "MEAN_REVERSION"
    ARBITRAGE = "ARBITRAGE"
    GRID = "GRID"
    DCA = "DCA"


class MonitoringLevel(str, Enum):
    """Monitoring alert levels."""
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"
    CRITICAL = "CRITICAL"


@dataclass
class RegimeSignal:
    """Market regime detection result."""
    regime: MarketRegime
    hurst: float
    volatility: float
    confidence: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class StrategyConfig:
    """Strategy configuration."""
    name: str
    strategy_type: StrategyType
    parameters: dict[str, float]
    enabled: bool = True
    max_position_size: float = 1.0
    daily_loss_limit: float = 500.0
    risk_fraction: float = 0.01


@dataclass
class StrategyResult:
    """Strategy execution result."""
    name: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float | None = None
    pnl: float = 0.0
    duration_seconds: float = 0.0
    status: str = "OPEN"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MonitoringAlert:
    """Monitoring alert."""
    level: MonitoringLevel
    message: str
    component: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoNoGoResult:
    """Go/No-Go decision."""
    decision: str  # "GO" or "NO_GO"
    confidence: float  # 0-1
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RegimeDetector:
    """Detect market regime using Hurst exponent."""

    def __init__(self, lookback: int = 100) -> None:
        self.lookback = lookback

    def detect(self, prices: pd.Series) -> RegimeSignal:
        """Detect market regime from price series."""
        if len(prices) < self.lookback:
            return RegimeSignal(
                regime=MarketRegime.RANDOM_WALK,
                hurst=0.5,
                volatility=0.0,
                confidence=0.0,
            )

        returns = prices.pct_change().dropna().values[-self.lookback:]

        # Compute Hurst exponent
        hurst = hurst_exponent(returns)
        regime = hurst_to_regime(hurst)

        # Compute volatility
        vol = np.std(returns) * np.sqrt(252)

        # Confidence based on Hurst deviation from 0.5
        confidence = min(1.0, abs(hurst - 0.5) * 5)

        return RegimeSignal(
            regime=MarketRegime(regime),
            hurst=float(hurst),
            volatility=float(vol),
            confidence=float(confidence),
        )


class StrategyFactory:
    """Strategy factory with parameter grid."""

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyConfig] = {}
        self._results: list[StrategyResult] = []

    def register(
        self,
        name: str,
        strategy_type: StrategyType,
        parameters: dict[str, float],
    ) -> StrategyConfig:
        """Register a strategy."""
        config = StrategyConfig(
            name=name,
            strategy_type=strategy_type,
            parameters=parameters,
        )
        self._strategies[name] = config
        return config

    def get(self, name: str) -> StrategyConfig | None:
        """Get strategy config."""
        return self._strategies.get(name)

    def list_strategies(self) -> list[StrategyConfig]:
        """List all registered strategies."""
        return list(self._strategies.values())

    def execute(
        self,
        name: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float | None = None,
    ) -> StrategyResult:
        """Execute a strategy."""
        config = self._strategies.get(name)
        if not config:
            raise ValueError(f"Strategy {name} not found")

        pnl = 0.0
        if exit_price is not None:
            if side == "BUY":
                pnl = (exit_price - entry_price) * config.max_position_size
            else:
                pnl = (entry_price - exit_price) * config.max_position_size

        result = StrategyResult(
            name=name,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            status="CLOSED" if exit_price else "OPEN",
        )
        self._results.append(result)
        return result

    def get_pnl(self) -> dict[str, float]:
        """Get total P&L across all strategies."""
        total = sum(r.pnl for r in self._results)
        return {
            "total_pnl": total,
            "n_trades": len(self._results),
            "n_closed": sum(1 for r in self._results if r.status == "CLOSED"),
            "n_open": sum(1 for r in self._results if r.status == "OPEN"),
        }


class ResearchAgent:
    """Research agent loop with go/no-go decision.

    Per AGENTS.md Part E:
    - Autonomous research loop
    - Go/no-go gate before live trading
    - All checks must pass
    """

    def __init__(
        self,
        validation_framework: ValidationFramework,
        regime_detector: RegimeDetector,
        strategy_factory: StrategyFactory,
    ) -> None:
        self.validation = validation_framework
        self.regime_detector = regime_detector
        self.strategy_factory = strategy_factory
        self._alerts: list[MonitoringAlert] = []
        self._iterations = 0

    def run_iteration(self, price_data: pd.DataFrame) -> GoNoGoResult:
        """Run one research iteration.

        Returns go/no-go decision based on all checks.
        """
        self._iterations += 1
        checks_passed: list[str] = []
        checks_failed: list[str] = []
        reasons: list[str] = []

        # Handle both DataFrame and Series input
        if isinstance(price_data, pd.Series):
            price_data = price_data.to_frame("price")

        # 1. Detect regime
        symbol = price_data.columns[0] if len(price_data.columns) > 0 else "BTCUSDT"
        prices = price_data[symbol]
        regime_signal = self.regime_detector.detect(prices)
        checks_passed.append(f"REGIME_{regime_signal.regime.value}")

        # 2. Validate strategy
        validation_report = self.validation.validate(
            prices.pct_change().dropna().values,
            strategy_name=f"strategy_{self._iterations}",
        )

        if validation_report.validation_passed:
            checks_passed.append("VALIDATION_PASSED")
        else:
            checks_failed.append("VALIDATION_FAILED")
            reasons.extend(validation_report.flags)

        # 3. Check regime confidence
        if regime_signal.confidence < 0.3:
            checks_failed.append("LOW_REGIME_CONFIDENCE")
            reasons.append(f"Confidence: {regime_signal.confidence:.2f}")
        else:
            checks_passed.append(f"REGIME_CONFIDENCE_{regime_signal.confidence:.2f}")

        # 4. Check for viable arbitrage
        if self.strategy_factory.list_strategies():
            checks_passed.append("STRATEGIES_REGISTERED")

        # 5. Decision
        if checks_failed:
            decision = "NO_GO"
            confidence = max(0.0, 1.0 - len(checks_failed) * 0.3)
        else:
            decision = "GO"
            confidence = min(1.0, len(checks_passed) * 0.25)

        result = GoNoGoResult(
            decision=decision,
            confidence=float(confidence),
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            reasons=reasons,
        )

        # Generate alert if needed
        if decision == "NO_GO" or confidence < 0.5:
            self._alerts.append(MonitoringAlert(
                level=MonitoringLevel.RED if decision == "NO_GO" else MonitoringLevel.YELLOW,
                message=f"Research iteration {self._iterations}: {decision} (confidence: {confidence:.2f})",
                component="RESEARCH_AGENT",
                details={"checks_passed": checks_passed, "checks_failed": checks_failed},
            ))

        return result

    def get_alerts(self) -> list[MonitoringAlert]:
        """Get monitoring alerts."""
        return list(self._alerts)

    def get_summary(self) -> dict[str, Any]:
        """Get research summary."""
        n_go = sum(1 for a in self._alerts if "GO" in a.message)
        n_no_go = sum(1 for a in self._alerts if "NO_GO" in a.message)
        return {
            "iterations": self._iterations,
            "go_decisions": n_go,
            "no_go_decisions": n_no_go,
            "total_alerts": len(self._alerts),
        }


class Monitor:
    """System monitoring and alerting."""

    def __init__(self) -> None:
        self._alerts: list[MonitoringAlert] = []
        self._metrics: dict[str, float] = {}

    def record_metric(self, name: str, value: float) -> None:
        """Record a metric."""
        self._metrics[name] = value

    def check_threshold(
        self,
        metric_name: str,
        threshold: float,
        alert_level: MonitoringLevel,
        component: str,
        message: str,
    ) -> bool:
        """Check if metric exceeds threshold."""
        value = self._metrics.get(metric_name)
        if value is None:
            return False

        if value >= threshold:
            self._alerts.append(MonitoringAlert(
                level=alert_level,
                message=message,
                component=component,
                details={metric_name: value, "threshold": threshold},
            ))
            return True
        return False

    def get_health(self) -> MonitoringLevel:
        """Get overall system health."""
        if not self._alerts:
            return MonitoringLevel.GREEN

        critical_alerts = [a for a in self._alerts if a.level in [MonitoringLevel.CRITICAL, MonitoringLevel.RED]]
        if critical_alerts:
            return MonitoringLevel.CRITICAL

        high_alerts = [a for a in self._alerts if a.level in [MonitoringLevel.RED, MonitoringLevel.ORANGE]]
        if high_alerts:
            return MonitoringLevel.ORANGE

        medium_alerts = [a for a in self._alerts if a.level == MonitoringLevel.YELLOW]
        if medium_alerts:
            return MonitoringLevel.YELLOW

        return MonitoringLevel.GREEN

    def get_alerts(self) -> list[MonitoringAlert]:
        """Get all alerts."""
        return list(self._alerts)

    def clear_alerts(self) -> None:
        """Clear all alerts."""
        self._alerts.clear()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
