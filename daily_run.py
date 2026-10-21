#!/usr/bin/env python3
"""Daily XAUUSD demo pipeline — REAL MT5 data only.

Fetches live XAUUSD data from MT5 demo, runs strategy,
executes via MT5 bridge, and reports results.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import json
from datetime import datetime, timezone

# Core components
from engine.safety_gate import SafetyGate, RiskContext, LabConfig
from engine.risk_engine import RiskEngine, PaperBroker, KillSwitch
from engine.mt5_bridge import MT5Bridge, MT5Config, MT5OrderMode
from engine.logger import AuditLogger
from engine.backtester_v2 import CostModel
from strategies import get_strategy
from libs.quant_math import sharpe_ratio, sortino_ratio


def main() -> int:
    print("="*60)
    print("  QUANT LAB — XAUUSD DAILY DEMO (REAL MT5)")
    print("="*60)
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    # 1. Initialize components
    print("[1/6] Initializing safety gate...")
    safety_gate = SafetyGate(LabConfig())
    context = RiskContext()
    safety_result = safety_gate.check_pre_trade(context)
    if not safety_result.can_proceed:
        print(f"  SAFETY GATE BLOCKED: {safety_result.reason}")
        return 1
    print(f"  Safety gate: PASS ({safety_result.reason})")

    print("[2/6] Initializing risk engine...")
    risk_engine = RiskEngine()
    kill_switch = KillSwitch(risk_engine)
    if kill_switch.is_active:
        print("  KILL SWITCH ACTIVE")
        return 1
    print(f"  Risk level: {risk_engine.risk_level.value}")

    print("[3/6] Connecting to MT5 demo...")
    mt5_config = MT5Config(
        account=5056172579,
        server="MetaQuotes-Demo",
        symbol="XAUUSD",
    )
    bridge = MT5Bridge(
        config=mt5_config,
        risk_engine=risk_engine,
    )
    bridge_mode = MT5OrderMode.SHADOW  # Shadow mode for demo
    print(f"  MT5 bridge: {bridge_mode.value} mode")
    print(f"  Symbol: {mt5_config.symbol}")

    # 4. Fetch real XAUUSD data
    print("[4/6] Fetching real XAUUSD data from MT5...")
    from adapters.mt5 import MetaTrader5Adapter
    mt5_adapter = MetaTrader5Adapter(
        account=5056172579,
        server="MetaQuotes-Demo",
        symbol="XAUUSD",
    )
    if not mt5_adapter.initialize():
        print("  ERROR: Failed to initialize MT5 adapter")
        return 1

    if not mt5_adapter.is_demo:
        print("  ERROR: Account is NOT in DEMO mode")
        mt5_adapter.shutdown()
        return 1

    # Fetch historical data
    df = mt5_adapter.fetch_historical("XAUUSD", "1h", 500)
    if df.empty:
        print("  ERROR: No historical data returned")
        mt5_adapter.shutdown()
        return 1

    print(f"  Data: {len(df)} candles")
    print(f"  Range: {df.iloc[0]['timestamp']} to {df.iloc[-1]['timestamp']}")
    print(f"  Last close: ${df.iloc[-1]['close']:,.2f}")

    # Get account info
    acct = mt5_adapter.get_account_info()
    sym = mt5_adapter.get_symbol_info()
    print(f"  Account balance: ${acct.get('balance', 0):,.2f}")
    print(f"  Symbol: {sym.get('symbol', 'N/A')}")
    print(f"  Bid: {sym.get('bid', 0):,.2f}")
    print(f"  Ask: {sym.get('ask', 0):,.2f}")
    print(f"  Spread: {sym.get('spread', 0)} points")
    print(f"  Contract size: {sym.get('contract_size', 0)} oz")
    print(f"  Min lot: {sym.get('min_lot', 0)}")
    print(f"  Max lot: {sym.get('max_lot', 0)}")
    print(f"  Lot step: {sym.get('lot_step', 0)}")
    print(f"  Tick value: ${sym.get('tick_value', 0)}")
    print(f"  Point: {sym.get('point', 0)}")

    # 5. Run strategy
    print("[5/6] Running strategy on real XAUUSD data...")
    strategy = get_strategy("momentum_breakout_real")
    if strategy is None:
        print("  ERROR: Strategy not found")
        mt5_adapter.shutdown()
        return 1

    # Generate signal from recent data
    signal = strategy.generate_signal(df)
    print(f"  Strategy: momentum_breakout_real")
    print(f"  Signal: {signal.action.value}")
    print(f"  Side: {signal.side}")
    print(f"  Price: {signal.price}")
    print(f"  Confidence: {signal.confidence:.2f}")
    print(f"  Reason: {signal.reason_codes}")

    # 6. Execute trade (shadow mode)
    print("[6/6] Executing trade...")
    trade_result = None
    if signal.action.value == "TAKE" and signal.side:
        # Calculate position size based on risk
        balance = acct.get("balance", 5000.0)
        risk_amount = balance * 0.01  # 1% risk
        stop_distance = 10.0  # $10 stop
        tick_value = sym.get("tick_value", 0.10)
        contract_size = sym.get("contract_size", 100.0)

        # Volume = risk / (stop * tick_value) in lots
        volume = risk_amount / (stop_distance * tick_value)
        volume = max(sym.get("min_lot", 0.01), min(volume, sym.get("max_lot", 1.0)))
        # Round to lot step
        lot_step = sym.get("lot_step", 0.01)
        volume = round(volume / lot_step) * lot_step

        print(f"  Risk amount: ${risk_amount:.2f}")
        print(f"  Volume: {volume:.2f} lots")

        # Execute via bridge
        trade_result = bridge.execute_order(
            symbol="XAUUSD",
            side=signal.side,
            quantity=volume,
            price=signal.price,
        )

        print(f"  Trade status: {trade_result.get('status', 'UNKNOWN')}")
        if trade_result.get("status") == "FILLED":
            print(f"  Fill price: ${trade_result.get('fill_price', 0):,.2f}")
            print(f"  Quantity: {trade_result.get('fill_quantity', 0):.2f} lots")
            print(f"  Mode: {trade_result.get('mode', 'N/A')}")
            print(f"  Ticket: {trade_result.get('ticket', 0)}")
        elif trade_result.get("status") == "REJECTED":
            print(f"  Rejected: {trade_result.get('reason', 'Unknown')}")
        else:
            print(f"  Result: {trade_result}")
    else:
        print(f"  No trade: signal is {signal.action.value}")

    # Cleanup
    mt5_adapter.shutdown()

    # Summary
    print()
    print("="*60)
    print("  DAILY DEMO COMPLETE")
    print("="*60)
    print(f"  Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print(f"  Symbol: XAUUSD")
    print(f"  Strategy: momentum_breakout_real")
    print(f"  Signal: {signal.action.value}")
    if trade_result:
        print(f"  Trade: {trade_result.get('status', 'N/A')}")
        if trade_result.get('status') == 'FILLED':
            print(f"  P&L: ${trade_result.get('slippage', 0):.2f} (slippage)")
    print("="*60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
