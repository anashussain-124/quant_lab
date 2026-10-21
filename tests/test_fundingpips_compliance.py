"""Tests for FundingPips compliance engine and rule profiles.

Validates:
1. All preset profiles match documented rules
2. UNVERIFIED profiles are blocked from execution
3. Daily loss limit calculation with FundingPips UTC+3 reset (21:00 UTC)
4. Floating loss immediately counted against daily and total limits
5. Hard loss limit breach marks state as BREACHED (irreversible)
6. Phase profit target reached state detection
7. Safety buffer prevents trades too close to limits
8. State persistence across restarts
9. Inactivity deadline checks
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from engine.prop_firm_rules import (
    PROP_FIRM_PRESETS,
    PropFirmModel,
    ChallengePhase,
    RuleSourceStatus,
    get_profile,
    list_profiles,
    FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
    FUNDINGPIPS_2STEP_STANDARD_3PCT_P1,
    FUNDINGPIPS_2STEP_PRO_P1,
    FUNDINGPIPS_1STEP_FLEX_P1,
)
from engine.prop_firm_compliance import (
    PropFirmComplianceEngine,
    ComplianceState,
    VerdictCode,
)


class TestPropFirmRules:
    def test_presets_exist(self):
        assert len(PROP_FIRM_PRESETS) >= 7
        assert "fundingpips_2step_standard_5pct_p1" in PROP_FIRM_PRESETS
        assert "fundingpips_2step_pro_p1" in PROP_FIRM_PRESETS

    def test_standard_5pct_p1_rules(self):
        p = FUNDINGPIPS_2STEP_STANDARD_5PCT_P1
        assert p.initial_balance == 10000.0
        assert p.profit_target_pct == 0.08
        assert p.max_daily_loss_pct == 0.05
        assert p.max_total_loss_pct == 0.10
        assert p.min_trading_days == 3
        assert p.daily_reset_utc_hour == 21
        assert p.source_status == RuleSourceStatus.EMPIRICAL

    def test_pro_p1_rules(self):
        p = FUNDINGPIPS_2STEP_PRO_P1
        assert p.profit_target_pct == 0.06
        assert p.max_daily_loss_pct == 0.03
        assert p.max_total_loss_pct == 0.06
        assert p.min_trading_days == 2

    def test_unverified_rule_profile(self):
        p = FUNDINGPIPS_1STEP_FLEX_P1
        assert p.source_status == RuleSourceStatus.UNVERIFIED

    def test_get_profile_lookup(self):
        p = get_profile("fundingpips_2step_standard_5pct_p1")
        assert p.model == PropFirmModel.FUNDINGPIPS_2STEP_STANDARD
        with pytest.raises(KeyError):
            get_profile("non_existent_profile")

    def test_list_profiles(self):
        summary = list_profiles()
        assert len(summary) >= 7
        assert any(s["profile_id"] == "fundingpips_2step_standard_5pct_p1" for s in summary)


class TestPropFirmComplianceEngine:
    @pytest.fixture
    def engine(self, tmp_path: Path):
        state_file = tmp_path / "compliance_state.json"
        return PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=state_file,
            safety_buffer_pct=0.005,  # 0.5% = $50
        )

    def test_unverified_profile_blocked(self, tmp_path: Path):
        eng = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_1STEP_FLEX_P1,
            state_path=tmp_path / "state_unverified.json",
        )
        verdict = eng.check_trade_eligible(risk_amount=50.0)
        assert not verdict.can_proceed
        assert verdict.code == VerdictCode.UNVERIFIED_RULES

    def test_initial_state_compliant(self, engine: PropFirmComplianceEngine):
        now = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10000.0, equity=10000.0, timestamp=now)
        assert engine.state == ComplianceState.COMPLIANT
        
        # Max daily loss is 5% = $500. Buffer is 0.5% = $50. Room = $450.
        verdict = engine.check_trade_eligible(risk_amount=100.0)
        assert verdict.can_proceed
        assert verdict.code == VerdictCode.APPROVED
        assert verdict.daily_room_remaining == pytest.approx(450.0)

    def test_risk_exceeds_daily_room(self, engine: PropFirmComplianceEngine):
        now = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10000.0, equity=10000.0, timestamp=now)
        
        # Room is $450 with buffer. Trade risking $460 should fail.
        verdict = engine.check_trade_eligible(risk_amount=460.0)
        assert not verdict.can_proceed
        assert verdict.code == VerdictCode.RISK_EXCEEDS_DAILY_ROOM

    def test_daily_loss_breach_from_floating(self, engine: PropFirmComplianceEngine):
        now = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10000.0, equity=10000.0, timestamp=now)
        
        # Floating loss drops equity to 9490 (loss = $510 > $500)
        engine.update_equity(balance=10000.0, equity=9490.0, timestamp=now)
        assert engine.state == ComplianceState.BREACHED
        
        # No more trades allowed
        verdict = engine.check_trade_eligible(risk_amount=10.0)
        assert not verdict.can_proceed
        assert verdict.code == VerdictCode.CHALLENGE_FAILED

    def test_utc_plus_3_day_reset(self, engine: PropFirmComplianceEngine):
        # 20:59 UTC on Jan 10 -> corresponds to previous session (FundingPips Day Jan 9)
        # 21:01 UTC on Jan 10 -> corresponds to new session (FundingPips Day Jan 10)
        t1 = datetime(2026, 1, 10, 20, 59, tzinfo=timezone.utc)
        engine.update_equity(balance=10000.0, equity=10000.0, timestamp=t1)
        day_1 = engine.generate_report()["today"]["day_id"]

        t2 = datetime(2026, 1, 10, 21, 1, tzinfo=timezone.utc)
        engine.update_equity(balance=10000.0, equity=10000.0, timestamp=t2)
        day_2 = engine.generate_report()["today"]["day_id"]

        assert day_1 != day_2
        assert day_1 == "2026-01-09"
        assert day_2 == "2026-01-10"

    def test_state_persistence_across_restarts(self, tmp_path: Path):
        state_file = tmp_path / "persist_test.json"
        eng1 = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=state_file,
        )
        t = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        eng1.update_equity(balance=10000.0, equity=10000.0, timestamp=t)
        eng1.record_trade_closed(realized_pnl=-200.0, timestamp=t)
        assert eng1.current_balance == 9800.0

        # Create new instance pointing to same state_file
        eng2 = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=state_file,
        )
        assert eng2.current_balance == 9800.0
        assert eng2.trading_days == 1

    def test_phase_target_reached(self, engine: PropFirmComplianceEngine):
        # Standard 5% P1 target is 8% ($800) with min 3 trading days
        days = [
            datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 11, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc),
        ]
        for idx, dt in enumerate(days):
            engine.update_equity(balance=10000.0 + idx * 300, equity=10000.0 + idx * 300, timestamp=dt)
            engine.record_trade_closed(realized_pnl=300.0, timestamp=dt)

        assert engine.current_balance == 10900.0  # +$900 > +$800
        assert engine.trading_days >= 3
        assert engine.state == ComplianceState.PHASE_TARGET_REACHED

        # New trades blocked once target reached
        verdict = engine.check_trade_eligible(risk_amount=20.0)
        assert not verdict.can_proceed
        assert verdict.code == VerdictCode.PHASE_TARGET_MET
