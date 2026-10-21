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
import uuid
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from engine.prop_firm_rules import PropFirmRuleProfile, RuleSourceStatus

logger = logging.getLogger("quant_lab.compliance")


# ---------------------------------------------------------------------------
# State enums
# ---------------------------------------------------------------------------

class ComplianceState(str, Enum):
    """Overall compliance state of the challenge account."""
    COMPLIANT = "COMPLIANT"                    # All limits healthy
    WARNING = "WARNING"                        # Approaching a limit
    NO_TRADE = "NO_TRADE"                      # Soft: no new entries; hold existing OK
    HALTED = "HALTED"                          # Hard: all actions frozen
    BREACHED = "BREACHED"                      # Rule violated — challenge failed
    PHASE_TARGET_REACHED = "PHASE_TARGET_REACHED"  # Profit target met (pending human review)


class VerdictCode(str, Enum):
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
    first_trade_ts: str | None           # ISO UTC
    last_trade_ts: str | None            # ISO UTC
    state: str                           # ComplianceState value
    breach_reason: str | None
    current_day: DaySnapshot | None
    total_realized_pnl: float = 0.0


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
    ) -> None:
        self.profile = rule_profile
        self.safety_buffer_pct = safety_buffer_pct
        self.state_path = Path(state_path) if state_path else None

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
    def trading_days(self) -> int:
        return self._snapshot.trading_days

    def update_equity(self, balance: float, equity: float, timestamp: datetime) -> None:
        """Called every bar/tick with the latest broker balance and equity.

        Args:
            balance:   Realised balance (closed trade P&L settled).
            equity:    Balance + unrealised floating P&L.
            timestamp: UTC-aware datetime of this update.
        """
        if self._state in (ComplianceState.BREACHED, ComplianceState.HALTED):
            # Already failed; do not update further.
            return

        ts_utc = self._ensure_utc(timestamp)
        day_id = self._fundingpips_day_id(ts_utc)

        # Initialise or roll the day
        self._maybe_new_day(day_id, balance, equity)

        # Update current balance
        self._snapshot.current_balance = balance
        self._floating_pnl = equity - balance

        # Update peak balance for total drawdown calculation
        if balance > self._snapshot.peak_balance:
            self._snapshot.peak_balance = balance

        # Check hard limits (floating loss counts immediately)
        self._check_hard_limits(equity, day_id)

        self._persist()

    def check_trade_eligible(
        self,
        risk_amount: float,  # $ at risk on this trade (worst-case loss including SL)
    ) -> ComplianceVerdict:
        """Check whether a new trade is compliant with all rules.

        This is called BEFORE every order submission.
        Fails closed: any uncertainty → block.

        Args:
            risk_amount: Maximum loss in $ if the trade hits its stop loss,
                         INCLUDING commission and estimated slippage.
        """
        decision_id = str(uuid.uuid4())

        # 1. Block UNVERIFIED profiles absolutely
        if self.profile.source_status == RuleSourceStatus.UNVERIFIED:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.UNVERIFIED_RULES,
                reason=(
                    f"Profile '{self.profile.profile_id}' is UNVERIFIED. "
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
        inactivity_verdict = self._check_inactivity()
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

        # Daily room
        max_daily_loss = ib * self.profile.max_daily_loss_pct
        total_daily_exposure = self._current_daily_exposure()
        daily_room = max_daily_loss - total_daily_exposure - safety_buf

        # Total room
        max_total_loss = ib * self.profile.max_total_loss_pct
        total_exposure_so_far = ib - self._snapshot.current_balance  # realised losses
        total_room = max_total_loss - total_exposure_so_far - safety_buf

        daily_loss_used_pct = total_daily_exposure / max_daily_loss if max_daily_loss > 0 else 0.0
        total_loss_used_pct = total_exposure_so_far / max_total_loss if max_total_loss > 0 else 0.0

        # 5. Will this trade fit in the daily room?
        if risk_amount > daily_room:
            return ComplianceVerdict(
                can_proceed=False,
                code=VerdictCode.RISK_EXCEEDS_DAILY_ROOM,
                reason=(
                    f"Trade risk ${risk_amount:.2f} exceeds daily room "
                    f"${daily_room:.2f} (limit ${max_daily_loss:.2f}, "
                    f"used ${total_daily_exposure:.2f}, buffer ${safety_buf:.2f})"
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
                    f"used ${total_exposure_so_far:.2f}, buffer ${safety_buf:.2f})"
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
                "daily_room": daily_room,
                "total_room": total_room,
            },
        )

    def record_trade_closed(self, realized_pnl: float, timestamp: datetime) -> None:
        """Record a closed trade's realized P&L.

        Call this whenever a position is fully or partially closed.

        Args:
            realized_pnl: Net P&L (positive=win, negative=loss). Include fees.
            timestamp:     UTC-aware datetime of the close.
        """
        ts_utc = self._ensure_utc(timestamp)
        day_id = self._fundingpips_day_id(ts_utc)

        # Roll day if needed before recording
        cur = self._snapshot.current_balance
        self._maybe_new_day(day_id, cur, cur)

        day = self._snapshot.current_day
        if day is not None:
            day.realized_pnl += realized_pnl
            day.trades_taken += 1

        self._snapshot.current_balance += realized_pnl
        self._snapshot.total_realized_pnl += realized_pnl
        self._snapshot.last_trade_ts = ts_utc.isoformat()

        if self._snapshot.first_trade_ts is None:
            self._snapshot.first_trade_ts = ts_utc.isoformat()

        # Update trading days count (count unique days with at least one trade)
        # Simplified: increment when a new calendar day has its first trade
        if day is not None and day.trades_taken == 1:
            self._snapshot.trading_days += 1

        # Re-check limits
        current_equity = self._snapshot.current_balance + self._floating_pnl
        self._check_hard_limits(current_equity, day_id)

        # Check phase target
        self._check_phase_target()

        self._persist()

    def generate_report(self) -> dict[str, Any]:
        """Generate a compliance status report."""
        ib = self._snapshot.initial_balance
        max_daily = ib * self.profile.max_daily_loss_pct
        max_total = ib * self.profile.max_total_loss_pct

        day = self._snapshot.current_day
        daily_exposure = self._current_daily_exposure()
        total_exposure = ib - self._snapshot.current_balance

        return {
            "profile_id": self.profile.profile_id,
            "display_name": self.profile.display_name,
            "source_status": self.profile.source_status.value,
            "state": self._state.value,
            "breach_reason": self._snapshot.breach_reason,
            "initial_balance": ib,
            "current_balance": self._snapshot.current_balance,
            "total_realized_pnl": self._snapshot.total_realized_pnl,
            "floating_pnl": self._floating_pnl,
            "peak_balance": self._snapshot.peak_balance,
            "trading_days": self._snapshot.trading_days,
            "min_trading_days_required": self.profile.min_trading_days,
            "trading_days_met": self._snapshot.trading_days >= self.profile.min_trading_days,
            "last_trade_ts": self._snapshot.last_trade_ts,
            "profit_target_pct": self.profile.profit_target_pct,
            "profit_target_usd": ib * self.profile.profit_target_pct,
            "profit_achieved_usd": self._snapshot.total_realized_pnl,
            "profit_achieved_pct": self._snapshot.total_realized_pnl / ib if ib > 0 else 0.0,
            "max_daily_loss_pct": self.profile.max_daily_loss_pct,
            "max_daily_loss_usd": max_daily,
            "daily_exposure_usd": daily_exposure,
            "daily_exposure_pct": daily_exposure / ib if ib > 0 else 0.0,
            "daily_room_usd": max(0.0, max_daily - daily_exposure),
            "max_total_loss_pct": self.profile.max_total_loss_pct,
            "max_total_loss_usd": max_total,
            "total_exposure_usd": total_exposure,
            "total_exposure_pct": total_exposure / ib if ib > 0 else 0.0,
            "total_room_usd": max(0.0, max_total - total_exposure),
            "today": {
                "day_id": day.day_id if day else None,
                "daily_reference": day.daily_reference if day else None,
                "realized_pnl": day.realized_pnl if day else 0.0,
                "trades_taken": day.trades_taken if day else 0,
            },
            "environment": "MT5_DEMO",
            "caveats": [
                "All compliance checks are advisory for demo trading.",
                "Challenge PASS requires human verification; never declared locally.",
                f"Source status: {self.profile.source_status.value}",
            ],
        }

    def halt(self, reason: str = "Manual halt") -> None:
        """Manually halt the compliance engine."""
        self._state = ComplianceState.HALTED
        self._snapshot.state = ComplianceState.HALTED.value
        self._snapshot.breach_reason = reason
        logger.warning("Compliance engine HALTED: %s", reason)
        self._persist()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fundingpips_day_id(ts: datetime) -> str:
        """FundingPips calendar day ID.

        Platform midnight = UTC+3 = 21:00 UTC.
        Shift the timestamp back by 21 hours, then take the UTC calendar date.
        This maps the interval 21:00 UTC (day N) → 20:59 UTC (day N+1) to 'day N'.
        """
        shifted = ts - timedelta(hours=21)
        return shifted.date().isoformat()

    @staticmethod
    def _ensure_utc(ts: datetime) -> datetime:
        """Ensure timestamp is UTC-aware."""
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

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

    def _current_daily_exposure(self) -> float:
        """How much of the daily limit has been consumed (positive = loss consumed).

        Includes both realized P&L for today AND floating P&L (unrealised losses).
        FundingPips counts floating drawdown against the daily limit.
        """
        day = self._snapshot.current_day
        if day is None:
            return 0.0

        daily_ref = day.daily_reference
        realized_today = day.realized_pnl  # negative if loss
        floating = self._floating_pnl      # negative if loss

        # Current equity = reference + realized_today + (floating beyond reference)
        # Daily loss = reference - current_equity (positive when losing)
        current_equity_relative = daily_ref + realized_today + floating
        daily_loss = daily_ref - current_equity_relative
        return max(0.0, daily_loss)

    def _check_hard_limits(self, equity: float, day_id: str) -> None:
        """Check and enforce hard daily and total loss limits."""
        if self._state in (ComplianceState.BREACHED, ComplianceState.HALTED):
            return

        ib = self._snapshot.initial_balance

        # ── Daily loss limit ──
        daily_exposure = self._current_daily_exposure()
        max_daily_loss = ib * self.profile.max_daily_loss_pct

        if daily_exposure >= max_daily_loss:
            self._breach(
                f"Daily loss limit breached: "
                f"exposure={daily_exposure:.2f}, limit={max_daily_loss:.2f} "
                f"on day {day_id}"
            )
            return

        # ── Total loss limit ──
        # For static accounts: reference is initial_balance
        total_loss = ib - self._snapshot.current_balance
        # Also account for floating losses
        floating_loss = -min(0.0, self._floating_pnl)
        effective_total_loss = total_loss + floating_loss
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

        if daily_fraction >= self.WARN_THRESHOLD or total_fraction >= self.WARN_THRESHOLD:
            if self._state == ComplianceState.COMPLIANT:
                self._state = ComplianceState.WARNING
                logger.warning(
                    "Compliance WARNING: daily=%.1f%% total=%.1f%% of limits used",
                    daily_fraction * 100,
                    total_fraction * 100,
                )
        elif self._state == ComplianceState.WARNING:
            self._state = ComplianceState.COMPLIANT

    def _check_phase_target(self) -> None:
        """Check if the profit target for the current phase has been reached."""
        if self._state in (ComplianceState.BREACHED, ComplianceState.HALTED,
                           ComplianceState.PHASE_TARGET_REACHED):
            return

        ib = self._snapshot.initial_balance
        profit = self._snapshot.current_balance - ib
        target = ib * self.profile.profit_target_pct

        if profit >= target and self._snapshot.trading_days >= self.profile.min_trading_days:
            self._state = ComplianceState.PHASE_TARGET_REACHED
            self._snapshot.state = self._state.value
            logger.info(
                "PHASE_TARGET_REACHED: profit=%.2f >= target=%.2f, "
                "trading_days=%d >= min=%d. Pending human review.",
                profit, target,
                self._snapshot.trading_days, self.profile.min_trading_days,
            )
            self._persist()

    def _check_inactivity(self) -> ComplianceVerdict | None:
        """Check if the inactivity deadline has been breached."""
        if self.profile.inactivity_max_days <= 0:
            return None
        last_ts = self._snapshot.last_trade_ts
        if last_ts is None:
            return None
        last_trade = datetime.fromisoformat(last_ts)
        if last_trade.tzinfo is None:
            last_trade = last_trade.replace(tzinfo=timezone.utc)
        days_idle = (datetime.now(timezone.utc) - last_trade).days
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
        """Load persisted state from disk, or create a fresh snapshot."""
        if self.state_path and self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                # Rebuild current_day if present
                current_day = None
                if data.get("current_day"):
                    current_day = DaySnapshot(**data["current_day"])
                snap = ComplianceSnapshot(
                    profile_id=data["profile_id"],
                    initial_balance=data["initial_balance"],
                    peak_balance=data["peak_balance"],
                    current_balance=data["current_balance"],
                    trading_days=data["trading_days"],
                    first_trade_ts=data.get("first_trade_ts"),
                    last_trade_ts=data.get("last_trade_ts"),
                    state=data["state"],
                    breach_reason=data.get("breach_reason"),
                    current_day=current_day,
                    total_realized_pnl=data.get("total_realized_pnl", 0.0),
                )
                # Validate profile matches
                if snap.profile_id != self.profile.profile_id:
                    logger.warning(
                        "Persisted profile '%s' != current profile '%s'. "
                        "Starting fresh state.",
                        snap.profile_id, self.profile.profile_id,
                    )
                    return self._fresh_snapshot()
                logger.info(
                    "Loaded compliance state from %s: state=%s",
                    self.state_path, snap.state,
                )
                return snap
            except Exception as exc:
                logger.error("Failed to load compliance state: %s. Starting fresh.", exc)

        return self._fresh_snapshot()

    def _fresh_snapshot(self) -> ComplianceSnapshot:
        ib = self.profile.initial_balance
        return ComplianceSnapshot(
            profile_id=self.profile.profile_id,
            initial_balance=ib,
            peak_balance=ib,
            current_balance=ib,
            trading_days=0,
            first_trade_ts=None,
            last_trade_ts=None,
            state=ComplianceState.COMPLIANT.value,
            breach_reason=None,
            current_day=None,
            total_realized_pnl=0.0,
        )

    def _persist(self) -> None:
        """Persist current state to disk."""
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {
                "profile_id": self._snapshot.profile_id,
                "initial_balance": self._snapshot.initial_balance,
                "peak_balance": self._snapshot.peak_balance,
                "current_balance": self._snapshot.current_balance,
                "trading_days": self._snapshot.trading_days,
                "first_trade_ts": self._snapshot.first_trade_ts,
                "last_trade_ts": self._snapshot.last_trade_ts,
                "state": self._snapshot.state,
                "breach_reason": self._snapshot.breach_reason,
                "total_realized_pnl": self._snapshot.total_realized_pnl,
                "current_day": (
                    asdict(self._snapshot.current_day)
                    if self._snapshot.current_day else None
                ),
                "_written_utc": datetime.now(timezone.utc).isoformat(),
            }
            self.state_path.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.error("Failed to persist compliance state: %s", exc)
