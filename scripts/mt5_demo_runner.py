#!/usr/bin/env python3
"""Check, reconcile, or (only if fully armed) execute on an MT5 demo account.

Read-only by default. ``--order-check`` performs a non-transmitting MT5
``order_check`` to validate symbol/price/margin plumbing. ``--monitor`` updates
compliance state from live floating equity and activates the kill switch on an
internal or firm limit without sending an order. ``--execute-demo`` requires
every deployment gate in configs/fundingpips.yaml to be open; they are not open
in this mission, so execution is refused before any order call.

Exit codes:
  0  report produced (check/reconcile/kill operations)
  2  execution was attempted but did not result in a verified fill
  3  configuration or startup failure
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.logger import setup_logger  # noqa: E402
from execution.mt5_demo_runner import (  # noqa: E402
    ENVIRONMENT,
    DemoRunner,
    MT5DemoClient,
    Signal,
    load_runner_config,
    utc_now,
)


def _print_summary(report: dict) -> None:
    print("=" * 78)
    print(" MT5 DEMO RUNNER — READ-ONLY CHECK")
    print("=" * 78)
    print(f"environment        : {report.get('environment')}")
    account = report.get("account") or {}
    print(f"account            : {account.get('login')} @ {account.get('server')}")
    print(f"trade_mode         : {account.get('trade_mode')} (0 = DEMO)")
    print(f"balance / equity   : {account.get('balance')} / {account.get('equity')}")
    terminal = report.get("terminal") or {}
    print(f"terminal connected : {terminal.get('connected')}")
    print(f"AutoTrading        : {terminal.get('trade_allowed')}")
    gate = report.get("execution_gate") or {}
    print(f"execution gate     : {gate.get('status')}")
    for reason in gate.get("reasons") or []:
        print(f"  - {reason}")
    spec = report.get("symbol_spec") or {}
    if spec:
        print(
            "spec               : "
            f"point={spec.get('point')} contract={spec.get('contract_size')} "
            f"tick_size={spec.get('tick_size')} "
            f"money_per_point/lot={spec.get('money_per_point_per_lot')}"
        )
        print(
            "volume             : "
            f"min={spec.get('volume_min')} step={spec.get('volume_step')} "
            f"max={spec.get('volume_max')}"
        )
        for warning in spec.get("warnings") or []:
            print(f"  spec warning: {warning}")
    tick = report.get("tick") or {}
    if tick:
        print(
            "tick               : "
            f"bid={tick.get('bid')} ask={tick.get('ask')} "
            f"spread={tick.get('spread_points')} pts age={tick.get('age_seconds')}s"
        )
    plumbing = report.get("order_plumbing")
    if plumbing:
        print(f"order_check        : {json.dumps(plumbing, default=str)}")
    print("=" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "fundingpips.yaml")
    parser.add_argument("--check", action="store_true", help="default: read-only preflight")
    parser.add_argument(
        "--order-check",
        action="store_true",
        help="also run a non-transmitting MT5 order_check",
    )
    parser.add_argument("--reconcile", action="store_true", help="reconcile intents/positions")
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="check floating equity/daily/overall limits and halt on alert (never sends orders)",
    )
    parser.add_argument(
        "--execute-demo",
        action="store_true",
        help="attempt one demo order; requires every deployment gate to be open",
    )
    parser.add_argument("--signal-file", type=Path, help="JSON signal for --execute-demo")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument("--kill", metavar="REASON", help="activate the manual kill switch")
    parser.add_argument("--clear-kill", action="store_true", help="clear the manual kill switch")
    parser.add_argument("--confirm", action="store_true", help="required with --clear-kill")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logger(
        name="quant_lab.mt5_demo",
        level="DEBUG" if args.verbose else "INFO",
        environment=ENVIRONMENT,
    )

    try:
        loaded = load_runner_config(args.config)
    except Exception as exc:  # noqa: BLE001 - config must fail closed
        print(f"CONFIG_ERROR: {exc}", file=sys.stderr)
        return 3

    settings = loaded.settings
    if args.kill:
        from execution.mt5_demo_runner import StateStore

        StateStore(settings.state_dir).activate_kill_switch(args.kill)
        print(f"kill switch activated: {args.kill}")
        return 0
    if args.clear_kill:
        if not args.confirm:
            print("REFUSED: --clear-kill requires --confirm", file=sys.stderr)
            return 3
        from execution.mt5_demo_runner import StateStore

        StateStore(settings.state_dir).clear_kill_switch()
        print("kill switch cleared")
        return 0

    client = MT5DemoClient(
        __import__("MetaTrader5"),
        expected_login=settings.expected_login,
        expected_server=settings.expected_server,
    )
    ok, init_message = client.initialize()
    if not ok:
        print(f"MT5_INIT_FAILED: {init_message}", file=sys.stderr)
        if not args.check and not args.reconcile and not args.execute_demo and not args.monitor:
            return 3

    runner = DemoRunner(
        client,
        settings,
        loaded.profile,
        loaded.compliance_state_path,
        config_sha256=loaded.config_sha256,
        now_fn=utc_now,
    )

    try:
        if args.reconcile:
            try:
                print(json.dumps(runner.reconcile(write=False), indent=2, default=str))
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"RECONCILE_ERROR: {exc}", file=sys.stderr)
                return 3

        if args.monitor:
            decision = runner.monitor()
            print(json.dumps(decision.to_dict(), indent=2, default=str))
            return 0 if decision.action == "MONITOR_OK" else 2

        if args.execute_demo:
            if not args.signal_file or not args.signal_file.exists():
                print("SIGNAL_FILE_REQUIRED", file=sys.stderr)
                return 3
            signal_data = json.loads(args.signal_file.read_text(encoding="utf-8"))
            signal = Signal(
                side=str(signal_data["side"]),
                stop_distance=float(signal_data["stop_distance"]),
                target_distance=float(signal_data.get("target_distance", 0.0)),
                candidate_id=signal_data.get("candidate_id"),
            )
            decision = runner.execute(signal)
            print(json.dumps(decision.to_dict(), indent=2, default=str))
            return 0 if decision.action == "FILLED" else 2

        report = runner.check_report(run_order_check=args.order_check)
        out = args.out or (
            ROOT
            / "reports_fundingpips"
            / "mt5_demo"
            / f"check_{utc_now().date().isoformat()}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        _print_summary(report)
        print(f"report written: {out}")
        return 0
    finally:
        client.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
