"""Prop firm compliance engine — deterministic, fail-closed.

Per AGENTS.md A3 & A7:
- Kill switch / risk engine: deterministic code, absolute veto
- Execution layer cannot bypass risk; fails closed
- No live trading; DEMO-only
- Every decision gets a decision_id with full trace

FundingPips daily reset: UTC+3 midnight = 21:00 UTC.
  day_id = (timestamp - 21h).date()  in UTC

Daily reference equity = max(balance_at_day_open, equity_at_day_open)
  This matches FundingPips documented rule.

State is persisted to JSON so restarts do not reset a breached limit.
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from engine.prop_firm_rules import PropFirmRuleProfile, RuleSourceStatus

logger = logging.getLogger("quant_lab.compliance")


# ---------------------------------------------------------------------------
# State enums
# ---------------------------------------------------------------------------

class ComplianceState(StrEnum):
    """Overall compliance state of the challenge account."""
    COMPLIANT = "COMPLIANT"                    # All limits healthy
    WARNING = "WARNING"                        # Approaching a limit
    NO_TRADE = "NO_TRADE"                      # Soft: no new entries; hold existing OK
    HALTED = "HALTED"                          # Hard: all actions frozen
    BREACHED = "BREACHED"                      # Rule violated — challenge failed
    PHASE_TARGET_REACHED = "PHASE_TARGET_REACHED"  # Profit target met (pending human review)


class VerdictCode(StrEnum):
    """Why a trade was approved or blocked."""
    APPROVED = "APPROVED"
    DAILY_LIMIT_BREACHED = "DAILY_LIMIT_BREACHED"
    TOTAL_LIMIT_BREACHED = "TOTAL_LIMIT_BREACHED"
    CHALLENGE_FAILED = "CHALLENGE_FAILED"
    UNVERIFIED_RULES = "UNVERIFIED_RULES"
    HALT_ACTIVE = "HALT_ACTIVE"
    RISK_EXCEEDS_DAILY_ROOM = "RISK_EXCEEDS_DAILY_ROOM"
    RISK_EXCEEDS_TOTAL_ROOM = "RISK_EXCEEDS_TOTAL_ROOM"
    PHASE_TARGET_MET = "PHASE_TARGET_MET"
    INACTIVITY_DEADLINE = "INACTIVITY_DEADLINE"
    SAFETY_BUFFER_VIOLATION = "SAFETY_BUFFER_VIOLATION"


# ---------------------------------------------------------------------------
# Verdict and state containers
# ---------------------------------------------------------------------------

@dataclass
class ComplianceVerdict:
    """Result of a compliance check for a proposed trade."""
    can_proceed: bool
    code: VerdictCode
    reason: str
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    daily_loss_used_pct: float = 0.0
    total_loss_used_pct: float = 0.0
    daily_room_remaining: float = 0.0    # $ dollars of room left today
    total_room_remaining: float = 0.0    # $ dollars of room left overall
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DaySnapshot:
    """Saved state for one FundingPips trading day."""
    day_id: str                  # ISO date string (FundingPips day: shifted -21h)
    opening_balance: float
    opening_equity: float
    daily_reference: float       # max(opening_balance, opening_equity)
    realized_pnl: float = 0.0
    trades_taken: int = 0


@dataclass
class ComplianceSnapshot:
    """Persisted state across restarts."""
    profile_id: str
    initial_balance: float
    peak_balance: float
    current_balance: float
    trading_days: int
    account_created_ts: str | None       # ISO UTC; drives inactivity before first close
    first_trade_ts: str | None           # ISO UTC
    last_trade_ts: str | None            # ISO UTC
    state: str                           # ComplianceState value
    breach_reason: str | None
    current_day: DaySnapshot | None
    total_realized_pnl: float = 0.0
    peak_equity: float = 0.0


# ---------------------------------------------------------------------------
# Main compliance engine
# ---------------------------------------------------------------------------

class PropFirmComplianceEngine:
    """Deterministic prop firm compliance enforcer.

    Responsibilities:
    1. Track balance, equity, daily reference, drawdown in real time.
    2. Block any trade that would breach daily or total loss limits.
    3. Apply a safety_buffer_pct cushion INSIDE the hard limits.
    4. Detect phase target reached (reporting only; human decides next step).
    5. Detect inactivity deadline breaches.
    6. Persist state to JSON so restarts cannot reset a breached limit.
    7. Block UNVERIFIED rule profiles from enabling order execution.

    Thread-safety note: single-threaded; protect externally if needed.
    """

    WARN_THRESHOLD = 0.70   # Fraction of daily/total room consumed triggers WARNING

    def __init__(
        self,
        rule_profile: PropFirmRuleProfile,
        state_path: Path | str | None = None,
        safety_buffer_pct: float = 0.005,  # 0.5% of initial balance as inner buffer
        environment: str = "MT5_DEMO",
        warn_threshold_pct: float = 0.70,
        account_created_ts: datetime | None = None,
    ) -> None:
        if not 0 <= rule_profile.daily_reset_utc_hour <= 23:
            raise ValueError(
                f"daily_reset_utc_hour must be 0..23, got {rule_profile.daily_reset_utc_hour}"
            )
        if rule_profile.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0 < safety_buffer_pct < 0.05:
            raise ValueError("safety_buffer_pct must be between 0 and 0.05")
        if not 0 < warn_threshold_pct < 1:
            raise ValueError("warn_threshold_pct must be between 0 and 1")

        self.profile = rule_profile
        self.safety_buffer_pct = safety_buffer_pct
        self.state_path = Path(state_path) if state_path else None
        self.environment = environment
        self.warn_threshold = warn_threshold_pct
        self._account_created_ts = (
            self._ensure_utc(account_created_ts)
            if account_created_ts is not None
            else datetime.now(UTC)
        )

        # Load or initialise persisted state
        self._snapshot = self._load_or_init()
        self._state = ComplianceState(self._snapshot.state)

        # Real-time floating P&L not in snapshot (updated every bar)
        self._floating_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> ComplianceState:
        return self._state

    @property
    def current_balance(self) -> float:
        return self._snapshot.current_balance

    @property
    def current_equity(self) -> float:
        return self._snapshot.current_balance + self._floating_pnl

    @property
    def trading_days(self) -> int:
        return self._snapshot.trading_days

    @property
    def daily_loss(self) -> float:
        return self._daily_loss()

    @property
    def total_loss(self) -> float:
        return self._total_loss()

    def update_equity(self, balance: float, equity: float, timestamp: datetime) -> None:
        """Called every bar/tick with the latest broker balance and equity.

        Args:
            balance:   Realised balance (closed trade P&L settled).
            equity:    Balance + unrealised floating P&L.
            timestamp: UTC-aware datetime of this update.
        """
        if not (math.isfinite(balance) and math.isfinite(equity)):
            raise ValueError(
                f"Non-finite broker values: balance={balance!r}, equity={equity!r}"
            )

        if self._state in (ComplianceState.BREACHED, ComplianceState.HALTED):
            # Already failed; do not update further.
            return

        ts_utc = self._ensure_utc(timestamp)
        day_id = self._fundingpips_day_id(ts_utc)

        # Initialise or roll the day
        self._maybe_new_day(day_id, balance, equity)

        # Update current balance / floating P&L consistently
        self._snapshot.current_balance = balance
        self._floating_pnl = equity - balance

        # Peak equity is only needed if a trailing overall limit is ever used
        if equity > self._snapshot.peak_equity:
            self._snapshot.peak_equity = equity
        if balance > self._snapshot.peak_balance:
            self._snapshot.peak_balance = balance

        # Check hard limits (floating loss counts immediately)
        self._check_hard_limits(equity, day_id)

        self._persist()

    def check_trade_eligible(
        self,
        risk_amount: float,  # $ at risk on this trade (worst-case loss including SL)
        open_risk_amount: float = 0.0,  # worst-case loss of currently open positions
        now: datetime | None = None,
    ) -> ComplianceVerdict:
        """Check whether a new trade is compliant with all rules.

        This is called BEFORE every order submission.
        Fails closed: any uncertainty → block.

        Args:
            risk_amount: Maximum loss in $ if the trade hits its stop loss,
                         INCLUDING commission and estimated slippage.
            open_risk_amount: Worst-case loss still available on open positions.
            now: UTC-aware time used for the inactivity check; defaults to now.
        """
        decision_id = str(uuid.uuid4())

        if not math.isfinite(risk_amount) or risk_amount <= 0:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.RISK_EXCEEDS_DAILY_ROOM,
                reason=f"Invalid risk_amount: {risk_amount!r}",
                decision_id=decision_id,
            )
        if not math.isfinite(open_risk_amount) or open_risk_amount < 0:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.RISK_EXCEEDS_DAILY_ROOM,
                reason=f"Invalid open_risk_amount: {open_risk_amount!r}",
                decision_id=decision_id,
            )

        # 1. Block UNVERIFIED or CONTRADICTED profiles absolutely
        if self.profile.source_status in (
            RuleSourceStatus.UNVERIFIED,
            RuleSourceStatus.CONTRADICTED,
        ):
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.UNVERIFIED_RULES,
                reason=(
                    f"Profile '{self.profile.profile_id}' is "
                    f"{self.profile.source_status.value}. "
                    "Verify rules from official source before enabling execution."
                ),
                decision_id=decision_id,
            )

        # 2. Already breached / halted
        if self._state == ComplianceState.BREACHED:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.CHALLENGE_FAILED,
                reason=f"Challenge already breached: {self._snapshot.breach_reason}",
                decision_id=decision_id,
            )
        if self._state == ComplianceState.HALTED:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.HALT_ACTIVE,
                reason="Compliance engine is HALTED.",
                decision_id=decision_id,
            )
        if self._state == ComplianceState.PHASE_TARGET_REACHED:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.PHASE_TARGET_MET,
                reason="Phase profit target reached. No new trades until human review.",
                decision_id=decision_id,
            )

        # 3. Inactivity check
        inactivity_verdict = self._check_inactivity(now)
        if inactivity_verdict is not None:
            return inactivity_verdict

        # 4. Compute available room
        day = self._snapshot.current_day
        if day is None:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.HALT_ACTIVE,
                reason="Daily state not initialised; call update_equity first.",
                decision_id=decision_id,
            )

        ib = self._snapshot.initial_balance
        safety_buf = ib * self.safety_buffer_pct

        # Daily limit is a percentage of THIS day's reference (the higher of
        # opening balance and opening equity), not of the initial balance.
        max_daily_loss = day.daily_reference * self.profile.max_daily_loss_pct
        daily_used = self._daily_loss()
        daily_room = max_daily_loss - daily_used - open_risk_amount - safety_buf

        # Total room: static from initial balance (or trailing peak equity if set)
        max_total_loss = ib * self.profile.max_total_loss_pct
        total_used = self._total_loss()
        total_room = max_total_loss - total_used - open_risk_amount - safety_buf

        daily_loss_used_pct = daily_used / max_daily_loss if max_daily_loss > 0 else 0.0
        total_loss_used_pct = total_used / max_total_loss if max_total_loss > 0 else 0.0

        # 5. Will this trade fit in the daily room?
        if risk_amount > daily_room:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.RISK_EXCEEDS_DAILY_ROOM,
                reason=(
                    f"Trade risk ${risk_amount:.2f} exceeds daily room "
                    f"${daily_room:.2f} (limit ${max_daily_loss:.2f}, "
                    f"used ${daily_used:.2f}, open risk ${open_risk_amount:.2f}, "
                    f"buffer ${safety_buf:.2f})"
                ),
                decision_id=decision_id,
                daily_loss_used_pct=daily_loss_used_pct,
                total_loss_used_pct=total_loss_used_pct,
                daily_room_remaining=max(0.0, daily_room),
                total_room_remaining=max(0.0, total_room),
            )

        # 6. Will this trade fit in the total room?
        if risk_amount > total_room:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.RISK_EXCEEDS_TOTAL_ROOM,
                reason=(
                    f"Trade risk ${risk_amount:.2f} exceeds total room "
                    f"${total_room:.2f} (max_total ${max_total_loss:.2f}, "
                    f"used ${total_used:.2f}, open risk ${open_risk_amount:.2f}, "
                    f"buffer ${safety_buf:.2f})"
                ),
                decision_id=decision_id,
                daily_loss_used_pct=daily_loss_used_pct,
                total_loss_used_pct=total_loss_used_pct,
                daily_room_remaining=max(0.0, daily_room),
                total_room_remaining=max(0.0, total_room),
            )

        # 7. APPROVED
        return ComplianceVerdict(
            can_proceed=True,
            code=VerdictCode.APPROVED,
            reason="All compliance checks passed.",
            decision_id=decision_id,
            daily_loss_used_pct=daily_loss_used_pct,
            total_loss_used_pct=total_loss_used_pct,
            daily_room_remaining=daily_room,
            total_room_remaining=total_room,
            details={
                "risk_amount": risk_amount,
                "open_risk_amount": open_risk_amount,
                "daily_room": daily_room,
                "total_room": total_room,
            },
        )

    def record_trade_closed(
        self,
        realized_pnl: float,
        timestamp: datetime,
        open_positions: int = 0,
    ) -> None:
        """Record a closed trade's realized P&L.

        Call this whenever a position is fully or partially closed.

        Args:
            realized_pnl: Net P&L (positive=win, negative=loss). Include fees.
            timestamp:     UTC-aware datetime of the close.
            open_positions: Positions still open after this close. The phase
                            target is not declared while any position remains open.
        """
        if not math.isfinite(realized_pnl):
            raise ValueError("realized_pnl must be finite")
        if not isinstance(open_positions, int) or open_positions < 0:
            raise ValueError(f"open_positions must be a non-negative int, got {open_positions!r}")
        ts_utc = self._ensure_utc(timestamp)
        day_id = self._fundingpips_day_id(ts_utc)

        # Roll the day using the equity observed just before this close settles.
        # A close that arrives just after the reset must not hide its own loss
        # from the new day's reference.
        balance_before = self._snapshot.current_balance
        equity_before = balance_before + self._floating_pnl
        self._maybe_new_day(day_id, balance_before, equity_before)

        day = self._snapshot.current_day
        if day is not None:
            day.realized_pnl += realized_pnl
            day.trades_taken += 1

        self._snapshot.current_balance += realized_pnl
        self._snapshot.total_realized_pnl += realized_pnl
        self._snapshot.last_trade_ts = ts_utc.isoformat()

        if self._snapshot.first_trade_ts is None:
            self._snapshot.first_trade_ts = ts_utc.isoformat()

        # Count the first close on each FundingPips day as a trading day.
        if day is not None and day.trades_taken == 1:
            self._snapshot.trading_days += 1

        # When flat, the broker-reported balance is the complete equity. With
        # positions still open the floating figure is stale until the caller's
        # next update_equity(); pass open_positions=0 on a full flatten.
        if open_positions == 0:
            self._floating_pnl = 0.0
        current_equity = self._snapshot.current_balance + self._floating_pnl
        if current_equity > self._snapshot.peak_equity:
            self._snapshot.peak_equity = current_equity
        if self._snapshot.current_balance > self._snapshot.peak_balance:
            self._snapshot.peak_balance = self._snapshot.current_balance

        # Re-check limits using live equity
        self._check_hard_limits(current_equity, day_id)

        # Check phase target only when flat
        self._check_phase_target(open_positions=open_positions)

        self._persist()

    def generate_report(self) -> dict[str, Any]:
        """Generate a compliance status report."""
        ib = self._snapshot.initial_balance
        day = self._snapshot.current_day
        daily_reference = day.daily_reference if day is not None else ib
        max_daily = daily_reference * self.profile.max_daily_loss_pct
        max_total = ib * self.profile.max_total_loss_pct

        daily_exposure = self._daily_loss()
        total_exposure = self._total_loss()
        safety_buf = ib * self.safety_buffer_pct

        return {
            "profile_id": self.profile.profile_id,
            "display_name": self.profile.display_name,
            "source_status": self.profile.source_status.value,
            "state": self._state.value,
            "breach_reason": self._snapshot.breach_reason,
            "initial_balance": ib,
            "current_balance": self._snapshot.current_balance,
            "current_equity": self.current_equity,
            "total_realized_pnl": self._snapshot.total_realized_pnl,
            "floating_pnl": self._floating_pnl,
            "peak_balance": self._snapshot.peak_balance,
            "peak_equity": self._snapshot.peak_equity,
            "trading_days": self._snapshot.trading_days,
            "min_trading_days_required": self.profile.min_trading_days,
            "trading_days_met": self._snapshot.trading_days >= self.profile.min_trading_days,
            "last_trade_ts": self._snapshot.last_trade_ts,
            "profit_target_pct": self.profile.profit_target_pct,
            "profit_target_usd": ib * self.profile.profit_target_pct,
            "profit_achieved_usd": self._snapshot.current_balance - ib,
            "profit_achieved_pct": (self._snapshot.current_balance - ib) / ib if ib > 0 else 0.0,
            "max_daily_loss_pct": self.profile.max_daily_loss_pct,
            "daily_limit_reference_usd": daily_reference,
            "max_daily_loss_usd": max_daily,
            "daily_exposure_usd": daily_exposure,
            "daily_exposure_pct": daily_exposure / ib if ib > 0 else 0.0,
            "daily_room_usd": max(0.0, max_daily - daily_exposure),
            "max_total_loss_pct": self.profile.max_total_loss_pct,
            "max_total_loss_usd": max_total,
            "total_exposure_usd": total_exposure,
            "total_exposure_pct": total_exposure / ib if ib > 0 else 0.0,
            "total_room_usd": max(0.0, max_total - total_exposure),
            "safety_buffer_usd": safety_buf,
            "today": {
                "day_id": day.day_id if day else None,
                "daily_reference": day.daily_reference if day else None,
                "realized_pnl": day.realized_pnl if day else 0.0,
                "trades_taken": day.trades_taken if day else 0,
            },
            "environment": self.environment,
            "caveats": [
                "All compliance checks are advisory for demo trading.",
                "Challenge PASS requires human verification; never declared locally.",
                f"Source status: {self.profile.source_status.value}",
            ],
        }

    def halt(self, reason: str = "Manual halt") -> None:
        """Manually halt the compliance engine. Never downgrades a breach."""
        if self._state == ComplianceState.BREACHED:
            logger.warning(
                "Halt requested but challenge is already BREACHED; state unchanged"
            )
            return
        self._state = ComplianceState.HALTED
        self._snapshot.state = ComplianceState.HALTED.value
        self._snapshot.breach_reason = reason
        logger.warning("Compliance engine HALTED: %s", reason)
        self._persist()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fundingpips_day_id(self, ts: datetime) -> str:
        """FundingPips calendar day ID using the profile's reset hour.

        With a 00:00 UTC+3 reset, reset_hour=21. Shifting back 21 hours maps
        21:00 UTC (day N) through 20:59 UTC (day N+1) to day N.
        """
        shifted = ts - timedelta(hours=self.profile.daily_reset_utc_hour)
        return shifted.date().isoformat()

    @staticmethod
    def _ensure_utc(ts: datetime) -> datetime:
        """Require a timezone-aware timestamp and normalise it to UTC."""
        if not isinstance(ts, datetime):
            raise TypeError(f"timestamp must be datetime, got {type(ts)!r}")
        if ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return ts.astimezone(UTC)

    def _maybe_new_day(self, day_id: str, balance: float, equity: float) -> None:
        """Roll to a new FundingPips trading day if necessary."""
        current_day = self._snapshot.current_day
        if current_day is None or current_day.day_id != day_id:
            daily_ref = max(balance, equity)
            self._snapshot.current_day = DaySnapshot(
                day_id=day_id,
                opening_balance=balance,
                opening_equity=equity,
                daily_reference=daily_ref,
                realized_pnl=0.0,
                trades_taken=0,
            )
            logger.debug("New FundingPips day: %s, daily_reference=%.2f", day_id, daily_ref)

    def _daily_loss(self) -> float:
        """Current daily loss from the day's reference, including floating P&L."""
        day = self._snapshot.current_day
        if day is None:
            return 0.0
        return max(0.0, day.daily_reference - self.current_equity)

    def _total_loss(self) -> float:
        """Current overall loss from the static initial balance.

        If a profile ever sets trailing_total, the reference becomes peak equity.
        """
        reference = (
            self._snapshot.peak_equity
            if self.profile.trailing_total
            else self._snapshot.initial_balance
        )
        return max(0.0, reference - self.current_equity)

    def _check_hard_limits(self, equity: float, day_id: str) -> None:
        """Check and enforce hard daily and total loss limits."""
        if self._state in (ComplianceState.BREACHED, ComplianceState.HALTED):
            return

        ib = self._snapshot.initial_balance
        day = self._snapshot.current_day

        # ── Daily loss limit ──
        daily_exposure = (
            max(0.0, day.daily_reference - equity) if day is not None else 0.0
        )
        daily_reference = day.daily_reference if day is not None else ib
        max_daily_loss = daily_reference * self.profile.max_daily_loss_pct

        if daily_exposure >= max_daily_loss:
            self._breach(
                f"Daily loss limit breached: "
                f"exposure={daily_exposure:.2f}, limit={max_daily_loss:.2f} "
                f"(reference={daily_reference:.2f}) on day {day_id}"
            )
            return

        # ── Total loss limit ──
        total_reference = (
            self._snapshot.peak_equity
            if self.profile.trailing_total
            else ib
        )
        effective_total_loss = max(0.0, total_reference - equity)
        max_total_loss = ib * self.profile.max_total_loss_pct

        if effective_total_loss >= max_total_loss:
            self._breach(
                f"Total loss limit breached: "
                f"effective_total_loss={effective_total_loss:.2f}, "
                f"limit={max_total_loss:.2f}"
            )
            return

        # ── Update warning state ──
        daily_fraction = daily_exposure / max_daily_loss if max_daily_loss > 0 else 0
        total_fraction = effective_total_loss / max_total_loss if max_total_loss > 0 else 0

        if daily_fraction >= self.warn_threshold or total_fraction >= self.warn_threshold:
            if self._state == ComplianceState.COMPLIANT:
                self._state = ComplianceState.WARNING
                logger.warning(
                    "Compliance WARNING: daily=%.1f%% total=%.1f%% of limits used",
                    daily_fraction * 100,
                    total_fraction * 100,
                )
        elif self._state == ComplianceState.WARNING:
            self._state = ComplianceState.COMPLIANT

    def _check_phase_target(self, open_positions: int = 0) -> None:
        """Check if the profit target for the current phase has been reached."""
        if self._state in (ComplianceState.BREACHED, ComplianceState.HALTED,
                           ComplianceState.PHASE_TARGET_REACHED):
            return

        ib = self._snapshot.initial_balance
        profit = self._snapshot.current_balance - ib
        target = ib * self.profile.profit_target_pct

        if (
            open_positions == 0
            and profit >= target
            and self._snapshot.trading_days >= self.profile.min_trading_days
        ):
            self._state = ComplianceState.PHASE_TARGET_REACHED
            self._snapshot.state = self._state.value
            logger.info(
                "PHASE_TARGET_REACHED: profit=%.2f >= target=%.2f, "
                "trading_days=%d >= min=%d. Pending human review.",
                profit, target,
                self._snapshot.trading_days, self.profile.min_trading_days,
            )
            self._persist()

    def _check_inactivity(self, now: datetime | None = None) -> ComplianceVerdict | None:
        """Check if the inactivity deadline has been breached."""
        if self.profile.inactivity_max_days <= 0:
            return None
        # FundingPips starts the clock at account creation if no trade has
        # completed yet, then at the day after the last fully closed trade.
        anchor_ts = (
            self._snapshot.last_trade_ts or self._snapshot.account_created_ts
        )
        if anchor_ts is None:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.INACTIVITY_DEADLINE,
                reason=(
                    "Inactivity clock cannot be established: no account creation "
                    "or completed-trade timestamp is persisted."
                ),
            )
        last_trade = datetime.fromisoformat(anchor_ts)
        if last_trade.tzinfo is None:
            last_trade = last_trade.replace(tzinfo=UTC)
        now_utc = self._ensure_utc(now) if now is not None else datetime.now(UTC)
        days_idle = (now_utc - last_trade).days
        if days_idle >= self.profile.inactivity_max_days:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.INACTIVITY_DEADLINE,
                reason=(
                    f"Inactivity limit exceeded: {days_idle} days since last trade "
                    f"(max {self.profile.inactivity_max_days})."
                ),
            )
        return None

    def _breach(self, reason: str) -> None:
        """Mark the challenge as breached (irreversible)."""
        self._state = ComplianceState.BREACHED
        self._snapshot.state = ComplianceState.BREACHED.value
        self._snapshot.breach_reason = reason
        logger.critical("COMPLIANCE BREACH: %s", reason)
        self._persist()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_or_init(self) -> ComplianceSnapshot:
        """Load persisted state from disk, or create a fresh snapshot.

        Fails closed: a corrupt state file, a different profile, or a different
        starting balance raises instead of silently resetting a breached account.
        """
        if not self.state_path or not self.state_path.exists():
            return self._fresh_snapshot()

        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            current_day = None
            if data.get("current_day"):
                current_day = DaySnapshot(**data["current_day"])
            snap = ComplianceSnapshot(
                profile_id=data["profile_id"],
                initial_balance=float(data["initial_balance"]),
                peak_balance=float(data["peak_balance"]),
                current_balance=float(data["current_balance"]),
                trading_days=int(data["trading_days"]),
                account_created_ts=(
                    data.get("account_created_ts")
                    or data.get("first_trade_ts")
                    or data.get("last_trade_ts")
                ),
                first_trade_ts=data.get("first_trade_ts"),
                last_trade_ts=data.get("last_trade_ts"),
                state=data["state"],
                breach_reason=data.get("breach_reason"),
                current_day=current_day,
                total_realized_pnl=float(data.get("total_realized_pnl", 0.0)),
                peak_equity=float(
                    data.get("peak_equity", data["initial_balance"])
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Compliance state at {self.state_path} is unreadable; refusing to trade"
            ) from exc

        if snap.profile_id != self.profile.profile_id:
            raise RuntimeError(
                f"Persisted profile '{snap.profile_id}' != configured profile "
                f"'{self.profile.profile_id}'; refusing to reuse state"
            )
        if abs(snap.initial_balance - self.profile.initial_balance) > 1e-9:
            raise RuntimeError(
                f"Persisted initial balance {snap.initial_balance} != configured "
                f"{self.profile.initial_balance}; refusing to reuse state"
            )
        try:
            ComplianceState(snap.state)
        except ValueError as exc:
            raise RuntimeError(
                f"Persisted compliance state '{snap.state}' is invalid"
            ) from exc
        if not math.isfinite(snap.current_balance):
            raise RuntimeError("Persisted balance is not finite")
        if not math.isfinite(snap.peak_balance) or not math.isfinite(snap.peak_equity):
            raise RuntimeError("Persisted peak balance/equity is not finite")
        if not math.isfinite(snap.total_realized_pnl):
            raise RuntimeError("Persisted total realized P&L is not finite")
        if snap.current_day is not None:
            day = snap.current_day
            if not isinstance(day.day_id, str) or not math.isfinite(day.daily_reference):
                raise RuntimeError("Persisted current day is invalid")
            if not math.isfinite(day.opening_balance) or not math.isfinite(day.opening_equity):
                raise RuntimeError("Persisted day opening values are not finite")
        if snap.account_created_ts is None:
            raise RuntimeError(
                "Persisted state has no account creation or trade timestamp; "
                "inactivity cannot be evaluated"
            )

        logger.info(
            "Loaded compliance state from %s: state=%s",
            self.state_path, snap.state,
        )
        return snap

    def _fresh_snapshot(self) -> ComplianceSnapshot:
        ib = self.profile.initial_balance
        return ComplianceSnapshot(
            profile_id=self.profile.profile_id,
            initial_balance=ib,
            peak_balance=ib,
            current_balance=ib,
            trading_days=0,
            account_created_ts=self._account_created_ts.isoformat(),
            first_trade_ts=None,
            last_trade_ts=None,
            state=ComplianceState.COMPLIANT.value,
            breach_reason=None,
            current_day=None,
            total_realized_pnl=0.0,
            peak_equity=ib,
        )

    def _persist(self) -> None:
        """Atomically persist state; raise if the state cannot be trusted."""
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {
                "profile_id": self._snapshot.profile_id,
                "initial_balance": self._snapshot.initial_balance,
                "peak_balance": self._snapshot.peak_balance,
                "peak_equity": self._snapshot.peak_equity,
                "current_balance": self._snapshot.current_balance,
                "trading_days": self._snapshot.trading_days,
                "account_created_ts": self._snapshot.account_created_ts,
                "first_trade_ts": self._snapshot.first_trade_ts,
                "last_trade_ts": self._snapshot.last_trade_ts,
                "state": self._snapshot.state,
                "breach_reason": self._snapshot.breach_reason,
                "total_realized_pnl": self._snapshot.total_realized_pnl,
                "current_day": (
                    asdict(self._snapshot.current_day)
                    if self._snapshot.current_day else None
                ),
                "_written_utc": datetime.now(UTC).isoformat(),
            }
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except Exception as exc:
            if self._state != ComplianceState.BREACHED:
                self._state = ComplianceState.HALTED
                self._snapshot.state = ComplianceState.HALTED.value
            raise RuntimeError(
                f"Failed to persist compliance state to {self.state_path}"
            ) from exc
