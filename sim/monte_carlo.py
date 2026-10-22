"""Prop-firm survival Monte Carlo using an observed net-R distribution."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from risk.profiles import RiskProfile


@dataclass(frozen=True)
class MonteCarloResult:
    simulations: int
    pass_probability: float
    daily_loss_probability: float
    max_drawdown_probability: float
    median_days_to_pass: float | None
    analytical_probability: float
    rules_enabled: bool
    assumptions: tuple[str, ...]


def analytical_pass_probability(mu: float, sigma: float, target: float, drawdown: float) -> float:
    if sigma <= 0 or target <= 0 or drawdown <= 0:
        raise ValueError("sigma, target, and drawdown must be positive")
    if abs(mu) < 1e-15:
        return target / (target + drawdown)
    numerator = 1 - math.exp(-2 * mu * target / sigma**2)
    denominator = 1 - math.exp(-2 * mu * (drawdown + target) / sigma**2)
    return numerator / denominator if denominator else 0.0


def run_monte_carlo(
    net_r: np.ndarray,
    profile: RiskProfile,
    *,
    simulations: int = 10_000,
    trades_per_day: int = 3,
    seed: int = 0,
    rules_enabled: bool = True,
    block_size: int | None = None,
) -> MonteCarloResult:
    """Return survival probabilities; every R is scaled by risk per trade."""
    values = np.asarray(net_r, dtype=float)
    if values.size == 0 or simulations < 1 or trades_per_day < 1:
        raise ValueError("net_r, simulations, and trades_per_day must be positive")
    rng = np.random.default_rng(seed)
    target, drawdown = profile.profit_target, profile.max_drawdown
    mu = float(values.mean() * profile.risk_per_trade)
    sigma = float(values.std(ddof=1) * profile.risk_per_trade) if len(values) > 1 else 0.0
    analytical = analytical_pass_probability(mu, sigma if sigma else 1e-12, target, drawdown)
    passed = daily_breached = dd_breached = 0
    days_to_pass: list[int] = []
    for _ in range(simulations):
        equity = peak = 1.0
        day_pnl = 0.0
        trades = day = 0
        locked = False
        while day < 365 and not locked and equity < 1.0 + target:
            day += 1
            day_pnl = 0.0
            for _trade in range(trades_per_day):
                if rules_enabled and trades >= profile.max_trades_per_day:
                    break
                if block_size and block_size > 1:
                    block = values[rng.integers(0, len(values), size=block_size)]
                    r = float(block[_trade % block_size])
                else:
                    r = float(values[rng.integers(0, len(values))])
                pnl = r * profile.risk_per_trade
                equity += pnl
                day_pnl += pnl
                trades += 1
                peak = max(peak, equity)
                if day_pnl <= -profile.daily_loss_limit:
                    daily_breached += 1
                    locked = True
                    break
                if peak - equity >= profile.max_drawdown:
                    dd_breached += 1
                    locked = True
                    break
                if rules_enabled and day_pnl <= -profile.daily_loss_limit * 0.5:
                    locked = True
                    break
            if locked:
                break
        if equity >= 1.0 + target and not locked:
            passed += 1
            days_to_pass.append(day)
    return MonteCarloResult(simulations, passed / simulations, daily_breached / simulations,
                            dd_breached / simulations, float(np.median(days_to_pass)) if days_to_pass else None,
                            analytical, rules_enabled,
                            ("Risk per trade is applied to net R.", "No data-driven rule parameters were tuned."))
