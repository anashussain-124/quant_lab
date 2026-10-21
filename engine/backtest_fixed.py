"""Run backtest with CORRECT XAUUSD position sizing.

XAUUSD specs:
- 1 lot = 100 oz
- 1 point (0.01) = $10 per lot
- P&L = price_move_lots × $10
- Position size = risk_amount / (stop_points × $10)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def run_backtest_fixed(data: pd.DataFrame, signals: list[dict[str, Any]],
                       initial_capital: float = 5000.0,
                       risk_per_trade_pct: float = 0.01,
                       max_drawdown_pct: float = 0.10,
                       daily_loss_limit_pct: float = 0.05,
                       max_holding_bars: int = 100,
                       atr_mult: float = 2.0,
                       commission_rate: float = 0.0004,
                       slippage_bps: float = 2.0,
                       risk_per_trade: float | None = None) -> dict[str, Any]:
    """Run backtest with correct XAUUSD position sizing."""
    if risk_per_trade is not None:
        risk_per_trade_pct = risk_per_trade

    if not signals or len(data) < 50:
        return {"error": "Insufficient data"}

    # Signal lookup
    signal_map = {}
    for sig in signals:
        ts = pd.Timestamp(sig["timestamp"])
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        signal_map[ts] = sig

    data_ts = data["timestamp"].apply(
        lambda x: pd.Timestamp(x).tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x)
    )
    close = data["close"].values
    high = data["high"].values
    low = data["low"].values

    equity = initial_capital
    position = 0
    entry_price = 0.0
    entry_bar = 0
    entry_stop = 0.0
    entry_lot = 0.0
    trades = []
    equity_curve = [(data_ts.iloc[0], equity)]
    daily_pnl = 0.0
    daily_date = data_ts.iloc[0].date()
    peak_equity = equity
    halted = False

    for i in range(len(data)):
        ts = data_ts.iloc[i]
        c = close[i]
        h = high[i]
        l = low[i]

        current_date = ts.date()
        if current_date != daily_date:
            daily_date = current_date
            daily_pnl = 0.0

        # Daily loss limit
        if daily_pnl < -initial_capital * daily_loss_limit_pct:
            if position != 0:
                pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1
                       else (entry_price - c) * entry_lot * 10.0)
                fee = abs(pnl) * commission_rate
                slip = c * entry_lot * (slippage_bps / 10000) * 10.0
                net = pnl - fee - slip
                trades.append({"side": "LONG" if position == 1 else "SHORT", "entry_price": entry_price,
                               "exit_price": c, "pnl": net, "return_pct": net / equity,
                               "bars_held": i - entry_bar, "exit_reason": "daily_limit"})
                equity += net
                daily_pnl += net
                position = 0
            equity_curve.append((ts, equity))
            continue

        # Max drawdown
        if equity < peak_equity * (1 - max_drawdown_pct):
            if position != 0:
                pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1
                       else (entry_price - c) * entry_lot * 10.0)
                fee = abs(pnl) * commission_rate
                slip = c * entry_lot * (slippage_bps / 10000) * 10.0
                net = pnl - fee - slip
                trades.append({"side": "LONG" if position == 1 else "SHORT", "entry_price": entry_price,
                               "exit_price": c, "pnl": net, "return_pct": net / equity,
                               "bars_held": i - entry_bar, "exit_reason": "max_dd"})
                equity += net
                position = 0
            halted = True
            equity_curve.append((ts, equity))
            continue

        if equity > peak_equity:
            peak_equity = equity

        # Exits
        if position != 0:
            bars_held = i - entry_bar
            exit_trade = False
            exit_price = c
            exit_reason = "end"
            if position == 1 and l <= entry_stop:
                exit_trade = True
                exit_price = entry_stop
                exit_reason = "stop_loss"
            elif position == -1 and h >= entry_stop:
                exit_trade = True
                exit_price = entry_stop
                exit_reason = "stop_loss"
            elif bars_held >= max_holding_bars:
                exit_trade = True
                exit_reason = "max_holding"
            if exit_trade:
                pnl = ((exit_price - entry_price) * entry_lot * 10.0 if position == 1
                       else (entry_price - exit_price) * entry_lot * 10.0)
                fee = abs(pnl) * commission_rate
                slip = exit_price * entry_lot * (slippage_bps / 10000) * 10.0
                net = pnl - fee - slip
                trades.append({"side": "LONG" if position == 1 else "SHORT", "entry_price": entry_price,
                               "exit_price": exit_price, "pnl": net, "return_pct": net / equity,
                               "bars_held": bars_held, "exit_reason": exit_reason})
                equity += net
                daily_pnl += net
                position = 0

        # Entries
        if not halted and ts in signal_map:
            sig = signal_map[ts]
            sig_side = 1 if sig["side"] == "BUY" else -1
            if position == 0:
                # Position sizing: risk / (stop_points × $10 per lot)
                atr_val = sig.get("atr")
                stop_dist = (atr_val if atr_val is not None and atr_val > 0 else c * 0.01) * atr_mult
                if stop_dist <= 0:
                    stop_dist = c * 0.01
                risk_amount = equity * risk_per_trade_pct
                pos_size = risk_amount / (stop_dist * 10.0)
                pos_size = max(0.01, min(pos_size, 10.0))
                pos_size = round(pos_size / 0.01) * 0.01
                position = sig_side
                entry_price = c
                entry_bar = i
                entry_stop = c - stop_dist if sig_side == 1 else c + stop_dist
                entry_lot = pos_size
            elif position != sig_side:
                pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1
                       else (entry_price - c) * entry_lot * 10.0)
                fee = abs(pnl) * commission_rate
                slip = c * entry_lot * (slippage_bps / 10000) * 10.0
                net = pnl - fee - slip
                trades.append({"side": "LONG" if position == 1 else "SHORT", "entry_price": entry_price,
                               "exit_price": c, "pnl": net, "return_pct": net / equity,
                               "bars_held": i - entry_bar, "exit_reason": "reverse"})
                equity += net
                daily_pnl += net
                atr_val = sig.get("atr")
                stop_dist = (atr_val if atr_val is not None and atr_val > 0 else c * 0.01) * atr_mult
                if stop_dist <= 0:
                    stop_dist = c * 0.01
                risk_amount = equity * risk_per_trade_pct
                pos_size = risk_amount / (stop_dist * 10.0)
                pos_size = max(0.01, min(pos_size, 10.0))
                pos_size = round(pos_size / 0.01) * 0.01
                position = sig_side
                entry_price = c
                entry_bar = i
                entry_stop = c - stop_dist if sig_side == 1 else c + stop_dist
                entry_lot = pos_size

        equity_curve.append((ts, equity))

    if position != 0:
        c = close[-1]
        pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1
               else (entry_price - c) * entry_lot * 10.0)
        fee = abs(pnl) * commission_rate
        slip = c * entry_lot * (slippage_bps / 10000) * 10.0
        net = pnl - fee - slip
        trades.append({"side": "LONG" if position == 1 else "SHORT", "entry_price": entry_price,
                       "exit_price": c, "pnl": net, "return_pct": net / equity,
                       "bars_held": len(data) - entry_bar, "exit_reason": "end"})
        equity += net
        equity_curve.append((data_ts.iloc[-1], equity))

    metrics = compute_metrics(trades, equity_curve, initial_capital)
    return {"trades": trades, "equity_curve": equity_curve, "metrics": metrics, "halted": halted}


def compute_metrics(trades, equity_curve, initial_capital):
    if not trades:
        return {"error": "No trades"}
    returns = np.array([t["return_pct"] for t in trades])
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    total_pnl = float(np.sum(pnls))
    total_return = (equity_curve[-1][1] / equity_curve[0][1] - 1) if equity_curve else 0.0
    win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0.0
    gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 1e-8
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    expectancy = float(np.mean(returns))
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 1e-8
    sharpe = float(np.mean(returns) / std * np.sqrt(252)) if std > 0 else 0.0
    downside = returns[returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1e-8
    sortino = float(np.mean(returns) / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0
    equities = np.array([e[1] for e in equity_curve])
    peaks = np.maximum.accumulate(equities)
    drawdowns = (equities - peaks) / peaks
    max_dd = float(np.min(drawdowns)) * 100 if len(drawdowns) > 0 else 0.0
    avg_dd = float(np.mean(drawdowns[drawdowns < 0])) * 100 if any(drawdowns < 0) else 0.0
    max_consec_wins = 0
    max_consec_losses = 0
    consec = 0
    for t in trades:
        if t["pnl"] > 0:
            consec += 1
            max_consec_wins = max(max_consec_wins, consec)
        else:
            consec = 0
    consec = 0
    for t in trades:
        if t["pnl"] < 0:
            consec += 1
            max_consec_losses = max(max_consec_losses, consec)
        else:
            consec = 0
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    avg_bars = float(np.mean([t["bars_held"] for t in trades])) if trades else 0.0
    total_days = sum(t["bars_held"] for t in trades) / 24.0
    sorted_pnls = np.sort(pnls)[::-1]
    top1 = float(sorted_pnls[0] / total_pnl * 100) if len(sorted_pnls) > 0 and total_pnl > 0 else 0.0
    top5 = float(np.sum(sorted_pnls[:5]) / total_pnl * 100) if len(sorted_pnls) >= 5 and total_pnl > 0 else 0.0
    calmar = (total_return * 100) / abs(max_dd) if max_dd != 0 else float("inf")
    return {
        "total_trades": len(trades), "total_pnl": total_pnl, "total_return_pct": total_return * 100,
        "win_rate": win_rate, "profit_factor": profit_factor, "expectancy": expectancy,
        "sharpe_ratio": sharpe, "sortino_ratio": sortino, "calmar_ratio": calmar,
        "max_drawdown_pct": max_dd, "avg_drawdown_pct": avg_dd,
        "max_consecutive_wins": max_consec_wins, "max_consecutive_losses": max_consec_losses,
        "avg_win": avg_win, "avg_loss": avg_loss, "avg_bars_held": avg_bars,
        "total_days_trading": total_days, "profit_top1_pct": top1, "profit_top5_pct": top5,
    }
