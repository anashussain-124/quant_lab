"""Explicit risk profiles and deterministic rule enforcement."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskProfile:
    name: str
    risk_per_trade: float
    max_trades_per_day: int
    profit_target: float
    max_drawdown: float
    daily_loss_limit: float
    min_trading_days: int
    source_status: str


FUNDINGPIPS_STANDARD_PHASE1 = RiskProfile(
    "fundingpips_2step_standard_phase1", 0.0025, 8, 0.08, 0.10, 0.05, 5, "DOCUMENTED"
)


@dataclass
class RuleState:
    day: int = 0
    day_pnl: float = 0.0
    equity: float = 1.0
    peak_equity: float = 1.0
    trades_today: int = 0
    trading_days: int = 0
    locked: bool = False


class RuleEngine:
    """Enforce loss lockouts and monotone stop tightening."""

    def __init__(self, profile: RiskProfile, lockout_fraction: float = 0.5) -> None:
        if not 0 < lockout_fraction <= 1:
            raise ValueError("lockout fraction must be in (0, 1]")
        self.profile = profile
        self.lockout_limit = profile.daily_loss_limit * lockout_fraction
        self.state = RuleState()
        self._stop: float | None = None

    def new_day(self, day: int) -> None:
        if day != self.state.day:
            if self.state.trades_today:
                self.state.trading_days += 1
            self.state.day = day
            self.state.day_pnl = 0.0
            self.state.trades_today = 0
            self.state.locked = False

    def allow_trade(self) -> bool:
        return (not self.state.locked and self.state.trades_today < self.profile.max_trades_per_day
                and self.state.equity - self.state.peak_equity > -self.profile.max_drawdown)

    def record(self, pnl: float) -> None:
        self.state.day_pnl += pnl
        self.state.equity += pnl
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
        self.state.trades_today += 1
        if self.state.day_pnl <= -self.lockout_limit or self.state.peak_equity - self.state.equity >= self.profile.max_drawdown:
            self.state.locked = True

    def tighten_stop(self, proposed: float) -> float:
        if self._stop is None:
            self._stop = proposed
        else:
            self._stop = max(self._stop, proposed)
        return self._stop


def daily_loss_basis(balance_at_open: float, equity_at_open: float, basis: str) -> float:
    """Return the configured daily-loss reference; unknown modes fail closed."""
    if basis == "equity":
        return equity_at_open
    if basis == "max_balance_equity":
        return max(balance_at_open, equity_at_open)
    raise ValueError(f"unknown daily-loss basis: {basis}")


@dataclass
class TrailingDrawdown:
    limit: float
    high_water_mark: float

    def update(self, equity: float) -> bool:
        self.high_water_mark = max(self.high_water_mark, equity)
        return self.high_water_mark - equity >= self.limit
