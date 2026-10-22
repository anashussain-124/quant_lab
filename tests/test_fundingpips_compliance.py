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
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from engine.prop_firm_rules import (
    PROP_FIRM_PRESETS,
    PropFirmModel,
    ChallengePhase,
    RuleSourceStatus,
    get_profile,
    make_profile,
    list_profiles,
    FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
    FUNDINGPIPS_2STEP_STANDARD_5PCT_P2,
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

    def test_make_profile_resizes_to_verified_account_sizes(self):
        p = make_profile("fundingpips_2step_standard_5pct_p1", 5_000.0)
        assert p.initial_balance == 5_000.0
        assert p.profile_id == "fundingpips_2step_standard_5pct_p1"
        assert p.profit_target_pct == 0.08
        with pytest.raises(ValueError):
            make_profile("fundingpips_2step_standard_5pct_p1", 7_500.0)

    def test_pro_profiles_are_not_claimed_as_verified(self):
        assert FUNDINGPIPS_2STEP_PRO_P1.source_status == RuleSourceStatus.UNVERIFIED


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


class TestComplianceRiskCore:
    """Regression tests for the loss accounting and fail-closed behaviour."""

    @pytest.fixture
    def engine(self, tmp_path: Path):
        return PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=tmp_path / "state.json",
            safety_buffer_pct=0.005,
            account_created_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_daily_reference_uses_higher_of_balance_and_equity(self, engine):
        t = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10_000.0, equity=10_100.0, timestamp=t)
        report = engine.generate_report()
        assert report["today"]["daily_reference"] == pytest.approx(10_100.0)

        # 5% of 10,100 = 505. A 500 loss is inside; 506 is a breach.
        engine.update_equity(balance=10_000.0, equity=9_600.0, timestamp=t)
        assert engine.state != ComplianceState.BREACHED
        engine.update_equity(balance=10_000.0, equity=9_594.0, timestamp=t)
        assert engine.state == ComplianceState.BREACHED

    def test_total_loss_is_static_not_trailing(self, tmp_path: Path):
        # Isolate the total-loss rule from the daily rule.
        profile = replace(
            FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            max_daily_loss_pct=0.99,
        )
        eng = PropFirmComplianceEngine(
            rule_profile=profile,
            state_path=tmp_path / "static_total.json",
        )
        t1 = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        eng.update_equity(balance=11_000.0, equity=11_000.0, timestamp=t1)

        t2 = datetime(2026, 1, 11, 10, 0, tzinfo=timezone.utc)
        eng.update_equity(balance=9_500.0, equity=9_500.0, timestamp=t2)
        # Static floor is $9,000, not $9,900 from the $11,000 peak.
        assert eng.state != ComplianceState.BREACHED

        eng.update_equity(balance=8_999.0, equity=8_999.0, timestamp=t2)
        assert eng.state == ComplianceState.BREACHED

    def test_open_risk_consumes_daily_room(self, engine):
        now = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10_000.0, equity=10_000.0, timestamp=now)
        assert engine.check_trade_eligible(
            300.0, open_risk_amount=100.0, now=now
        ).can_proceed
        blocked = engine.check_trade_eligible(
            300.0, open_risk_amount=200.0, now=now
        )
        assert not blocked.can_proceed
        assert blocked.code == VerdictCode.RISK_EXCEEDS_DAILY_ROOM

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
    def test_invalid_risk_is_rejected(self, engine, bad):
        engine.update_equity(
            balance=10_000.0,
            equity=10_000.0,
            timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        )
        verdict = engine.check_trade_eligible(bad)
        assert not verdict.can_proceed

    def test_non_finite_broker_values_are_rejected(self, engine):
        with pytest.raises(ValueError):
            engine.update_equity(
                balance=float("nan"),
                equity=10_000.0,
                timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
            )
        assert engine.current_balance == 10_000.0
        assert engine.state == ComplianceState.COMPLIANT

    def test_flatten_clears_stale_floating_pnl(self, engine):
        t = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10_000.0, equity=10_400.0, timestamp=t)
        engine.record_trade_closed(300.0, t, open_positions=0)
        assert engine.current_balance == pytest.approx(10_300.0)
        assert engine.current_equity == pytest.approx(10_300.0)
        assert engine.generate_report()["floating_pnl"] == pytest.approx(0.0)

    def test_close_after_reset_uses_pre_close_equity(self, engine):
        t1 = datetime(2026, 1, 10, 20, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10_000.0, equity=10_100.0, timestamp=t1)

        # First event of Jan 11 FundingPips day: close at a loss.
        t2 = datetime(2026, 1, 10, 21, 30, tzinfo=timezone.utc)
        engine.record_trade_closed(-150.0, t2, open_positions=0)
        report = engine.generate_report()
        assert report["today"]["day_id"] == "2026-01-10"
        assert report["today"]["daily_reference"] == pytest.approx(10_100.0)
        assert report["daily_exposure_usd"] == pytest.approx(250.0)

    def test_min_trading_days_blocks_target(self, engine):
        for day, pnl in [(10, 300.0), (11, 300.0)]:
            t = datetime(2026, 1, day, 10, 0, tzinfo=timezone.utc)
            engine.update_equity(balance=engine.current_balance,
                                 equity=engine.current_balance, timestamp=t)
            engine.record_trade_closed(pnl, t, open_positions=0)
        assert engine.trading_days == 2
        assert engine.state != ComplianceState.PHASE_TARGET_REACHED

        t = datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=engine.current_balance,
                             equity=engine.current_balance, timestamp=t)
        engine.record_trade_closed(300.0, t, open_positions=0)
        assert engine.trading_days == 3
        assert engine.state == ComplianceState.PHASE_TARGET_REACHED

    def test_target_not_declared_with_open_positions(self, engine):
        for day in (10, 11, 12):
            t = datetime(2026, 1, day, 10, 0, tzinfo=timezone.utc)
            engine.update_equity(balance=engine.current_balance,
                                 equity=engine.current_balance, timestamp=t)
            engine.record_trade_closed(300.0, t, open_positions=1)
        assert engine.state != ComplianceState.PHASE_TARGET_REACHED

        engine.record_trade_closed(0.0, t, open_positions=0)
        assert engine.state == ComplianceState.PHASE_TARGET_REACHED

    def test_inactivity_runs_from_account_creation(self, tmp_path: Path):
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        eng = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=tmp_path / "inactive.json",
            account_created_ts=now - timedelta(days=31),
        )
        eng.update_equity(balance=10_000.0, equity=10_000.0, timestamp=now)
        verdict = eng.check_trade_eligible(10.0, now=now)
        assert not verdict.can_proceed
        assert verdict.code == VerdictCode.INACTIVITY_DEADLINE

    def test_last_close_resets_inactivity_clock(self, tmp_path: Path):
        now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        eng = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=tmp_path / "active.json",
            account_created_ts=now - timedelta(days=60),
        )
        eng.update_equity(balance=10_000.0, equity=10_000.0, timestamp=now)
        eng.record_trade_closed(10.0, now - timedelta(days=1), open_positions=0)
        assert eng.check_trade_eligible(10.0, now=now).can_proceed

    def test_warning_threshold_is_configurable(self, tmp_path: Path):
        eng = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=tmp_path / "warn.json",
            warn_threshold_pct=0.50,
        )
        t = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        eng.update_equity(balance=10_000.0, equity=9_800.0, timestamp=t)
        assert eng.state == ComplianceState.COMPLIANT
        eng.update_equity(balance=10_000.0, equity=9_700.0, timestamp=t)
        assert eng.state == ComplianceState.WARNING

    def test_halt_does_not_downgrade_breach(self, engine):
        t = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10_000.0, equity=9_490.0, timestamp=t)
        assert engine.state == ComplianceState.BREACHED
        reason = engine.generate_report()["breach_reason"]
        engine.halt("manual")
        assert engine.state == ComplianceState.BREACHED
        assert engine.generate_report()["breach_reason"] == reason

    def test_corrupt_state_fails_closed(self, tmp_path: Path):
        path = tmp_path / "corrupt.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(RuntimeError):
            PropFirmComplianceEngine(
                rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
                state_path=path,
            )

    def test_profile_mismatch_fails_closed(self, engine):
        engine.update_equity(
            balance=10_000.0,
            equity=10_000.0,
            timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(RuntimeError):
            PropFirmComplianceEngine(
                rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P2,
                state_path=engine.state_path,
            )

    def test_initial_balance_mismatch_fails_closed(self, engine):
        engine.update_equity(
            balance=10_000.0,
            equity=10_000.0,
            timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
        )
        with pytest.raises(RuntimeError):
            PropFirmComplianceEngine(
                rule_profile=make_profile(
                    "fundingpips_2step_standard_5pct_p1", 5_000.0
                ),
                state_path=engine.state_path,
            )

    def test_persistence_failure_halts(self, engine, monkeypatch):
        def fail_replace(src, dst):
            raise OSError("disk unavailable")

        monkeypatch.setattr("engine.prop_firm_compliance.os.replace", fail_replace)
        with pytest.raises(RuntimeError):
            engine.update_equity(
                balance=10_000.0,
                equity=10_000.0,
                timestamp=datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc),
            )
        assert engine.state == ComplianceState.HALTED

    def test_state_reload_preserves_equity_peak_and_creation_time(self, engine):
        t = datetime(2026, 1, 10, 10, 0, tzinfo=timezone.utc)
        engine.update_equity(balance=10_000.0, equity=10_500.0, timestamp=t)
        reloaded = PropFirmComplianceEngine(
            rule_profile=FUNDINGPIPS_2STEP_STANDARD_5PCT_P1,
            state_path=engine.state_path,
        )
        report = reloaded.generate_report()
        assert report["peak_equity"] == pytest.approx(10_500.0)
        assert report["today"]["day_id"] == "2026-01-09"
        assert reloaded._snapshot.account_created_ts == "2026-01-01T00:00:00+00:00"
