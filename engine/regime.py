"""Phase 2: Regime Detector + Strategy Matrix + Multi-Timeframe.

Regime detection:
- TREND_UP / TREND_DOWN / RANGE / HIGH_VOL / LOW_VOL / TRANSITION

Strategy families:
- Trend following (Donchian breakout, EMA crossover)
- Mean reversion (Z-score, Bollinger Bands)
- Volatility breakout (ATR channels, Keltner)
- Market structure (Support/resistance, supply/demand zones)

Multi-timeframe:
- Higher TF = regime filter (only trade in direction of regime)
- Lower TF = entry trigger
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    TRANSITION = "TRANSITION"


@dataclass
class RegimeState:
    """Current market regime."""
    regime: Regime
    trend_strength: float  # 0-1
    volatility: float  # ATR as percentage of price
    adx: float  # Average Directional Index
    timestamp: Any


class RegimeDetector:
    """Detects market regime using ADX, ATR, and moving average structure."""

    def __init__(
        self,
        adx_period: int = 14,
        atr_period: int = 14,
        ma_fast: int = 20,
        ma_slow: int = 50,
        vol_lookback: int = 50,
    ):
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow
        self.vol_lookback = vol_lookback

    def _compute_adx(self, data: pd.DataFrame) -> float:
        """Compute Average Directional Index."""
        if len(data) < self.adx_period + 1:
            return 25.0  # Neutral

        high = data["high"].values
        low = data["low"].values
        close = data["close"].values

        # True range
        tr = np.maximum(high[1:] - low[1:], np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        ))

        # Directional movement
        plus_dm = np.where(
            (high[1:] > high[:-1]) & ((high[1:] - high[:-1]) > (low[:-1] - low[1:])),
            np.maximum(high[1:] - high[:-1], 0),
            0
        )
        minus_dm = np.where(
            (low[:-1] > low[1:]) & ((low[:-1] - low[1:]) > (high[1:] - high[:-1])),
            np.maximum(low[:-1] - low[1:], 0),
            0
        )

        # Smoothed averages
        atr = np.mean(tr[-self.adx_period:])
        plus_di = 100 * np.mean(plus_dm[-self.adx_period:]) / atr if atr > 0 else 0
        minus_di = 100 * np.mean(minus_dm[-self.adx_period:]) / atr if atr > 0 else 0

        # ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
        return dx

    def _compute_atr(self, data: pd.DataFrame) -> float:
        """Compute Average True Range."""
        if len(data) < self.atr_period:
            return 0.0

        high = data["high"].values
        low = data["low"].values
        close = data["close"].values

        tr = np.maximum(high[1:] - low[1:], np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        ))

        return float(np.mean(tr[-self.atr_period:]))

    def _compute_volatility(self, data: pd.DataFrame) -> float:
        """Compute realized volatility as ATR/price."""
        atr = self._compute_atr(data)
        price = float(data["close"].values[-1])
        return atr / price if price > 0 else 0.0

    def detect(self, data: pd.DataFrame) -> RegimeState:
        """Detect current market regime."""
        if len(data) < self.ma_slow:
            return RegimeState(Regime.TRANSITION, 0.5, 0.0, 25.0, data.iloc[-1]["timestamp"])

        close = data["close"].values
        adx = self._compute_adx(data)
        vol = self._compute_volatility(data)

        # Trend strength from ADX
        trend_strength = min(adx / 50.0, 1.0)

        # Moving averages
        ma_fast = np.mean(close[-self.ma_fast:])
        ma_slow = np.mean(close[-self.ma_slow:])

        # Volatility regime
        vol_history = []
        for i in range(min(self.vol_lookback, len(data) // 2)):
            window = data.iloc[-(i + self.adx_period):] if i > 0 else data
            if len(window) > self.adx_period:
                vol_history.append(self._compute_volatility(window))

        vol_median = np.median(vol_history) if vol_history else vol
        high_vol = vol > vol_median * 1.5
        low_vol = vol < vol_median * 0.5

        # Determine regime
        if adx > 25:
            if ma_fast > ma_slow and close[-1] > ma_fast:
                regime = Regime.TREND_UP
            elif ma_fast < ma_slow and close[-1] < ma_fast:
                regime = Regime.TREND_DOWN
            else:
                regime = Regime.TRANSITION
        else:
            if high_vol:
                regime = Regime.HIGH_VOL
            elif low_vol:
                regime = Regime.LOW_VOL
            else:
                regime = Regime.RANGE

        return RegimeState(
            regime=regime,
            trend_strength=trend_strength,
            volatility=vol,
            adx=adx,
            timestamp=data.iloc[-1]["timestamp"],
        )


class StrategyMatrix:
    """Strategy matrix for XAUUSD.

    Each strategy is a function that takes data and returns signals.
    Strategies are designed for specific regimes.
    """

    def __init__(self):
        self.strategies = {
            # Trend following
            "trend_donchian": {
                "regimes": [Regime.TREND_UP, Regime.TREND_DOWN],
                "function": self._donchian_breakout,
            },
            "trend_ema_cross": {
                "regimes": [Regime.TREND_UP, Regime.TREND_DOWN],
                "function": self._ema_crossover,
            },
            # Mean reversion
            "mean_reversion_zscore": {
                "regimes": [Regime.RANGE, Regime.LOW_VOL],
                "function": self._zscore_mean_reversion,
            },
            "mean_reversion_bollinger": {
                "regimes": [Regime.RANGE, Regime.LOW_VOL],
                "function": self._bollinger_mean_reversion,
            },
            # Volatility breakout
            "volatility_atr_breakout": {
                "regimes": [Regime.HIGH_VOL, Regime.TRANSITION],
                "function": self._atr_breakout,
            },
            # Market structure
            "structure_support_resistance": {
                "regimes": [Regime.RANGE, Regime.LOW_VOL],
                "function": self._support_resistance,
            },
        }

    def get_strategies_for_regime(self, regime: Regime) -> list[str]:
        """Get strategy names for a given regime."""
        result = []
        for name, config in self.strategies.items():
            if regime in config["regimes"]:
                result.append(name)
        return result

    def generate_signals(
        self,
        data: pd.DataFrame,
        strategy_name: str,
        regime: Regime | None = None,
        lookback: int = 50,
    ) -> list[dict[str, Any]]:
        """Generate signals for a strategy."""
        if strategy_name not in self.strategies:
            return []

        strategy = self.strategies[strategy_name]

        # Check regime compatibility
        if regime is not None and regime not in strategy["regimes"]:
            return []

        # Generate signals
        return strategy["function"](data, lookback)

    def _donchian_breakout(self, data: pd.DataFrame, lookback: int = 50) -> list[dict[str, Any]]:
        """Donchian breakout strategy."""
        if len(data) < lookback + 10:
            return []

        signals = []
        close = data["close"].values
        high = data["high"].values
        low = data["low"].values
        timestamps = data["timestamp"].values

        for i in range(lookback, len(data)):
            window_high = np.max(high[i - lookback:i])
            window_low = np.min(low[i - lookback:i])

            if close[i] > window_high:
                signals.append({
                    "timestamp": pd.Timestamp(timestamps[i]).tz_localize(None),  # Make naive
                    "side": "BUY",
                    "confidence": min(0.8, (close[i] - window_high) / window_high * 100 + 0.5),
                    "reason": "donchian_breakout_up",
                })
            elif close[i] < window_low:
                signals.append({
                    "timestamp": pd.Timestamp(timestamps[i]).tz_localize(None),  # Make naive
                    "side": "SELL",
                    "confidence": min(0.8, (window_low - close[i]) / window_low * 100 + 0.5),
                    "reason": "donchian_breakout_down",
                })

        return signals

    def _ema_crossover(self, data: pd.DataFrame, lookback: int = 50) -> list[dict[str, Any]]:
        """EMA crossover strategy."""
        if len(data) < lookback + 20:
            return []

        signals = []
        close = data["close"].values
        timestamps = data["timestamp"].values

        ema_fast = pd.Series(close).ewm(span=20).mean().values
        ema_slow = pd.Series(close).ewm(span=50).mean().values

        for i in range(50, len(data)):
            if ema_fast[i] > ema_slow[i] and ema_fast[i - 1] <= ema_slow[i - 1]:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "BUY",
                    "confidence": 0.7,
                    "reason": "ema_golden_cross",
                })
            elif ema_fast[i] < ema_slow[i] and ema_fast[i - 1] >= ema_slow[i - 1]:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "SELL",
                    "confidence": 0.7,
                    "reason": "ema_death_cross",
                })

        return signals

    def _zscore_mean_reversion(self, data: pd.DataFrame, lookback: int = 50) -> list[dict[str, Any]]:
        """Z-score mean reversion strategy."""
        if len(data) < lookback + 10:
            return []

        signals = []
        close = data["close"].values
        timestamps = data["timestamp"].values

        for i in range(lookback, len(data)):
            mean = np.mean(close[i - lookback:i])
            std = np.std(close[i - lookback:i])

            if std == 0:
                continue

            z_score = (close[i] - mean) / std

            if z_score > 2.0:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "SELL",
                    "confidence": min(0.8, abs(z_score) / 4.0),
                    "reason": "zscore_overbought",
                })
            elif z_score < -2.0:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "BUY",
                    "confidence": min(0.8, abs(z_score) / 4.0),
                    "reason": "zscore_oversold",
                })

        return signals

    def _bollinger_mean_reversion(self, data: pd.DataFrame, lookback: int = 50) -> list[dict[str, Any]]:
        """Bollinger Band mean reversion strategy."""
        if len(data) < lookback + 10:
            return []

        signals = []
        close = data["close"].values
        timestamps = data["timestamp"].values

        for i in range(lookback, len(data)):
            mean = np.mean(close[i - lookback:i])
            std = np.std(close[i - lookback:i])

            upper = mean + 2 * std
            lower = mean - 2 * std

            if close[i] > upper:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "SELL",
                    "confidence": min(0.7, (close[i] - upper) / std * 0.1),
                    "reason": "bollinger_upper_touch",
                })
            elif close[i] < lower:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "BUY",
                    "confidence": min(0.7, (lower - close[i]) / std * 0.1),
                    "reason": "bollinger_lower_touch",
                })

        return signals

    def _atr_breakout(self, data: pd.DataFrame, lookback: int = 50) -> list[dict[str, Any]]:
        """ATR channel breakout strategy."""
        if len(data) < lookback + 10:
            return []

        signals = []
        close = data["close"].values
        high = data["high"].values
        low = data["low"].values
        timestamps = data["timestamp"].values

        for i in range(lookback, len(data)):
            atr = np.mean(np.maximum(
                high[i - lookback:i] - low[i - lookback:i],
                np.abs(high[i - lookback:i] - close[i - lookback - 1:i - 1])
            ))

            upper = close[i - 1] + 2 * atr
            lower = close[i - 1] - 2 * atr

            if close[i] > upper:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "BUY",
                    "confidence": 0.6,
                    "reason": "atr_breakout_up",
                })
            elif close[i] < lower:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "SELL",
                    "confidence": 0.6,
                    "reason": "atr_breakout_down",
                })

        return signals

    def _support_resistance(self, data: pd.DataFrame, lookback: int = 50) -> list[dict[str, Any]]:
        """Support/resistance bounce strategy."""
        if len(data) < lookback * 2:
            return []

        signals = []
        close = data["close"].values
        high = data["high"].values
        low = data["low"].values
        timestamps = data["timestamp"].values

        for i in range(lookback, len(data)):
            # Find local highs and resistance
            window_high = np.max(high[i - lookback:i])
            window_low = np.min(low[i - lookback:i])

            # Price near support
            if close[i] <= window_low * 1.001 and close[i] >= window_low * 0.999:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "BUY",
                    "confidence": 0.5,
                    "reason": "support_bounce",
                })
            # Price near resistance
            elif close[i] >= window_high * 0.999 and close[i] <= window_high * 1.001:
                signals.append({
                    "timestamp": timestamps[i],
                    "side": "SELL",
                    "confidence": 0.5,
                    "reason": "resistance_rejection",
                })

        return signals


def create_regime_detector() -> RegimeDetector:
    """Create default regime detector for XAUUSD."""
    return RegimeDetector()


def create_strategy_matrix() -> StrategyMatrix:
    """Create default strategy matrix."""
    return StrategyMatrix()


def calculate_donchian(data: pd.DataFrame, period: int) -> tuple[np.ndarray, np.ndarray]:
    """Calculate Donchian upper and lower channel boundaries."""
    high = data["high"].values
    low = data["low"].values
    n = len(data)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period, n):
        upper[i] = np.max(high[i - period:i])
        lower[i] = np.min(low[i - period:i])
    return upper, lower


def calculate_atr(data: pd.DataFrame, period: int = 14) -> np.ndarray:
    """Calculate Average True Range (ATR)."""
    high = data["high"].values
    low = data["low"].values
    close = data["close"].values
    n = len(data)
    atr = np.full(n, np.nan)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    for i in range(period, n):
        atr[i] = np.mean(tr[i - period + 1:i + 1])
    return atr

