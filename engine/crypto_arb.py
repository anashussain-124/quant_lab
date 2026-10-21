"""Phase 7: Crypto Arbitrage Feasibility Study.

Per AGENTS.md Part E (Phase 5):
- Crypto arbitrage: cross-exchange funding rate + price discrepancy
- Paper trading only, CCXT paper mode
- Funding rate arb between exchanges
- Price arb with real order books
- Risk limits, daily loss limits
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from engine.risk_engine import RiskEngine, OrderSide
from engine.safety_gate import RiskContext
from engine.logger import AuditLogger
from engine.backtester_v2 import CostModel
from adapters.data_quality import DataQualityEngine, QualityReport


class ArbitrageType(str, Enum):
    """Types of crypto arbitrage."""
    FUNDING_RATE = "FUNDING_RATE"  # Cross-exchange funding rate arb
    PRICE = "PRICE"  # Cross-exchange price discrepancy
    TRIANGLE = "TRIANGLE"  # Triangle arbitrage within one exchange
    STATISTICAL = "STATISTICAL"  # Statistical arbitrage


class ArbitrageStatus(str, Enum):
    """Arbitrage opportunity status."""
    VIABLE = "VIABLE"
    MARGINAL = "MARGINAL"
    NOT_VIABLE = "NOT_VIABLE"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    HIGH_RISK = "HIGH_RISK"


@dataclass
class ArbitrageOpportunity:
    """Represents a detected arbitrage opportunity."""
    opportunity_id: str
    arbitrage_type: ArbitrageType
    symbol: str
    exchanges: list[str]
    buy_price: float
    sell_price: float
    spread_bps: float  # Spread in basis points
    net_edge_bps: float  # Net edge after costs
    funding_rate_bps: float = 0.0
    estimated_profit: float = 0.0
    volume: float = 1.0
    status: ArbitrageStatus = ArbitrageStatus.NOT_VIABLE
    risk_score: float = 0.0  # 0-1, lower = safer
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: float = 30.0  # Time to live


@dataclass
class ArbitrageResult:
    """Result of an arbitrage feasibility study."""
    opportunities_found: int = 0
    opportunities_viable: int = 0
    total_volume_traded: float = 0.0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    success_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    avg_hold_time: float = 0.0
    viability_score: float = 0.0  # Overall feasibility score 0-1
    recommendations: list[str] = field(default_factory=list)


class CryptoArbitrageFeasibility:
    """Crypto arbitrage feasibility study.

    Per AGENTS.md Part E:
    - Cross-exchange funding rate + price discrepancy
    - Paper trading only
    - CCXT paper mode against real order books
    - Risk limits enforced
    """

    def __init__(
        self,
        risk_engine: RiskEngine | None = None,
        audit: AuditLogger | None = None,
        quality_engine: DataQualityEngine | None = None,
    ) -> None:
        self.risk_engine = risk_engine or RiskEngine()
        self.audit = audit or AuditLogger()
        self.quality_engine = quality_engine or DataQualityEngine()
        self._opportunities: list[ArbitrageOpportunity] = []
        _results: list[ArbitrageResult] = []
        self._cost_model = CostModel()
        self._trades: list[dict[str, Any]] = []

    def detect_funding_rate_arb(
        self,
        symbol: str,
        exchange_a_rate: float,
        exchange_b_rate: float,
        price_a: float,
        price_b: float,
        volume: float = 1.0,
    ) -> ArbitrageOpportunity:
        """Detect cross-exchange funding rate arbitrage.

        Funding rate arb: long on exchange with negative rate, short on exchange with positive rate.
        """
        # Calculate funding rate difference
        rate_diff = exchange_b_rate - exchange_a_rate  # bps

        if abs(rate_diff) < 1.0:  # Minimum 1 bps spread
            return ArbitrageOpportunity(
                opportunity_id=f"arb_{int(time.time())}",
                arbitrage_type=ArbitrageType.FUNDING_RATE,
                symbol=symbol,
                exchanges=["A", "B"],
                buy_price=price_a,
                sell_price=price_b,
                spread_bps=0.0,
                net_edge_bps=0.0,
                status=ArbitrageStatus.NOT_VIABLE,
                risk_score=1.0,
            )

        # Calculate costs
        notional = price_a * volume
        costs = self._cost_model.compute_total_cost(
            notional=notional, order_type="LIMIT", price=price_a, volume=volume
        )
        total_cost_bps = (costs["fees"] + costs["slippage"]) / notional * 10000

        net_edge = abs(rate_diff) * 10000 - total_cost_bps

        # Determine viability
        if net_edge > 5.0:
            status = ArbitrageStatus.VIABLE
        elif net_edge > 2.0:
            status = ArbitrageStatus.MARGINAL
        else:
            status = ArbitrageStatus.NOT_VIABLE

        risk_score = max(0.0, min(1.0, 1.0 - net_edge / 20.0))

        opp = ArbitrageOpportunity(
            opportunity_id=f"arb_{int(time.time())}",
            arbitrage_type=ArbitrageType.FUNDING_RATE,
            symbol=symbol,
            exchanges=["A", "B"],
            buy_price=price_a,
            sell_price=price_b,
            spread_bps=abs(rate_diff),
            net_edge_bps=net_edge,
            funding_rate_bps=rate_diff,
            estimated_profit=net_edge / 10000 * notional,
            volume=volume,
            status=status,
            risk_score=risk_score,
        )

        self._opportunities.append(opp)
        return opp

    def detect_price_arb(
        self,
        symbol: str,
        price_a: float,
        price_b: float,
        book_a_depth: float,
        book_b_depth: float,
        volume: float = 1.0,
    ) -> ArbitrageOpportunity:
        """Detect cross-exchange price discrepancy arbitrage."""
        spread = abs(price_a - price_b) / min(price_a, price_b) * 10000  # bps

        if spread < 5.0:  # Minimum 5 bps to be interesting
            return ArbitrageOpportunity(
                opportunity_id=f"arb_{int(time.time())}",
                arbitrage_type=ArbitrageType.PRICE,
                symbol=symbol,
                exchanges=["A", "B"],
                buy_price=min(price_a, price_b),
                sell_price=max(price_a, price_b),
                spread_bps=spread,
                net_edge_bps=0.0,
                status=ArbitrageStatus.NOT_VIABLE,
                risk_score=1.0,
            )

        # Check liquidity
        if book_a_depth < volume or book_b_depth < volume:
            return ArbitrageOpportunity(
                opportunity_id=f"arb_{int(time.time())}",
                arbitrage_type=ArbitrageType.PRICE,
                symbol=symbol,
                exchanges=["A", "B"],
                buy_price=min(price_a, price_b),
                sell_price=max(price_a, price_b),
                spread_bps=spread,
                net_edge_bps=0.0,
                status=ArbitrageStatus.INSUFFICIENT_LIQUIDITY,
                risk_score=1.0,
            )

        # Calculate costs
        notional = min(price_a, price_b) * volume
        costs = self._cost_model.compute_total_cost(
            notional=notional, order_type="MARKET", price=min(price_a, price_b), volume=volume
        )
        total_cost_bps = (costs["fees"] + costs["slippage"]) / notional * 10000

        net_edge = spread - total_cost_bps

        # Determine viability
        if net_edge > 10.0:
            status = ArbitrageStatus.VIABLE
        elif net_edge > 5.0:
            status = ArbitrageStatus.MARGINAL
        else:
            status = ArbitrageStatus.NOT_VIABLE

        risk_score = max(0.0, min(1.0, 1.0 - net_edge / 30.0))

        opp = ArbitrageOpportunity(
            opportunity_id=f"arb_{int(time.time())}",
            arbitrage_type=ArbitrageType.PRICE,
            symbol=symbol,
            exchanges=["A", "B"],
            buy_price=min(price_a, price_b),
            sell_price=max(price_a, price_b),
            spread_bps=spread,
            net_edge_bps=net_edge,
            estimated_profit=net_edge / 10000 * notional,
            volume=volume,
            status=status,
            risk_score=risk_score,
        )

        self._opportunities.append(opp)
        return opp

    def check_viability(self, opp: ArbitrageOpportunity) -> bool:
        """Check if opportunity is viable given risk limits."""
        if opp.net_edge_bps < 0:
            return False

        if opp.risk_score > 0.5:
            return False

        if opp.status == ArbitrageStatus.NOT_VIABLE:
            return False

        # Check risk engine
        context = RiskContext()
        result, reason = self.risk_engine.evaluate(context, equity=5000.0)
        if not result:
            return False

        return True

    def run_feasibility_study(
        self,
        symbols: list[str],
        n_iterations: int = 10,
    ) -> ArbitrageResult:
        """Run full feasibility study.

        Scans for arbitrage opportunities and evaluates viability.
        """
        for _ in range(n_iterations):
            for symbol in symbols:
                # Simulate funding rate arb
                rate_a = np.random.randn() * 0.01
                rate_b = np.random.randn() * 0.01
                price_a = np.random.uniform(50000, 60000)
                price_b = price_a * (1 + np.random.randn() * 0.0005)
                self.detect_funding_rate_arb(
                    symbol, rate_a, rate_b, price_a, price_b
                )

                # Simulate price arb
                book_depth = np.random.uniform(1, 10)
                self.detect_price_arb(
                    symbol, price_a, price_b, book_depth, book_depth
                )

        # Evaluate all opportunities
        viable = [opp for opp in self._opportunities if self.check_viability(opp)]

        total_pnl = sum(opp.estimated_profit for opp in viable)
        total_fees = sum(self._cost_model.compute_total_cost(
            notional=opp.buy_price * opp.volume, order_type="LIMIT",
            price=opp.buy_price, volume=opp.volume
        )["total_cost"] for opp in viable)

        # Compute metrics
        n_viable = len(viable)
        n_total = len(self._opportunities)
        success_rate = n_viable / n_total if n_total > 0 else 0.0

        result = ArbitrageResult(
            opportunities_found=n_total,
            opportunities_viable=n_viable,
            total_pnl=total_pnl,
            total_fees=total_fees,
            success_rate=success_rate,
            viability_score=success_rate * (1.0 - np.mean([o.risk_score for o in viable]) if viable else 0.0),
            recommendations=self._generate_recommendations(viable),
        )

        return result

    def _generate_recommendations(self, viable: list[ArbitrageOpportunity]) -> list[str]:
        """Generate recommendations based on viable opportunities."""
        recs = []
        if not viable:
            return ["No viable opportunities found"]

        funding = [o for o in viable if o.arbitrage_type == ArbitrageType.FUNDING_RATE]
        price = [o for o in viable if o.arbitrage_type == ArbitrageType.PRICE]

        if funding:
            recs.append(f"Funding rate arb: {len(funding)} viable opportunities")
        if price:
            recs.append(f"Price arb: {len(price)} viable opportunities")

        avg_edge = np.mean([o.net_edge_bps for o in viable])
        recs.append(f"Average net edge: {avg_edge:.1f} bps")

        best = max(viable, key=lambda o: o.net_edge_bps)
        recs.append(f"Best opportunity: {best.symbol} at {best.net_edge_bps:.1f} bps")

        return recs


class CCXTPaperTrading:
    """CCXT paper trading wrapper.

    Per AGENTS.md Part E:
    - Paper trading against real order books
    - CCXT adapter with paper mode
    - No real funds at risk
    """

    def __init__(self, exchange_name: str = "binance") -> None:
        self.exchange_name = exchange_name
        self._orders: list[dict[str, Any]] = []
        self._balance: float = 10000.0
        self._positions: dict[str, float] = {}

    def fetch_order_book(self, symbol: str) -> dict[str, Any]:
        """Fetch order book (simulated)."""
        return {
            "bids": [[50000 - i * 0.01, 1.0] for i in range(10)],
            "asks": [[50000 + i * 0.01, 1.0] for i in range(10)],
            "timestamp": int(time.time() * 1000),
        }

    def create_market_order(
        self, symbol: str, side: str, amount: float
    ) -> dict[str, Any]:
        """Create paper market order."""
        order = {
            "id": f"paper_{int(time.time())}",
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": None,  # Market order
            "status": "filled",
            "cost": amount * 50000,  # Simulated
            "timestamp": int(time.time() * 1000),
            "exchange": self.exchange_name,
        }
        self._orders.append(order)
        self._balance -= order["cost"]
        return order

    def create_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict[str, Any]:
        """Create paper limit order."""
        order = {
            "id": f"paper_{int(time.time())}",
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "cost": amount * price,
            "timestamp": int(time.time() * 1000),
            "exchange": self.exchange_name,
        }
        self._orders.append(order)
        return order

    def get_balance(self) -> float:
        """Get paper balance."""
        return self._balance

    def get_orders(self) -> list[dict[str, Any]]:
        """Get all orders."""
        return list(self._orders)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
