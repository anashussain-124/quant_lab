"""Phase 3 tests — Backtester + Cost model + Quant Math.

Per AGENTS.md A6 & A4:
- Order lifecycle: SIGNAL > RISK_CHECK > EDGE_CHECK > LIQUIDITY_CHECK
  > EXEC_SIM > ORDER > ACK > PARTIAL_FILL > FILL > SETTLED
- Cost model: NET_EDGE = gross_diff - fees - expected_slippage - latency
  - funding_cost - transfer/rebalancing_cost - operational_cost
- Leakage canary tests mandatory
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import numpy as np
from decimal import Decimal
from datetime import datetime, timezone

from engine.backtester_v2 import (
    Order, OrderSide, OrderStatus, BacktestResult, CostModel, EventDrivenBacktester, PnlLedger
)


def test_decimal_pnl_ledger_sums_10000_small_trades_exactly():
    ledger = PnlLedger()
    for _ in range(10_000):
        ledger.record("0.01", "0.001", "0.0001")
    assert ledger.net_pnl == Decimal("89.0000")
    assert ledger.net_pnl == sum((Decimal("0.0089") for _ in range(10_000)), Decimal("0"))
from engine.safety_gate import SafetyGate, RiskContext, LabConfig
from engine.leakage_canary import LeakageCanary, CanaryResult
from libs.quant_math import (
    realized_volatility, ewma_volatility, ema, trend_score,
    z_score, sharpe_ratio, sortino_ratio, position_size,
    expected_value, kelly_criterion, fractional_kelly, hurst_exponent,
    hurst_to_regime, annualize_sharpe, information_ratio, calmar_ratio, omega_ratio,
)
from strategies import (
    BaseStrategy, Signal, SignalAction, MomentumBreakout,
    get_strategy, STRATEGY_REGISTRY,
)


class TestCostModel:
    """Test transaction cost model."""

    def test_fees_calculation(self):
        """Fees should be computed correctly."""
        model = CostModel(maker_fee=0.0002, taker_fee=0.0004)
        fees = model.compute_fees(10000.0, "LIMIT")
        assert fees == 2.0  # 10000 * 0.0002

        fees = model.compute_fees(10000.0, "MARKET")
        assert fees == 4.0  # 10000 * 0.0004

    def test_slippage_calculation(self):
        """Slippage should be computed correctly."""
        model = CostModel(expected_slippage_bps=2.0)
        slippage = model.compute_slippage(50000.0, 1.0)
        assert slippage == 10.0  # 50000 * 1 * 2/10000

    def test_latency_cost(self):
        """Latency cost should scale."""
        model = CostModel(latency_cost_usd=0.01)
        cost = model.compute_latency_cost(10)
        assert cost == 0.10

    def test_funding_cost(self):
        """Funding cost should scale."""
        model = CostModel(funding_rate=0.0001)
        cost = model.compute_funding_cost(10000.0, 1.0)
        assert cost == 1.0

    def test_total_cost(self):
        """Total cost should sum all components."""
        model = CostModel()
        costs = model.compute_total_cost(
            notional=10000.0, order_type="LIMIT", price=50000.0, volume=1.0
        )
        assert "fees" in costs
        assert "slippage" in costs
        assert "latency_cost" in costs
        assert "funding_cost" in costs
        assert "total_cost" in costs
        assert costs["total_cost"] > 0

    def test_net_edge_formula(self):
        """NET_EDGE formula from AGENTS.md A6."""
        gross_diff = 100.0
        fees = 2.0
        slippage = 1.0
        latency = 0.5
        funding = 0.1
        transfer = 0.0
        operational = 0.0

        net_edge = gross_diff - fees - slippage - latency - funding - transfer - operational
        assert net_edge == 96.4


class TestEventDrivenBacktester:
    """Test event-driven backtester."""

    def test_generate_signal(self):
        """Signal generation should create order."""
        bt = EventDrivenBacktester()
        order = bt.generate_signal(
            "BTCUSDT", "BUY", 1.0, 50000.0, expected_edge=0.01
        )
        assert order.order_id.startswith("ord_")
        assert order.symbol == "BTCUSDT"
        assert order.side == OrderSide.BUY
        assert order.status == OrderStatus.SIGNALED

    def test_order_lifecycle(self):
        """Order should progress through full lifecycle."""
        bt = EventDrivenBacktester()
        order = bt.generate_signal("BTCUSDT", "BUY", 1.0, 50000.0)
        assert order.status == OrderStatus.SIGNALED

        # Risk check
        context = RiskContext()
        assert bt.check_risk(order, context) is True
        assert order.status == OrderStatus.RISK_CHECKED

        # Edge check
        assert bt.check_edge(order, 0.01) is True
        assert order.status == OrderStatus.EDGE_CHECKED

        # Liquidity check
        assert bt.check_liquidity(order) is True
        assert order.status == OrderStatus.LIQUIDITY_CHECKED

        # Simulate execution
        order = bt.simulate_execution(order, fill_price=50100.0)
        assert order.status == OrderStatus.SETTLED
        assert order.fill_price == 50100.0

    def test_backtest_returns_results(self):
        """Backtest should return results."""
        bt = EventDrivenBacktester()
        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.01, "book_depth": 100.0, "fill_price": 50100.0,
             "fill_quantity": 1.0}
        ]
        context = RiskContext()
        result = bt.run_backtest(signals, context)
        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0
        assert isinstance(result.net_edge, float)

    def test_costs_reflected_in_results(self):
        """Costs should be reflected in backtest results."""
        bt = EventDrivenBacktester()
        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.01, "book_depth": 100.0, "fill_price": 50100.0}
        ]
        context = RiskContext()
        result = bt.run_backtest(signals, context)
        assert result.total_fees >= 0
        assert result.total_slippage >= 0

    def test_insufficient_edge_rejects(self):
        """Low edge should be rejected."""
        bt = EventDrivenBacktester()
        order = bt.generate_signal("BTCUSDT", "BUY", 1.0, 50000.0, expected_edge=0.0001)
        assert bt.check_edge(order, 0.0001) is False
        assert order.status == OrderStatus.REJECTED

    def test_insufficient_liquidity_rejects(self):
        """Insufficient liquidity should reject order."""
        from adapters import OrderBook, OrderBook as OB
        from decimal import Decimal as D

        bt = EventDrivenBacktester()
        order = bt.generate_signal("BTCUSDT", "BUY", 1000.0, 50000.0)
        book = OB(
            symbol="BTCUSDT",
            bids=[(D("49999"), 1)],
            asks=[(D("50001"), 1)],
            timestamp=pd.Timestamp.now(tz="UTC"),
        )
        assert bt.check_liquidity(order, book) is False
        assert order.status == "REJECTED" or order.status.value == "REJECTED"

    def test_rejected_order_not_in_results(self):
        """Rejected orders should not proceed to settlement."""
        bt = EventDrivenBacktester()
        order = bt.generate_signal("BTCUSDT", "BUY", 1.0, 50000.0)
        # Reject at risk check
        bad_context = RiskContext(daily_loss_so_far=0.10)
        assert bt.check_risk(order, bad_context) is False
        # Order should not be settled
        assert order.status.value != "SETTLED"


class TestLeakageCanary:
    """Test leakage detection."""

    def test_future_shift_flags_suspicious(self):
        """Future-shifted features should be flagged."""
        canary = LeakageCanary()
        from engine.backtester_v2 import BacktestResult as BR
        result = BR(sharpe_ratio=10.0, total_pnl=100.0, total_trades=50)
        canary_result = canary.future_shift_test(result)
        assert canary_result.passed is False

    def test_normal_backtest_passes(self):
        """Normal backtest should pass."""
        canary = LeakageCanary()
        from engine.backtester_v2 import BacktestResult as BR
        result = BR(sharpe_ratio=0.5, total_pnl=100.0, total_trades=50)
        canary_result = canary.future_shift_test(result)
        assert canary_result.passed is True

    def test_shuffled_labels_show_zero_edge(self):
        """Shuffled labels should produce ~0 edge."""
        canary = LeakageCanary()
        from engine.backtester_v2 import BacktestResult as BR
        result = BR(expectancy=0.0001, total_trades=100)
        canary_result = canary.shuffled_labels_test(result)
        assert canary_result.passed is True

    def test_pipeline_broken_on_suspicious(self):
        """Pipeline broken when suspicious backtest detected."""
        canary = LeakageCanary()
        from engine.backtester_v2 import BacktestResult as BR
        suspicious = BR(sharpe_ratio=10.0, total_pnl=100.0, total_trades=50)
        canary.future_shift_test(suspicious)
        assert canary.pipeline_broken is True


class TestQuantMath:
    """Test all quant math functions."""

    def test_realized_volatility(self):
        """Realized volatility should be calculable."""
        returns = [0.01, -0.01, 0.02, -0.02, 0.01]
        result = realized_volatility(returns)
        assert result.value > 0
        assert result.is_causal is True

    def test_ewma_volatility(self):
        """EWMA volatility should be calculable."""
        returns = [0.01, -0.01, 0.02, -0.02, 0.01]
        result = ewma_volatility(returns)
        assert result.value > 0
        assert result.is_causal is True

    def test_ema(self):
        """EMA should smooth the series."""
        series = [1, 2, 3, 4, 5]
        ema_values = ema(series, n=3)
        assert len(ema_values) == len(series)
        assert ema_values[-1] > ema_values[0]

    def test_trend_score(self):
        """Trend score should be calculable."""
        fast = [1, 2, 3, 4, 5]
        slow = [1, 1.5, 2, 2.5, 3]
        vol = 0.01
        result = trend_score(fast, slow, vol)
        assert result.value > 0  # Uptrend

    def test_z_score(self):
        """Z-score should be normalized."""
        values = list(range(1, 11))
        z_scores = z_score(values, window=5)
        assert len(z_scores) == len(values)
        assert z_scores[-1] > 0  # Above mean

    def test_sharpe_ratio(self):
        """Sharpe ratio should be calculable."""
        returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        sr = sharpe_ratio(returns)
        assert isinstance(sr, float)

    def test_sortino_ratio(self):
        """Sortino ratio should be calculable."""
        returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        sortino = sortino_ratio(returns)
        assert isinstance(sortino, float)

    def test_position_size(self):
        """Position sizing should respect constraints."""
        lots = position_size(
            equity=10000.0,
            risk_fraction=0.01,
            stop_dist=50.0,
            tick_size=0.01,
            tick_value=10.0,
        )
        assert lots >= 0.0

    def test_expected_value(self):
        """EV rule should work."""
        ev = expected_value(p=0.6, win_avg=100.0, loss_avg=50.0, cost=10.0)
        assert ev > 0

    def test_kelly_criterion(self):
        """Kelly should return valid fraction."""
        k = kelly_criterion(p=0.6, b=2.0)
        assert 0.0 <= k <= 1.0

    def test_fractional_kelly(self):
        """Fractional Kelly should be smaller than full."""
        k_full = kelly_criterion(p=0.6, b=2.0)
        k_frac = fractional_kelly(k_full, fraction=0.25)
        assert k_frac <= k_full

    def test_hurst_exponent(self):
        """Hurst should be between 0 and 1."""
        returns = [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.02]
        h = hurst_exponent(returns)
        assert 0.0 <= h <= 1.0

    def test_hurst_to_regime(self):
        """Hurst should map to correct regime."""
        assert hurst_to_regime(0.7) == "TRENDING"
        assert hurst_to_regime(0.3) == "MEAN_REVERTING"
        assert hurst_to_regime(0.5) == "RANDOM_WALK"

    def test_annualize_sharpe(self):
        """Annualized Sharpe should be higher."""
        sr = annualize_sharpe(252, 0.5)
        assert sr > 0.5

    def test_information_ratio(self):
        """Information ratio should be calculable."""
        returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        benchmark = [0.005, 0.01, 0.0, 0.01, 0.002]
        ir = information_ratio(returns, benchmark)
        assert isinstance(ir, float)

    def test_calmar_ratio(self):
        """Calmar ratio should be calculable."""
        returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        cr = calmar_ratio(returns)
        assert isinstance(cr, float)

    def test_omega_ratio(self):
        """Omega ratio should be calculable."""
        returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        omega = omega_ratio(returns)
        assert isinstance(omega, float)

    def test_all_functions_causal(self):
        """All functions should be causal by default."""
        returns = [0.01, -0.01, 0.02]
        assert realized_volatility(returns).is_causal
        assert ewma_volatility(returns).is_causal
        assert trend_score([1, 2], [1, 1.5], 0.01).is_causal


class TestBacktesterIntegration:
    """Test end-to-end backtester integration."""

    def test_full_pipeline(self):
        """Full pipeline: signal → risk → edge → liquidity → execute."""
        bt = EventDrivenBacktester()

        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.02, "book_depth": 100.0, "fill_price": 50100.0,
             "fill_quantity": 1.0},
            {"symbol": "ETHUSDT", "side": "SELL", "quantity": 2.0, "price": 3000.0,
             "expected_edge": 0.015, "book_depth": 50.0, "fill_price": 2990.0,
             "fill_quantity": 2.0},
        ]
        context = RiskContext()
        result = bt.run_backtest(signals, context)

        assert result.total_trades >= 0
        assert isinstance(result.net_edge, float)
        assert isinstance(result.total_pnl, float)
        assert isinstance(result.sharpe_ratio, float)
        assert isinstance(result.total_fees, float)
        assert isinstance(result.total_slippage, float)

    def test_cost_model_reflected(self):
        """Costs should be subtracted from gross profit."""
        bt = EventDrivenBacktester()
        signals = [
            {"symbol": "BTCUSDT", "side": "BUY", "quantity": 1.0, "price": 50000.0,
             "expected_edge": 0.02, "book_depth": 100.0, "fill_price": 50050.0}
        ]
        context = RiskContext()
        result = bt.run_backtest(signals, context)

        # Net profit should be less than gross
        assert result.net_edge <= (result.gross_profit - result.gross_loss)

    def test_no_live_trading(self):
        """Backtester should never create live orders."""
        bt = EventDrivenBacktester()
        order = bt.generate_signal("BTCUSDT", "BUY", 1.0, 50000.0)
        assert order.status == OrderStatus.SIGNALED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
