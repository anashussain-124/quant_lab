"""Real strategy implementations for BTC/USDT and XAUUSD.

These are NOT placeholders — they implement actual logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies import BaseStrategy, Signal, SignalAction


def _symbol(data: pd.DataFrame) -> str:
    """Return a scalar symbol without accidentally returning a whole column."""
    value = data.attrs.get("symbol")
    if isinstance(value, str) and value:
        return value
    if "symbol" in data.columns and not data.empty:
        value = data["symbol"].iloc[-1]
        return str(value) if pd.notna(value) else "UNKNOWN"
    return "UNKNOWN"


class MomentumBreakout(BaseStrategy):
    """Donchian breakout with EMA trend filter.

    Entry: Price closes above N-day high
    Exit: Price closes below M-day low
    Filter: Only trade in direction of longer-term trend
    """

    def __init__(self, lookback: int = 20, exit_lookback: int = 10, trend_period: int = 50):
        if min(lookback, exit_lookback, trend_period) < 2:
            raise ValueError("lookback, exit_lookback, and trend_period must be >= 2")
        self.lookback = lookback
        self.exit_lookback = exit_lookback
        self.trend_period = trend_period
        self.min_history = max(lookback + 1, exit_lookback + 1, trend_period)

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        if len(data) < self.trend_period:
            return Signal(action=SignalAction.NO_TRADE, symbol=_symbol(data))

        close = data["close"].values
        high = data["high"].values
        low = data["low"].values

        # Donchian breakout
        upper = np.max(high[-self.lookback-1:-1])
        lower = np.min(low[-self.exit_lookback-1:-1])

        # Trend filter
        ema = pd.Series(close).ewm(span=self.trend_period, adjust=False).mean().values[-1]

        # Signals
        if close[-1] > upper and close[-1] > ema:
            return Signal(
                action=SignalAction.TAKE,
                symbol=_symbol(data),
                side="BUY",
                price=float(close[-1]),
                confidence=min(0.8, (close[-1] - upper) / upper * 100 + 0.5),
                reason_codes=["donchian_breakout", "trend_aligned"],
            )
        elif close[-1] < lower and close[-1] < ema:
            return Signal(
                action=SignalAction.TAKE,
                symbol=_symbol(data),
                side="SELL",
                price=float(close[-1]),
                confidence=min(0.8, (lower - close[-1]) / lower * 100 + 0.5),
                reason_codes=["donchian_breakdown", "trend_aligned"],
            )

        return Signal(
            action=SignalAction.NO_TRADE,
            symbol=_symbol(data),
            reason_codes=["no_breakout"],
        )

    def estimate_edge(self, signal: Signal) -> float:
        if signal.action != SignalAction.TAKE:
            return 0.0
        return signal.confidence * 0.01  # Rough estimate

    def estimate_risk(self, signal: Signal) -> float:
        return 0.02  # 2% risk per trade

    def confidence(self, signal: Signal) -> float:
        return signal.confidence

    def explain(self, signal: Signal) -> str:
        return f"Momentum breakout: {signal.side} at {signal.price}, {signal.reason_codes}"


class MeanReversionZScore(BaseStrategy):
    """Z-score mean reversion.

    Entry: Price deviates N standard deviations from mean
    Exit: Price reverts to mean
    """

    def __init__(self, lookback: int = 50, z_entry: float = 2.0, z_exit: float = 0.5):
        if lookback < 2 or z_entry <= 0 or z_exit < 0:
            raise ValueError("lookback must be >= 2 and z_entry must be positive")
        self.lookback = lookback
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.min_history = lookback

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        if len(data) < self.lookback:
            return Signal(action=SignalAction.NO_TRADE, symbol=_symbol(data))

        close = data["close"].values
        mean = np.mean(close[-self.lookback:])
        std = np.std(close[-self.lookback:])

        if std == 0:
            return Signal(action=SignalAction.NO_TRADE, symbol=data.get("symbol", "UNKNOWN"))

        z_score = (close[-1] - mean) / std

        if z_score > self.z_entry:
            return Signal(
                action=SignalAction.TAKE,
                symbol=_symbol(data),
                side="SELL",
                price=float(close[-1]),
                confidence=min(0.8, abs(z_score) / 4.0),
                reason_codes=["zscore_overbought"],
            )
        elif z_score < -self.z_entry:
            return Signal(
                action=SignalAction.TAKE,
                symbol=_symbol(data),
                side="BUY",
                price=float(close[-1]),
                confidence=min(0.8, abs(z_score) / 4.0),
                reason_codes=["zscore_oversold"],
            )

        return Signal(
            action=SignalAction.NO_TRADE,
            symbol=_symbol(data),
            reason_codes=["zscore_neutral"],
        )

    def estimate_edge(self, signal: Signal) -> float:
        if signal.action != SignalAction.TAKE:
            return 0.0
        return signal.confidence * 0.005

    def estimate_risk(self, signal: Signal) -> float:
        return 0.015

    def confidence(self, signal: Signal) -> float:
        return signal.confidence

    def explain(self, signal: Signal) -> str:
        return f"Mean reversion: {signal.side} at {signal.price}, {signal.reason_codes}"


class FundingContrarianDirectional(BaseStrategy):
    """Directional funding-rate contrarian strategy, not true carry.

    Uses funding rates from perpetual markets to choose a directional bet.
    True carry requires a delta-neutral spot+perp model with explicit basis
    and funding-payment accounting; this strategy is not that model.
    """

    def __init__(self, lookback: int = 8):
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.lookback = lookback
        self.min_history = lookback

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        # Check if funding rate data is available
        if "funding_rate" not in data.columns:
            return Signal(
                action=SignalAction.NO_TRADE,
                symbol=_symbol(data),
                reason_codes=["no_funding_data"],
            )

        funding = data["funding_rate"].values
        if len(funding) < self.lookback:
            return Signal(action=SignalAction.NO_TRADE, symbol=_symbol(data))

        avg_funding = np.mean(funding[-self.lookback:])

        if avg_funding > 0.0001:
            return Signal(
                action=SignalAction.TAKE,
                symbol=_symbol(data),
                side="SELL",
                confidence=min(0.7, avg_funding * 10000),
                reason_codes=["positive_funding"],
            )
        elif avg_funding < -0.0001:
            return Signal(
                action=SignalAction.TAKE,
                symbol=_symbol(data),
                side="BUY",
                confidence=min(0.7, abs(avg_funding) * 10000),
                reason_codes=["negative_funding"],
            )

        return Signal(
            action=SignalAction.NO_TRADE,
            symbol=_symbol(data),
            reason_codes=["funding_neutral"],
        )

    def estimate_edge(self, signal: Signal) -> float:
        if signal.action != SignalAction.TAKE:
            return 0.0
        return signal.confidence * 0.001

    def estimate_risk(self, signal: Signal) -> float:
        return 0.01

    def confidence(self, signal: Signal) -> float:
        return signal.confidence

    def explain(self, signal: Signal) -> str:
        return f"Funding carry: {signal.side}, {signal.reason_codes}"


# Registry is populated in __init__.py after all classes are defined
