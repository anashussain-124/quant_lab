#!/usr/bin/env python3
"""Daily report generator for Donchian-50 XAUUSD strategy."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def load_json(path: Path) -> list[dict]:
    """Load JSON file."""
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return []


def generate_report():
    """Generate daily strategy report."""
    log_dir = Path("logs")
    trades_file = log_dir / "trades.json"
    equity_file = log_dir / "equity_curve.json"
    state_file = log_dir / "strategy_state.json"
    
    trades = load_json(trades_file)
    equity = load_json(equity_file)
    state = {}
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
        except:
            pass
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Filter today's trades
    today_trades = [t for t in trades if t.get("timestamp", "").startswith(today)]
    
    # Calculate stats
    total_pnl = sum(t.get("pnl", 0) for t in today_trades)
    wins = sum(1 for t in today_trades if t.get("pnl", 0) > 0)
    losses = sum(1 for t in today_trades if t.get("pnl", 0) <= 0)
    
    report = f"""
{'='*60}
  DONCHIAN-50 XAUUSD — DAILY REPORT
{'='*60}

Date: {today}
Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}

TODAY'S PERFORMANCE
{'-'*40}
Trades: {len(today_trades)}
Wins: {wins}
Losses: {losses}
Win Rate: {wins/len(today_trades)*100 if len(today_trades) > 0 else 0:.0f}%
P&L: ${total_pnl:.2f}

STRATEGY STATE
{'-'*40}
Trades Today: {state.get('trades_today', 0)}
Daily P&L: ${state.get('daily_pnl', 0):.2f}
Consecutive Losses: {state.get('consecutive_losses', 0)}
Open Position: {state.get('open_position', 'None')}

OVERALL PERFORMANCE
{'-'*40}
Total Trades: {len(trades)}
Total P&L: ${sum(t.get('pnl', 0) for t in trades):.2f}

RISK LIMITS
{'-'*40}
Max Daily Loss: 5%
Max Drawdown: 10%
Max Consecutive Losses: 10
Risk Per Trade: 0.25%

STATUS: {'ACTIVE' if state.get('consecutive_losses', 0) < 10 else 'PAUSED'}

{'='*60}
"""
    
    print(report)
    
    # Save report
    report_file = log_dir / f"report_{today}.txt"
    with open(report_file, "w") as f:
        f.write(report)
    
    return report


if __name__ == "__main__":
    generate_report()
