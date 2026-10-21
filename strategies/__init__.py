"""Strategies interface — plug-in system for trading strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd
from decimal import Decimal


class SignalAction(str, Enum):
    TAKE = "TAKE"
    NO_TRADE = "NO_TRADE"
    REDUCE_RISK = "REDUCE_RISK"
    RETIRE = "RETIRE"
    RESEARCH = "RESEARCH"


@dataclass
class Signal:
    action: SignalAction
    symbol: str
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    confidence: float = 0.0
    edge: float = 0.0
    risk: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    explanation: str = ""
    environment: str = "BACKTEST"


@dataclass
class StrategyResult:
    signal: Signal
    passed_gates: list[str]
    failed_gates: list[str]
    backtest_pnl: float = 0.0
    oos_expectancy: float = 0.0
    trades: int = 0
    sharpe: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    @abstractmethod
    def generate_signal(self, data: pd.DataFrame) -> Signal:
        ...

    @abstractmethod
    def estimate_edge(self, signal: Signal) -> float:
        ...

    @abstractmethod
    def estimate_risk(self, signal: Signal) -> float:
        ...

    @abstractmethod
    def confidence(self, signal: Signal) -> float:
        ...

    @abstractmethod
    def explain(self, signal: Signal) -> str:
        ...

    def run(self, data: pd.DataFrame) -> StrategyResult:
        signal = self.generate_signal(data)
        edge = self.estimate_edge(signal)
        risk = self.estimate_risk(signal)
        conf = self.confidence(signal)
        explanation = self.explain(signal)

        return StrategyResult(
            signal=Signal(
                action=SignalAction.TAKE if edge > 0 else SignalAction.NO_TRADE,
                symbol=signal.symbol,
                side=signal.side,
                quantity=signal.quantity,
                price=signal.price,
                confidence=conf,
                edge=edge,
                risk=risk,
                explanation=explanation,
                reason_codes=signal.reason_codes,
                environment=signal.environment,
            ),
            passed_gates=[],
            failed_gates=[],
        )


class MomentumBreakout(BaseStrategy):
    """Placeholder momentum strategy."""

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(
            action=SignalAction.NO_TRADE,
            symbol="UNKNOWN",
            explanation="Placeholder",
        )

    def estimate_edge(self, signal: Signal) -> float:
        return 0.0

    def estimate_risk(self, signal: Signal) -> float:
        return 0.0

    def confidence(self, signal: Signal) -> float:
        return 0.0

    def explain(self, signal: Signal) -> str:
        return "Placeholder"


class MeanReversionVWAP(BaseStrategy):
    """Placeholder VWAP mean reversion."""

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(action=SignalAction.NO_TRADE, symbol="UNKNOWN", explanation="Placeholder")

    def estimate_edge(self, signal: Signal) -> float:
        return 0.0

    def estimate_risk(self, signal: Signal) -> float:
        return 0.0

    def confidence(self, signal: Signal) -> float:
        return 0.0

    def explain(self, signal: Signal) -> str:
        return "Placeholder"


class StatArb(BaseStrategy):
    """Placeholder stat arb."""

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(action=SignalAction.NO_TRADE, symbol="UNKNOWN", explanation="Placeholder")

    def estimate_edge(self, signal: Signal) -> float:
        return 0.0

    def estimate_risk(self, signal: Signal) -> float:
        return 0.0

    def confidence(self, signal: Signal) -> float:
        return 0.0

    def explain(self, signal: Signal) -> str:
        return "Placeholder"


class FundingCarry(BaseStrategy):
    """Placeholder funding carry."""

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        return Signal(action=SignalAction.NO_TRADE, symbol="UNKNOWN", explanation="Placeholder")

    def estimate_edge(self, signal: Signal) -> float:
        return 0.0

    def estimate_risk(self, signal: Signal) -> float:
        return 0.0

    def confidence(self, signal: Signal) -> float:
        return 0.0

    def explain(self, signal: Signal) -> str:
        return "Placeholder"


# Registry
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "momentum_breakout": MomentumBreakout,
    "mean_reversion_vwap": MeanReversionVWAP,
    "stat_arb": StatArb,
    "funding_carry": FundingCarry,
}


def get_strategy(name: str) -> BaseStrategy | None:
    strategy_class = STRATEGY_REGISTRY.get(name)
    if strategy_class:
        return strategy_class()
    return None


def register_strategy(name: str, strategy_class: type[BaseStrategy]) -> None:
    STRATEGY_REGISTRY[name] = strategy_class


# Register real strategies (done at end to avoid circular import)
def _register_real_strategies():
    from strategies.real_strategies import (
        MomentumBreakout,
        MeanReversionZScore,
        FundingCarryReal,
    )
    STRATEGY_REGISTRY["momentum_breakout_real"] = MomentumBreakout
    STRATEGY_REGISTRY["mean_reversion_zscore"] = MeanReversionZScore
    STRATEGY_REGISTRY["funding_carry_real"] = FundingCarryReal


_register_real_strategies()
