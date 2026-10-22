"""Tests for the fail-closed MT5 demo execution runner.

All broker interaction uses a fake MT5 module. No test connects to a terminal
or transmits an order.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.prop_firm_rules import FUNDINGPIPS_2STEP_STANDARD_5PCT_P1
from execution.mt5_demo_runner import (
    DemoRunner,
    IntentRecord,
    MT5DemoClient,
    RunnerSettings,
    Signal,
    SpecError,
    SymbolSpec,
    load_runner_config,
    size_position,
    validate_tick,
)


class FakeClock:
    def __init__(self, ts: datetime) -> None:
        self.ts = ts

    def __call__(self) -> datetime:
        return self.ts

    def advance(self, seconds: float) -> None:
        self.ts += timedelta(seconds=seconds)


class FakeMT5:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.initialized = False
        self.account = SimpleNamespace(
            login=123456,
            server="MetaQuotes-Demo",
            trade_mode=0,
            balance=5000.0,
            equity=5000.0,
            currency="USD",
            leverage=30,
            trade_allowed=True,
            trade_expert=True,
        )
        self.terminal = SimpleNamespace(
            connected=True,
            trade_allowed=True,
            company="Fake",
            name="Fake Terminal",
        )
        self.symbol = SimpleNamespace(
            name="XAUUSD",
            digits=2,
            point=0.01,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_contract_size=100.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=0,
            trade_freeze_level=0,
            currency_profit="USD",
            trade_mode=4,
            filling_mode=3,
            trade_exemode=2,
        )
        self.tick = SimpleNamespace(
            bid=2000.00,
            ask=2000.20,
            time_msc=int((clock.ts - timedelta(seconds=0.5)).timestamp() * 1000),
            volume=0,
        )
        self.positions: tuple = ()
        self.orders: tuple = ()
        self.sent: list[dict] = []
        self.checked: list[dict] = []
        self.send_result = {
            "retcode": 10009,
            "order": 111,
            "deal": 222,
            "price": 2000.20,
            "volume": 0.01,
            "comment": "done",
        }
        self.on_send = None
        self.on_order_check = None
        self.order_check_result = {
            "retcode": 0,
            "margin": 40.0,
            "margin_free": 4960.0,
            "comment": "Done",
        }

    def initialize(self) -> bool:
        self.initialized = True
        return True

    def shutdown(self) -> None:
        self.initialized = False

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        return self.account

    def terminal_info(self):
        return self.terminal

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return True

    def symbol_info(self, symbol: str):
        return self.symbol

    def symbol_info_tick(self, symbol: str):
        return self.tick

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        diff = (
            price_close - price_open
            if order_type == 0
            else price_open - price_close
        )
        return diff * self.symbol.trade_contract_size * volume

    def positions_get(self, symbol: str | None = None):
        return self.positions

    def orders_get(self, symbol: str | None = None):
        return self.orders

    def order_check(self, request):
        self.checked.append(dict(request))
        if self.on_order_check is not None:
            self.on_order_check(request)
        return dict(self.order_check_result)

    def order_send(self, request):
        self.sent.append(dict(request))
        if self.on_send is not None:
            return self.on_send(request)
        position = SimpleNamespace(
            ticket=111,
            symbol=request["symbol"],
            type=request["type"],
            volume=request["volume"],
            price_open=request["price"],
            price_current=request["price"],
            sl=request["sl"],
            tp=request["tp"],
            magic=request["magic"],
            comment=request["comment"],
            profit=0.0,
            swap=0.0,
            time=0,
            time_msc=0,
            identifier=111,
        )
        self.positions = (position,)
        return dict(self.send_result)


def make_env(tmp_path, **settings_overrides):
    clock = FakeClock(datetime(2026, 1, 5, 10, 0, tzinfo=UTC))
    fake = FakeMT5(clock)
    defaults = {
        "account_initial_balance": 5000.0,
        "risk_fraction": 0.0025,
        "state_dir": tmp_path / "state",
        "expected_login": 123456,
        "expected_server": "MetaQuotes-Demo",
        "config_armed": True,
        "order_sending_enabled": True,
        "candidate_id": "candidate-test",
        "candidate_validated": True,
        "deployment_blockers": (),
    }
    defaults.update(settings_overrides)
    settings = RunnerSettings(**defaults)
    profile = replace(FUNDINGPIPS_2STEP_STANDARD_5PCT_P1, initial_balance=5000.0)
    client = MT5DemoClient(
        fake,
        expected_login=settings.expected_login,
        expected_server=settings.expected_server,
        sleep=lambda _: None,
    )
    runner = DemoRunner(
        client,
        settings,
        profile,
        tmp_path / "compliance.json",
        config_sha256="test",
        now_fn=clock,
        sleep=lambda _: None,
    )
    return SimpleNamespace(
        clock=clock,
        fake=fake,
        settings=settings,
        client=client,
        runner=runner,
        profile=profile,
    )


def buy_signal(**kwargs) -> Signal:
    defaults = {"side": "BUY", "stop_distance": 10.0, "candidate_id": "candidate-test"}
    defaults.update(kwargs)
    return Signal(**defaults)


# ---------------------------------------------------------------------------
# Symbol specification and sizing
# ---------------------------------------------------------------------------


class TestSymbolSpec:
    def test_uses_profit_calc_and_contract_size(self, tmp_path):
        env = make_env(tmp_path)
        spec = env.client.build_symbol_spec("XAUUSD")
        assert spec.contract_size == 100.0
        assert spec.profit_per_unit_per_lot == pytest.approx(100.0)
        assert spec.money_per_point_per_lot == pytest.approx(1.0)
        assert spec.risk_per_lot(10.0) == pytest.approx(1000.0)
        assert spec.tick_value_consistent is True

    def test_reported_tick_value_mismatch_is_flagged_not_trusted(self, tmp_path):
        # This reproduces the live MetaQuotes-Demo XAUUSD value: tick value
        # reports 0.10 while order_calc_profit proves 1.00 per 0.01 move.
        env = make_env(tmp_path)
        env.fake.symbol.trade_tick_value = 0.10
        spec = env.client.build_symbol_spec("XAUUSD")
        assert spec.tick_value_consistent is False
        assert any("trade_tick_value" in w for w in spec.warnings)
        assert spec.money_per_point_per_lot == pytest.approx(1.0)

    def test_contract_profit_mismatch_rejected(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.symbol.trade_contract_size = 10.0
        env.fake.order_calc_profit = lambda *args: 100.0
        with pytest.raises(SpecError, match="contract-size/profit mismatch"):
            env.client.build_symbol_spec("XAUUSD")

    def test_missing_tick_value_rejected(self, tmp_path):
        env = make_env(tmp_path)
        del env.fake.symbol.trade_tick_value
        with pytest.raises(SpecError, match="trade_tick_value"):
            env.client.build_symbol_spec("XAUUSD")

    def test_no_supported_filling_mode(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.symbol.filling_mode = 0
        spec = env.client.build_symbol_spec("XAUUSD")
        with pytest.raises(SpecError, match="filling_mode"):
            spec.choose_filling()


class TestSizing:
    def _spec(self, tmp_path) -> SymbolSpec:
        return make_env(tmp_path).client.build_symbol_spec("XAUUSD")

    def test_floors_to_step_and_includes_commission(self, tmp_path):
        spec = self._spec(tmp_path)
        decision = size_position(
            spec,
            equity=5000.0,
            risk_fraction=0.0025,
            stop_distance=10.0,
            extra_cost_per_lot_usd=15.0,
        )
        assert decision.approved
        assert decision.lots == 0.01
        assert decision.risk_usd == pytest.approx(10.15)
        assert decision.risk_usd <= decision.risk_budget_usd

    def test_min_lot_over_budget_is_rejected_not_rounded_up(self, tmp_path):
        spec = self._spec(tmp_path)
        decision = size_position(
            spec,
            equity=5000.0,
            risk_fraction=0.001,
            stop_distance=10.0,
            extra_cost_per_lot_usd=15.0,
        )
        assert not decision.approved
        assert decision.reason == "MIN_LOT_EXCEEDS_BUDGET"
        assert decision.lots == 0.0

    def test_room_cap_can_reject_min_lot(self, tmp_path):
        spec = self._spec(tmp_path)
        decision = size_position(
            spec,
            equity=5000.0,
            risk_fraction=0.0025,
            stop_distance=10.0,
            extra_cost_per_lot_usd=15.0,
            daily_room_usd=5.0,
        )
        assert not decision.approved
        assert decision.reason == "MIN_LOT_EXCEEDS_BUDGET"

    def test_room_cap_limits_budget(self, tmp_path):
        spec = self._spec(tmp_path)
        decision = size_position(
            spec,
            equity=5000.0,
            risk_fraction=0.0025,
            stop_distance=1.0,
            extra_cost_per_lot_usd=0.0,
            overall_room_usd=1.5,
        )
        assert decision.approved
        assert decision.lots == 0.01
        assert decision.risk_usd == pytest.approx(1.0)

    def test_zero_stop_rejected(self, tmp_path):
        spec = self._spec(tmp_path)
        decision = size_position(
            spec, equity=5000.0, risk_fraction=0.0025, stop_distance=0.0
        )
        assert not decision.approved
        assert decision.reason == "INVALID_STOP_DISTANCE"


# ---------------------------------------------------------------------------
# Tick validation
# ---------------------------------------------------------------------------


class TestTickValidation:
    def _spec(self, tmp_path):
        env = make_env(tmp_path)
        return env, env.client.build_symbol_spec("XAUUSD")

    def test_fresh_tick_ok(self, tmp_path):
        env, spec = self._spec(tmp_path)
        snap = validate_tick(
            env.fake.tick, spec, env.clock.ts, max_age_seconds=5, max_spread_points=50
        )
        assert snap.ok
        assert snap.spread_points == pytest.approx(20.0)

    def test_stale_tick_rejected(self, tmp_path):
        env, spec = self._spec(tmp_path)
        env.fake.tick.time_msc = int(
            (env.clock.ts - timedelta(seconds=10)).timestamp() * 1000
        )
        snap = validate_tick(
            env.fake.tick, spec, env.clock.ts, max_age_seconds=5, max_spread_points=50
        )
        assert not snap.ok
        assert any("STALE_TICK" in reason for reason in snap.reasons)

    def test_future_tick_rejected(self, tmp_path):
        env, spec = self._spec(tmp_path)
        env.fake.tick.time_msc = int(
            (env.clock.ts + timedelta(seconds=10)).timestamp() * 1000
        )
        snap = validate_tick(
            env.fake.tick, spec, env.clock.ts, max_age_seconds=5, max_spread_points=50
        )
        assert not snap.ok
        assert any("FUTURE_TIMESTAMP" in reason for reason in snap.reasons)

    def test_crossed_quote_rejected(self, tmp_path):
        env, spec = self._spec(tmp_path)
        env.fake.tick.bid = 2000.30
        snap = validate_tick(
            env.fake.tick, spec, env.clock.ts, max_age_seconds=5, max_spread_points=50
        )
        assert not snap.ok
        assert "CROSSED_QUOTE" in snap.reasons

    def test_wide_spread_rejected(self, tmp_path):
        env, spec = self._spec(tmp_path)
        env.fake.tick.ask = 2001.00
        snap = validate_tick(
            env.fake.tick, spec, env.clock.ts, max_age_seconds=5, max_spread_points=50
        )
        assert not snap.ok
        assert any("SPREAD_TOO_WIDE" in reason for reason in snap.reasons)

    def test_server_time_offset_is_applied(self, tmp_path):
        env, spec = self._spec(tmp_path)
        env.fake.tick.time_msc = int(
            (env.clock.ts + timedelta(hours=3) - timedelta(seconds=0.5)).timestamp() * 1000
        )
        snap = validate_tick(
            env.fake.tick,
            spec,
            env.clock.ts,
            max_age_seconds=5,
            max_spread_points=50,
            server_utc_offset_seconds=3 * 3600,
        )
        assert snap.ok
        assert snap.age_seconds == pytest.approx(0.5, abs=0.01)
        assert snap.server_offset_seconds == 3 * 3600
        assert snap.raw_time_utc != snap.time_utc

    def test_wrong_server_offset_is_not_silently_accepted(self, tmp_path):
        env, spec = self._spec(tmp_path)
        env.fake.tick.time_msc = int(
            (env.clock.ts + timedelta(hours=3)).timestamp() * 1000
        )
        snap = validate_tick(
            env.fake.tick,
            spec,
            env.clock.ts,
            max_age_seconds=5,
            max_spread_points=50,
            server_utc_offset_seconds=0.0,
        )
        assert not snap.ok
        assert any("FUTURE_TIMESTAMP" in reason for reason in snap.reasons)


# ---------------------------------------------------------------------------
# Demo verification and gating
# ---------------------------------------------------------------------------


class TestDemoGating:
    def test_non_demo_account_rejected(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.account.trade_mode = 1
        verdict = env.client.verify_demo()
        assert not verdict.ok
        assert any("NOT_DEMO" in reason for reason in verdict.reasons)

    def test_unknown_trade_mode_rejected(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.account.trade_mode = None
        verdict = env.client.verify_demo()
        assert not verdict.ok
        assert "TRADE_MODE_UNKNOWN" in verdict.reasons

    def test_disconnected_terminal_rejected(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.terminal.connected = False
        verdict = env.client.verify_demo()
        assert not verdict.ok
        assert "TERMINAL_DISCONNECTED" in verdict.reasons

    def test_autotrading_disabled_blocks_execution(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.terminal.trade_allowed = False
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "PREFLIGHT_FAILED"
        assert "TERMINAL_AUTOTRADING_DISABLED" in decision.detail["reasons"]
        assert env.fake.sent == []
        assert env.fake.checked == []

    def test_deployment_gate_blocks_before_broker_calls(self, tmp_path):
        env = make_env(
            tmp_path,
            config_armed=False,
            order_sending_enabled=False,
            deployment_blockers=("unresolved rule",),
        )
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "DEPLOYMENT_GATE"
        assert "CONFIG_NOT_ARMED" in decision.detail["reasons"]
        assert "DEPLOYMENT_BLOCKERS_PRESENT" in decision.detail["reasons"]
        assert env.fake.sent == []
        assert env.fake.checked == []

    def test_account_size_mismatch_blocks(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.account.balance = 100000.0
        env.fake.account.equity = 100000.0
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "ACCOUNT_BASELINE"
        assert any("ACCOUNT_BALANCE_NOT_INITIAL" in r for r in decision.detail["reasons"])
        assert env.fake.sent == []

    def test_expected_login_mismatch_blocks(self, tmp_path):
        env = make_env(tmp_path, expected_login=999999)
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert any("LOGIN_MISMATCH" in r for r in decision.detail["reasons"])
        assert env.fake.sent == []

    def test_kill_switch_blocks(self, tmp_path):
        env = make_env(tmp_path)
        env.runner.store.activate_kill_switch("manual test")
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "KILL_SWITCH"
        assert env.fake.sent == []


# ---------------------------------------------------------------------------
# Execution state machine
# ---------------------------------------------------------------------------


class TestExecution:
    def test_successful_demo_fill_is_verified(self, tmp_path):
        env = make_env(tmp_path)
        decision = env.runner.execute(buy_signal())
        assert decision.action == "FILLED", decision.detail
        assert decision.reason_code == "FILL_VERIFIED"
        assert len(env.fake.sent) == 1
        request = env.fake.sent[0]
        assert request["volume"] == 0.01
        assert request["sl"] == pytest.approx(1990.20)
        assert request["tp"] == 0.0
        assert request["magic"] == env.settings.magic
        assert request["comment"].startswith("QL")
        assert request["type_filling"] == 1  # IOC
        assert env.runner.store.load_baseline()["login"] == 123456
        assert (tmp_path / "compliance.json").exists()

    def test_sell_request_uses_bid_and_stop_above(self, tmp_path):
        env = make_env(tmp_path)
        decision = env.runner.execute(
            buy_signal(side="SELL", stop_distance=10.0, target_distance=20.0)
        )
        assert decision.action == "FILLED", decision.detail
        request = env.fake.sent[0]
        assert request["type"] == 1
        assert request["price"] == pytest.approx(2000.00)
        assert request["sl"] == pytest.approx(2010.00)
        assert request["tp"] == pytest.approx(1980.00)

    def test_order_check_rejection_blocks_without_send(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.order_check_result = {
            "retcode": 10019,
            "margin": 0.0,
            "margin_free": 5000.0,
            "comment": "no money",
        }
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "ORDER_CHECK_REJECTED"
        assert env.fake.sent == []

    def test_unknown_margin_blocks(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.order_check_result = {
            "retcode": 0,
            "margin": 0.0,
            "margin_free": 5000.0,
            "comment": "Done",
        }
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "MARGIN_UNKNOWN"
        assert env.fake.sent == []

    def test_margin_above_cap_blocks(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.order_check_result = {
            "retcode": 0,
            "margin": 2000.0,
            "margin_free": 3000.0,
            "comment": "Done",
        }
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "MARGIN_TOO_HIGH"
        assert env.fake.sent == []

    def test_rejection_is_not_retried(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.on_send = lambda request: {"retcode": 10020, "comment": "price changed"}
        decision = env.runner.execute(buy_signal())
        assert decision.action == "REJECTED"
        assert decision.reason_code.startswith("RETCODE_10020")
        assert len(env.fake.sent) == 1
        assert not env.runner.store.kill_switch_path.exists()

    def test_send_timeout_is_uncertain_and_halts(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.on_send = lambda request: (_ for _ in ()).throw(TimeoutError("timeout"))
        decision = env.runner.execute(buy_signal())
        assert decision.action == "UNKNOWN"
        assert decision.reason_code == "SEND_OUTCOME_UNKNOWN"
        assert env.runner.store.kill_switch_path.exists()
        assert len(env.fake.sent) == 1

    def test_fill_verification_failure_halts(self, tmp_path):
        env = make_env(tmp_path)

        def bad_fill(request):
            env.fake.positions = (
                SimpleNamespace(
                    ticket=111,
                    symbol=request["symbol"],
                    type=request["type"],
                    volume=request["volume"],
                    price_open=request["price"],
                    price_current=request["price"],
                    sl=0.0,
                    tp=request["tp"],
                    magic=request["magic"],
                    comment=request["comment"],
                ),
            )
            return {"retcode": 10009, "order": 111, "deal": 222, "price": request["price"]}

        env.fake.on_send = bad_fill
        decision = env.runner.execute(buy_signal())
        assert decision.action == "VERIFICATION_FAILED"
        assert decision.reason_code == "FILL_VERIFICATION_FAILED"
        assert env.runner.store.kill_switch_path.exists()
        assert len(env.fake.sent) == 1

    def test_demo_reverified_after_order_check_before_send(self, tmp_path):
        env = make_env(tmp_path)

        def flip_to_real(_request):
            env.fake.account.trade_mode = 1

        env.fake.on_order_check = flip_to_real
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "PRE_SEND_VERIFICATION"
        assert env.fake.sent == []
        intent = env.runner.store.load_intents()[0]
        assert intent.state == "CANCELLED"
        assert "NOT_DEMO" in intent.reason

    def test_existing_pending_position_blocks_new_entry(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.positions = (
            SimpleNamespace(
                ticket=999,
                symbol="XAUUSD",
                type=0,
                volume=0.01,
                price_open=2000.0,
                price_current=2000.0,
                sl=1990.0,
                tp=0.0,
                magic=env.settings.magic,
                comment="manual",
            ),
        )
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "RECONCILIATION"
        assert any("POSITION_ALREADY_OPEN" in r for r in decision.detail["reconciliation"]["reasons"])
        assert env.fake.sent == []

    def test_unknown_pending_intent_blocks_after_restart(self, tmp_path):
        env = make_env(tmp_path)
        env.runner.store.save_intent(
            IntentRecord(
                intent_id="abcdef0123456789",
                state="PENDING",
                symbol="XAUUSD",
                side="BUY",
                lots=0.01,
                stop_distance=10.0,
                target_distance=0.0,
                request={"volume": 0.01, "price": 2000.2, "sl": 1990.2, "tp": 0.0},
                comment="QLabcdef0123456789",
                created_utc="2026-01-05T09:00:00+00:00",
                updated_utc="2026-01-05T09:00:00+00:00",
            )
        )
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "RECONCILIATION"
        assert "UNRESOLVED_INTENTS" in " ".join(decision.detail["reconciliation"]["reasons"])
        assert env.runner.store.load_intents()[0].state == "UNKNOWN"
        assert env.fake.sent == []

    def test_stale_tick_blocks(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.tick.time_msc = int(
            (env.clock.ts - timedelta(seconds=30)).timestamp() * 1000
        )
        decision = env.runner.execute(buy_signal())
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "PREFLIGHT_FAILED"
        assert env.fake.sent == []

    def test_stop_inside_freeze_level_blocked(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.symbol.trade_freeze_level = 2000  # 20.00 USD
        decision = env.runner.execute(buy_signal(stop_distance=10.0))
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "ORDER_BUILD_FAILED"
        assert "STOP_TOO_CLOSE_TO_MARKET" in decision.detail["error"]
        assert env.fake.sent == []


# ---------------------------------------------------------------------------
# Check report and config loading
# ---------------------------------------------------------------------------


class TestCheckReport:
    def test_report_is_read_only_and_blocked(self, tmp_path):
        env = make_env(tmp_path)
        env.fake.account.balance = 100000.0
        env.fake.account.equity = 100000.0
        env.fake.terminal.trade_allowed = False
        env.settings = replace(env.settings, deployment_blockers=("rule unresolved",))
        runner = DemoRunner(
            env.client,
            env.settings,
            env.profile,
            tmp_path / "compliance_report.json",
            now_fn=env.clock,
            sleep=lambda _: None,
        )
        report = runner.check_report(run_order_check=True)
        assert report["environment"] == "MT5_DEMO"
        assert report["execution_gate"]["status"] == "BLOCKED"
        reasons = " ".join(report["execution_gate"]["reasons"])
        assert "ACCOUNT_BALANCE_NOT_INITIAL" in reasons
        assert "TERMINAL_AUTOTRADING_DISABLED" in reasons
        assert "DEPLOYMENT_BLOCKERS_PRESENT" in reasons
        assert report["order_plumbing"]["performed"] is True
        assert report["order_plumbing"]["transmitted"] is False
        assert report["order_plumbing"]["retcode"] == 0
        assert env.fake.sent == []
        assert len(env.fake.checked) == 1
        assert not (tmp_path / "compliance_report.json").exists()
        assert not (tmp_path / "state" / "account_baseline.json").exists()

    def test_report_does_not_create_kill_switch_or_intents(self, tmp_path):
        env = make_env(tmp_path)
        env.runner.check_report()
        assert not env.runner.store.kill_switch_path.exists()
        assert env.runner.store.load_intents() == []


class TestMonitor:
    def test_monitor_requires_existing_baseline(self, tmp_path):
        env = make_env(tmp_path)
        decision = env.runner.monitor()
        assert decision.action == "BLOCKED"
        assert decision.reason_code == "NO_BASELINE"
        assert env.runner.store.load_baseline() is None
        assert not env.runner.store.kill_switch_path.exists()
        assert env.fake.sent == []

    def test_monitor_halts_on_daily_soft_limit(self, tmp_path):
        env = make_env(tmp_path)
        env.runner.store.save_baseline(
            login=123456,
            server="MetaQuotes-Demo",
            currency="USD",
            initial_balance=5000.0,
        )
        env.fake.account.balance = 5000.0
        env.fake.account.equity = 5000.0
        assert env.runner.monitor().action == "MONITOR_OK"
        env.fake.account.balance = 4940.0
        env.fake.account.equity = 4940.0
        decision = env.runner.monitor()
        assert decision.action == "HALT"
        assert decision.reason_code == "MONITOR_ALERT"
        assert any("DAILY_SOFT_LIMIT" in alert for alert in decision.detail["alerts"])
        assert env.runner.store.kill_switch_path.exists()
        assert env.fake.sent == []

    def test_monitor_ok_below_limits(self, tmp_path):
        env = make_env(tmp_path)
        env.runner.store.save_baseline(
            login=123456,
            server="MetaQuotes-Demo",
            currency="USD",
            initial_balance=5000.0,
        )
        env.fake.account.balance = 5000.0
        env.fake.account.equity = 4990.0
        decision = env.runner.monitor()
        assert decision.action == "MONITOR_OK"
        assert decision.reason_code == "MONITOR_OK"
        assert not env.runner.store.kill_switch_path.exists()
        assert env.fake.sent == []


class TestConfigLoading:
    def test_real_config_is_blocked_and_consistent(self):
        loaded = load_runner_config(Path("configs/fundingpips.yaml"))
        assert loaded.settings.account_initial_balance == 5000.0
        assert loaded.profile.initial_balance == 5000.0
        assert loaded.profile.profile_id == "fundingpips_2step_standard_5pct_p1"
        assert loaded.settings.config_armed is False
        assert loaded.settings.order_sending_enabled is False
        assert loaded.settings.candidate_id is None
        assert loaded.settings.candidate_validated is False
        assert loaded.settings.deployment_blockers
        assert loaded.settings.risk_fraction == 0.0025
        assert loaded.settings.server_utc_offset_hours == 3.0
        assert loaded.settings.state_dir.is_absolute()
        assert loaded.compliance_state_path.is_absolute()
        assert len(loaded.config_sha256) == 64

    def test_live_environment_rejected(self, tmp_path):
        source = Path("configs/fundingpips.yaml").read_text(encoding="utf-8")
        path = tmp_path / "live.yaml"
        path.write_text(source.replace('environment: "MT5_DEMO"', 'environment: "LIVE"'), encoding="utf-8")
        with pytest.raises(ValueError, match="MT5_DEMO"):
            load_runner_config(path)

    def test_rule_mismatch_rejected(self, tmp_path):
        source = Path("configs/fundingpips.yaml").read_text(encoding="utf-8")
        path = tmp_path / "bad_rules.yaml"
        path.write_text(
            source.replace("max_daily_loss_pct: 0.05", "max_daily_loss_pct: 0.03"),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="max_daily_loss_pct"):
            load_runner_config(path)

