"""Prop firm rule profiles for FundingPips challenge models.

Per AGENTS.md A9: Every research claim tagged EMPIRICAL | THEORETICAL | UNVERIFIED | CONTRADICTED.

IMPORTANT: These rule parameters are sourced from FundingPips official documentation.
The 2 Step Standard presets were re-verified on 2026-10-22 against
https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard
Rule parameters can change without notice. Always verify before live use.

Daily reset: the official daily-loss FAQ states 00:00 platform time (UTC+3),
i.e. 21:00 UTC. The Master auto-close table implies UTC+2 in winter, so the
offset around DST transitions remains unconfirmed and is a deployment blocker.
day_id is assigned by shifting timestamp 21 hours back before taking calendar date.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

FUNDINGPIPS_ACCOUNT_SIZES: tuple[float, ...] = (
    5_000.0, 10_000.0, 25_000.0, 50_000.0, 100_000.0,
)

_STANDARD_2026_URL = (
    "https://help.fundingpips.com/hc/en-us/articles/"
    "34501809112081-2-Step-Standard"
)


class PropFirmModel(StrEnum):
    """Supported FundingPips challenge models."""
    FUNDINGPIPS_2STEP_STANDARD = "fundingpips_2step_standard"
    FUNDINGPIPS_2STEP_PRO = "fundingpips_2step_pro"
    FUNDINGPIPS_1STEP_FLEX = "fundingpips_1step_flex"


class ChallengePhase(StrEnum):
    """Challenge phase identifier."""
    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    FUNDED = "funded"


class RuleSourceStatus(StrEnum):
    """Claim status per AGENTS.md A9."""
    EMPIRICAL = "EMPIRICAL"       # Verified against official source
    THEORETICAL = "THEORETICAL"  # Derived from stated rules
    UNVERIFIED = "UNVERIFIED"     # Needs independent confirmation
    CONTRADICTED = "CONTRADICTED" # Known to conflict with another source


@dataclass(frozen=True)
class PropFirmRuleProfile:
    """Immutable rule profile for one challenge model + phase.

    All percentage values are fractional (0.05 = 5%).
    """
    # Identity
    profile_id: str                      # e.g. "fundingpips_2step_standard_p1_5pct"
    model: PropFirmModel
    phase: ChallengePhase
    display_name: str

    # Account parameters
    initial_balance: float               # e.g. 10_000.0
    profit_target_pct: float             # Fraction of initial_balance (phase target)

    # Loss limits (fractions of initial_balance or reference equity)
    max_daily_loss_pct: float            # Max daily drawdown allowed
    max_total_loss_pct: float            # Max total / overall drawdown
    trailing_total: bool                 # True if max_total_loss trails peak equity

    # Time constraints
    min_trading_days: int                # Minimum calendar trading days required
    max_duration_days: int               # 0 = unlimited
    inactivity_max_days: int             # Days without a trade before violation

    # Daily reset (UTC hour at which FundingPips "day" resets)
    daily_reset_utc_hour: int            # 21 for FundingPips (UTC+3 midnight)

    # Reference equity for daily loss calculation
    # FundingPips: max(balance_at_day_open, opening_equity_at_day_open)
    daily_ref_is_max_balance_equity: bool = True

    # Metadata
    rule_version: str = "2024-Q4"
    source_url: str = "https://fundingpips.com/challenges"
    source_status: RuleSourceStatus = RuleSourceStatus.UNVERIFIED
    rule_verified_utc: str = ""
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Preset profiles
# ---------------------------------------------------------------------------

FUNDINGPIPS_2STEP_STANDARD_5PCT_P1 = PropFirmRuleProfile(
    # EMPIRICAL: re-verified 2026-10-22 against the official 2 Step Standard
    # help article (profit targets, loss limits, inactivity, account sizes).
    profile_id="fundingpips_2step_standard_5pct_p1",
    model=PropFirmModel.FUNDINGPIPS_2STEP_STANDARD,
    phase=ChallengePhase.PHASE_1,
    display_name="FundingPips 2-Step Standard (5% daily) – Phase 1",
    initial_balance=10_000.0,
    profit_target_pct=0.08,         # 8% profit target in phase 1
    max_daily_loss_pct=0.05,        # 5% daily loss limit
    max_total_loss_pct=0.10,        # 10% overall drawdown
    trailing_total=False,           # Static (from initial balance)
    min_trading_days=3,
    max_duration_days=0,            # Unlimited
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="2026-10-22",
    source_url=_STANDARD_2026_URL,
    source_status=RuleSourceStatus.EMPIRICAL,
    rule_verified_utc="2026-10-22",
    notes=[
        "Phase 1 target 8%; Phase 2 target 5%; no time limit.",
        "Daily loss = 5% of max(opening balance, opening equity); "
        "floating and closed losses both count.",
        "Daily reset 00:00 platform time (stated UTC+3) = 21:00 UTC.",
        "Total loss = 10% of starting balance, static; equity or balance "
        "cannot touch the floor at any time.",
        "Minimum 3 trading days before claiming the phase target; target "
        "only counts with all trades closed.",
        "Inactivity: 30 consecutive calendar days without a completed trade.",
        "2 Step Standard evaluation: news trading unrestricted; overnight "
        "and weekend holding allowed.",
        "Account sizes: $5K, $10K, $25K, $50K, $100K.",
        "UNRESOLVED: FAQ states UTC+3 while the Master auto-close table "
        "shows UTC+2 in winter; confirm platform offset before deployment.",
    ],
)

FUNDINGPIPS_2STEP_STANDARD_5PCT_P2 = PropFirmRuleProfile(
    profile_id="fundingpips_2step_standard_5pct_p2",
    model=PropFirmModel.FUNDINGPIPS_2STEP_STANDARD,
    phase=ChallengePhase.PHASE_2,
    display_name="FundingPips 2-Step Standard (5% daily) – Phase 2",
    initial_balance=10_000.0,
    profit_target_pct=0.05,         # 5% profit target in phase 2
    max_daily_loss_pct=0.05,
    max_total_loss_pct=0.10,
    trailing_total=False,
    min_trading_days=3,
    max_duration_days=0,
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="2026-10-22",
    source_url=_STANDARD_2026_URL,
    source_status=RuleSourceStatus.EMPIRICAL,
    rule_verified_utc="2026-10-22",
    notes=[
        "Phase 2 is a fresh account at the same starting balance.",
        "Same loss limits as Phase 1; lower 5% profit target.",
        "Daily loss calculation: max(opening balance, opening equity), "
        "floating included; reset 00:00 platform time (UTC+3).",
    ],
)

FUNDINGPIPS_2STEP_STANDARD_3PCT_P1 = PropFirmRuleProfile(
    # EMPIRICAL: 3% daily loss add-on variant of the 2-Step Standard model.
    profile_id="fundingpips_2step_standard_3pct_p1",
    model=PropFirmModel.FUNDINGPIPS_2STEP_STANDARD,
    phase=ChallengePhase.PHASE_1,
    display_name="FundingPips 2-Step Standard (3% daily) – Phase 1",
    initial_balance=10_000.0,
    profit_target_pct=0.08,
    max_daily_loss_pct=0.03,        # Stricter 3% daily limit
    max_total_loss_pct=0.10,
    trailing_total=False,
    min_trading_days=0,             # No minimum trading days in 3% variant
    max_duration_days=0,
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="2026-10-22",
    source_url=_STANDARD_2026_URL,
    source_status=RuleSourceStatus.EMPIRICAL,
    rule_verified_utc="2026-10-22",
    notes=[
        "3% daily loss add-on variant; no minimum trading day requirement.",
        "Stricter daily limit makes the challenge harder to pass.",
    ],
)

FUNDINGPIPS_2STEP_STANDARD_3PCT_P2 = PropFirmRuleProfile(
    profile_id="fundingpips_2step_standard_3pct_p2",
    model=PropFirmModel.FUNDINGPIPS_2STEP_STANDARD,
    phase=ChallengePhase.PHASE_2,
    display_name="FundingPips 2-Step Standard (3% daily) – Phase 2",
    initial_balance=10_000.0,
    profit_target_pct=0.05,
    max_daily_loss_pct=0.03,
    max_total_loss_pct=0.10,
    trailing_total=False,
    min_trading_days=0,
    max_duration_days=0,
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="2026-10-22",
    source_url=_STANDARD_2026_URL,
    source_status=RuleSourceStatus.EMPIRICAL,
    rule_verified_utc="2026-10-22",
    notes=[
        "3% daily limit with 5% profit target for phase 2.",
    ],
)

FUNDINGPIPS_2STEP_PRO_P1 = PropFirmRuleProfile(
    # EMPIRICAL: FundingPips 2-Step Pro model phase 1.
    profile_id="fundingpips_2step_pro_p1",
    model=PropFirmModel.FUNDINGPIPS_2STEP_PRO,
    phase=ChallengePhase.PHASE_1,
    display_name="FundingPips 2-Step Pro – Phase 1",
    initial_balance=10_000.0,
    profit_target_pct=0.06,         # 6% profit target
    max_daily_loss_pct=0.03,        # 3% daily loss limit (stricter than Standard 5%)
    max_total_loss_pct=0.06,        # 6% overall drawdown (stricter)
    trailing_total=False,
    min_trading_days=2,
    max_duration_days=0,
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="2024-Q4-NOT-REVERIFIED",
    source_status=RuleSourceStatus.UNVERIFIED,
    notes=[
        "NOT RE-VERIFIED for 2026; blocked from execution until checked.",
        "Pro model: tighter 6% total loss, 3% daily loss",
        "Profit target and total loss limit are equal (6%), leaving no room for error",
    ],
)

FUNDINGPIPS_2STEP_PRO_P2 = PropFirmRuleProfile(
    profile_id="fundingpips_2step_pro_p2",
    model=PropFirmModel.FUNDINGPIPS_2STEP_PRO,
    phase=ChallengePhase.PHASE_2,
    display_name="FundingPips 2-Step Pro – Phase 2",
    initial_balance=10_000.0,
    profit_target_pct=0.06,         # Same 6% target in phase 2
    max_daily_loss_pct=0.03,
    max_total_loss_pct=0.06,
    trailing_total=False,
    min_trading_days=2,
    max_duration_days=0,
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="2024-Q4-NOT-REVERIFIED",
    source_status=RuleSourceStatus.UNVERIFIED,
    notes=[
        "NOT RE-VERIFIED for 2026; blocked from execution until checked.",
        "Both phases identical; 6% profit target, 6% total loss limit",
    ],
)

FUNDINGPIPS_1STEP_FLEX_P1 = PropFirmRuleProfile(
    # UNVERIFIED: 1-Step Flex rules have not been independently confirmed.
    # DO NOT use for execution decisions until verified from official source.
    profile_id="fundingpips_1step_flex_p1",
    model=PropFirmModel.FUNDINGPIPS_1STEP_FLEX,
    phase=ChallengePhase.PHASE_1,
    display_name="FundingPips 1-Step Flex – Phase 1 [UNVERIFIED]",
    initial_balance=10_000.0,
    profit_target_pct=0.12,         # ~12% target — UNVERIFIED
    max_daily_loss_pct=0.05,        # ~5% daily — UNVERIFIED
    max_total_loss_pct=0.10,        # ~10% total — UNVERIFIED
    trailing_total=False,
    min_trading_days=0,
    max_duration_days=0,
    inactivity_max_days=30,
    daily_reset_utc_hour=21,
    daily_ref_is_max_balance_equity=True,
    rule_version="UNVERIFIED",
    source_url="https://fundingpips.com/challenges (UNVERIFIED — check official site)",
    source_status=RuleSourceStatus.UNVERIFIED,
    notes=[
        "UNVERIFIED: 1-Step Flex parameters have not been confirmed from official source.",
        "The compliance engine will BLOCK all orders for UNVERIFIED profiles.",
        "Verify rules at https://fundingpips.com before enabling execution.",
    ],
)


# ---------------------------------------------------------------------------
# Registry: keyed by profile_id
# ---------------------------------------------------------------------------

PROP_FIRM_PRESETS: dict[str, PropFirmRuleProfile] = {
    p.profile_id: p for p in [
        FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
        FUNDINGPIPS_2STEP_STANDARD_5PCT_P2,
        FUNDINGPIPS_2STEP_STANDARD_3PCT_P1,
        FUNDINGPIPS_2STEP_STANDARD_3PCT_P2,
        FUNDINGPIPS_2STEP_PRO_P1,
        FUNDINGPIPS_2STEP_PRO_P2,
        FUNDINGPIPS_1STEP_FLEX_P1,
    ]
}


def get_profile(profile_id: str) -> PropFirmRuleProfile:
    """Look up a rule profile by ID.

    Raises KeyError if the profile_id is unknown.
    """
    if profile_id not in PROP_FIRM_PRESETS:
        available = list(PROP_FIRM_PRESETS.keys())
        raise KeyError(
            f"Unknown prop firm profile: '{profile_id}'. "
            f"Available profiles: {available}"
        )
    return PROP_FIRM_PRESETS[profile_id]


def make_profile(
    profile_id: str,
    initial_balance: float | None = None,
) -> PropFirmRuleProfile:
    """Return a profile, optionally resized to a verified FundingPips account size."""
    base = get_profile(profile_id)
    if initial_balance is None:
        return base
    if not math.isfinite(initial_balance) or initial_balance not in FUNDINGPIPS_ACCOUNT_SIZES:
        raise ValueError(
            f"initial_balance must be one of {FUNDINGPIPS_ACCOUNT_SIZES}, "
            f"got {initial_balance!r}"
        )
    return replace(base, initial_balance=float(initial_balance))


def list_profiles() -> list[dict[str, Any]]:
    """Return a summary list of all profiles for reporting."""
    return [
        {
            "profile_id": p.profile_id,
            "display_name": p.display_name,
            "model": p.model.value,
            "phase": p.phase.value,
            "profit_target_pct": p.profit_target_pct,
            "max_daily_loss_pct": p.max_daily_loss_pct,
            "max_total_loss_pct": p.max_total_loss_pct,
            "min_trading_days": p.min_trading_days,
            "source_status": p.source_status.value,
            "rule_version": p.rule_version,
            "rule_verified_utc": p.rule_verified_utc,
        }
        for p in PROP_FIRM_PRESETS.values()
    ]
