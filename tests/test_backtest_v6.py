"""Synthetic-only checks for defined H4 v6 execution mechanics."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from research.backtest_v6 import (
    V6BacktestConfig,
    V6EquityPoint,
    V6ExecutionBlockedError,
    V6Trade,
    _build_market,
    _Market,
    _simulate_v6_plan,
    _simulate_v6_trade,
    max_drawdown_usd,
    run_v6_frozen_grid,
    validate_v6_frame,
)
from research.constants_v6 import V6Environment
from research.costs_v6 import V6CostModel, V6CostSemanticsBlockedError
from research.strategies_v6 import all_specs
from research.v6_safety import V6RunBlockedError
from research.validation_v6 import build_v6_perturbations


class SyntheticNoSwapPolicy:
    def credit_price_per_ounce(
        self,
        *,
        direction: int,
        entry_time: pd.Timestamp,
        exit_time: pd.Timestamp,
        stress_multiplier: float,
    ) -> float:
        del direction, entry_time, exit_time, stress_multiplier
        return 0.0


def frame(
    *,
    length: int = 130,
    start: str = "2020-01-01",
) -> pd.DataFrame:
    index = pd.date_range(start, periods=length, freq="4h", tz="UTC")
    values = np.full(length, 100.0)
    return pd.DataFrame(
        {
            "open": values.copy(),
            "high": values + 1.0,
            "low": values - 1.0,
            "close": values.copy(),
            "spread": np.full(length, 10.0),
        },
        index=index,
    )


def market_for(data: pd.DataFrame, *, atr: float = 2.0) -> _Market:
    config = V6BacktestConfig(
        point_size=0.01,
        contract_size_ounces_per_lot=100.0,
    )
    return _build_market(data, np.full(len(data), atr), config)


def simulate(
    data: pd.DataFrame,
    *,
    entry_index: int = 1,
    direction: int = 1,
    stop_distance: float = 2.0,
    trail_atr: float = 2.0,
) -> V6Trade:
    config = V6BacktestConfig(
        point_size=0.01,
        contract_size_ounces_per_lot=100.0,
    )
    cost_model = V6CostModel()
    return _simulate_v6_trade(
        market_for(data),
        signal_index=entry_index - 1,
        entry_index=entry_index,
        direction=direction,
        stop_distance=stop_distance,
        trail_atr=trail_atr,
        max_hold=120,
        allow_matched_null_horizon=False,
        equity_usd=config.starting_balance_usd,
        spec_config_id="synthetic",
        config=config,
        cost_model=cost_model,
        swap_policy=SyntheticNoSwapPolicy(),
    )


def test_v6_gap_stop_fills_at_open_and_accounts_for_costs_and_size() -> None:
    data = frame()
    data.loc[data.index[1], ["open", "high", "low", "close"]] = [
        100.0,
        101.0,
        99.0,
        100.0,
    ]
    data.loc[data.index[2], ["open", "high", "low", "close"]] = [
        97.0,
        98.0,
        96.0,
        97.0,
    ]

    trade = simulate(data)

    assert trade.environment is V6Environment.BACKTEST
    assert trade.reason == "stop"
    assert trade.open_fill is True
    assert trade.exit_index == 2
    assert trade.exit_price == 97.0
    assert trade.gross_r == pytest.approx(-1.5)
    assert trade.spread_slippage_r == pytest.approx(0.055)
    assert trade.commission_r == pytest.approx(0.05)
    assert trade.net_r == pytest.approx(-1.605)
    assert trade.volume_lots == pytest.approx(0.12)
    assert trade.risk_usd == pytest.approx(24.0)
    assert trade.net_pnl_usd == pytest.approx(-38.52)


def test_v6_intrabar_stop_uses_level_and_trailing_stop_activates_next_bar() -> None:
    data = frame()
    data.loc[data.index[1], ["open", "high", "low", "close"]] = [
        100.0,
        110.0,
        99.0,
        110.0,
    ]
    data.loc[data.index[2], ["open", "high", "low", "close"]] = [
        107.0,
        108.0,
        105.0,
        107.0,
    ]

    trade = simulate(data)

    assert trade.reason == "stop"
    assert trade.open_fill is False
    assert trade.exit_index == 2
    assert trade.exit_price == 106.0
    assert trade.gross_r == pytest.approx(3.0)


def test_v6_time_exit_uses_next_open_after_120_bars() -> None:
    data = frame()
    data.loc[data.index[121], ["open", "high", "low", "close"]] = [
        101.0,
        102.0,
        100.0,
        101.0,
    ]

    trade = simulate(data)

    assert trade.reason == "time"
    assert trade.open_fill is True
    assert trade.exit_index == 121
    assert trade.exit_price == 101.0
    assert trade.bars_held == 120


def test_v6_execution_refuses_missing_swap_policy_and_stress_mismatch() -> None:
    data = frame()
    market = market_for(data)
    config = V6BacktestConfig(point_size=0.01, contract_size_ounces_per_lot=100.0)
    with pytest.raises(V6ExecutionBlockedError, match="no approved"):
        _simulate_v6_trade(
            market,
            signal_index=0,
            entry_index=1,
            direction=1,
            stop_distance=2.0,
            trail_atr=2.0,
            max_hold=120,
            allow_matched_null_horizon=False,
            equity_usd=config.starting_balance_usd,
            spec_config_id="synthetic",
            config=config,
            cost_model=V6CostModel(),
            swap_policy=None,
        )

    with pytest.raises(ValueError, match="must match"):
        _simulate_v6_plan(
            data,
            spec=all_specs()[0],
            config=replace(config, stress_multiplier=1.5),
            cost_model=V6CostModel(stress_multiplier=1.0),
            swap_policy=SyntheticNoSwapPolicy(),
        )

    with pytest.raises(V6CostSemanticsBlockedError, match="unresolved"):
        V6CostModel().swap_cost(direction=1, rollovers=1)


def test_v6_plan_is_non_pyramiding_and_uses_frozen_config_only() -> None:
    data = frame(length=150)
    signal_index = 23
    data.loc[data.index[signal_index], ["open", "high", "low", "close"]] = [
        100.0,
        102.0,
        99.0,
        101.5,
    ]
    config = V6BacktestConfig(point_size=0.01, contract_size_ounces_per_lot=100.0)
    result = _simulate_v6_plan(
        data,
        spec=all_specs()[0],
        config=config,
        cost_model=V6CostModel(),
        swap_policy=SyntheticNoSwapPolicy(),
    )
    assert result.config_id == "h4d01"
    assert len(result.trades) == 1
    assert result.trades[0].signal_index == signal_index
    assert result.trades[0].entry_index == signal_index + 1
    assert result.trades[0].exit_index == signal_index + 1 + 120
    assert result.trades[0].bars_held == 120
    assert result.equity_curve[-1].equity_usd == pytest.approx(
        config.starting_balance_usd + result.trades[0].net_pnl_usd
    )

    altered = replace(all_specs()[0], params={**all_specs()[0].params, "n": 21})
    with pytest.raises(ValueError, match="exact 24 frozen"):
        _simulate_v6_plan(
            data,
            spec=altered,
            config=config,
            cost_model=V6CostModel(),
            swap_policy=SyntheticNoSwapPolicy(),
        )


def test_frozen_grid_refuses_before_accessing_supplied_frame() -> None:
    with pytest.raises(V6RunBlockedError, match="partial_h4_bin_policy"):
        run_v6_frozen_grid(
            pd.DataFrame(),
            config=V6BacktestConfig(
                point_size=0.01,
                contract_size_ounces_per_lot=100.0,
            ),
            cost_model=V6CostModel(),
            swap_policy=SyntheticNoSwapPolicy(),
        )


def test_only_enumerated_per_config_perturbations_enter_the_simulator() -> None:
    data = frame(length=150)
    spec = all_specs()[0]
    variant = build_v6_perturbations(spec)[0]
    permitted = replace(spec, params=dict(variant.parameters))
    config = V6BacktestConfig(
        point_size=0.01,
        contract_size_ounces_per_lot=100.0,
    )
    result = _simulate_v6_plan(
        data,
        spec=permitted,
        config=config,
        cost_model=V6CostModel(),
        swap_policy=SyntheticNoSwapPolicy(),
        allow_perturbation=True,
    )
    assert result.config_id == spec.config_id
    assert result.cost_stress_multiplier == 1.0

    arbitrary = replace(spec, params={**spec.params, "n": 21})
    with pytest.raises(ValueError, match="outside the approved perturbation set"):
        _simulate_v6_plan(
            data,
            spec=arbitrary,
            config=config,
            cost_model=V6CostModel(),
            swap_policy=SyntheticNoSwapPolicy(),
            allow_perturbation=True,
        )


def test_v6_frame_rejects_invalid_data_and_drawdown_is_consistent() -> None:
    data = frame(length=3)
    validate_v6_frame(data)
    invalid = data.copy()
    invalid.loc[invalid.index[1], "high"] = 90.0
    with pytest.raises(ValueError, match="candle bounds"):
        validate_v6_frame(invalid)

    points = (
        V6EquityPoint(data.index[0], 5_000.0, 0.0, 0.0),
        V6EquityPoint(data.index[1], 5_100.0, 1.0, 0.0),
        V6EquityPoint(data.index[2], 4_950.0, -0.5, 150.0),
    )
    assert max_drawdown_usd(points) == pytest.approx(150.0)
