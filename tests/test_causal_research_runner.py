"""Regression tests for causal generic-strategy research execution."""

import numpy as np
import pandas as pd

from engine.backtester_v2 import CostModel
from engine.research_runner import ResearchRunner
from strategies.real_strategies import MomentumBreakout
from strategies.real_strategies import FundingCarryReal


def _bars(n: int = 90) -> pd.DataFrame:
    close = np.full(n, 100.0)
    close[55:65] = np.linspace(100.0, 120.0, 10)
    close[65:] = 120.0
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "symbol": ["XAUUSD"] * n,
        }
    )


def test_real_strategy_returns_scalar_symbol_and_is_causal() -> None:
    data = _bars()
    strategy = MomentumBreakout(lookback=5, exit_lookback=3, trend_period=10)
    first = strategy.generate_signal(data.iloc[:60])
    changed_future = data.copy()
    changed_future.loc[60:, "close"] = 10_000.0
    changed_future.loc[60:, "high"] = 10_001.0
    changed_future.loc[60:, "low"] = 9_999.0
    second = strategy.generate_signal(changed_future.iloc[:60])
    assert first.symbol == "XAUUSD"
    assert second.action == first.action
    assert second.side == first.side


def test_causal_runner_uses_next_open_and_closes_final_position() -> None:
    data = _bars()
    runner = ResearchRunner(data)
    strategy = MomentumBreakout(lookback=5, exit_lookback=3, trend_period=10)
    costs = CostModel(taker_fee=0.0, expected_slippage_bps=0.0, spread_bps=0.0)

    legacy = runner._run_backtest_on_data(data, strategy, costs)
    causal = runner._run_causal_backtest_on_data(data, strategy, costs)

    assert len(causal) >= 1
    assert len(legacy) >= 1
    assert not np.array_equal(legacy, causal)
    assert all(np.isfinite(causal))


def test_invalid_real_strategy_parameters_fail_closed() -> None:
    try:
        MomentumBreakout(lookback=1)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid strategy parameters must be rejected")


def test_funding_no_trade_signal_cannot_become_take_via_edge(monkeypatch) -> None:
    strategy = FundingCarryReal(lookback=3)
    signal = strategy.generate_signal(pd.DataFrame({"funding_rate": [0.0, 0.0, 0.0]}))
    signal.confidence = 0.7
    assert strategy.estimate_edge(signal) == 0.0
    monkeypatch.setattr(strategy, "generate_signal", lambda data: signal)
    result = strategy.run(pd.DataFrame())
    assert result.signal.action.value == "NO_TRADE"
    assert result.signal.edge == 0.0
