"""Standalone XAUUSD research script."""
from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_donchian(data, period):
    high = data["high"].values
    low = data["low"].values
    n = len(data)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period, n):
        upper[i] = np.max(high[i - period:i])
        lower[i] = np.min(low[i - period:i])
    return upper, lower


def calculate_atr(data, period):
    high = data["high"].values
    low = data["low"].values
    close = data["close"].values
    n = len(data)
    atr = np.full(n, np.nan)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    for i in range(period, n):
        atr[i] = np.mean(tr[i - period + 1:i + 1])
    return atr


def generate_signals(data, donchian_period=50, atr_mult=2.0, session_start=8, session_end=16):
    if len(data) < donchian_period + 10:
        return []
    upper, lower = calculate_donchian(data, donchian_period)
    atr = calculate_atr(data, 14)
    signals = []
    for i in range(donchian_period + 10, len(data)):
        if np.isnan(upper[i]) or np.isnan(atr[i]):
            continue
        ts = pd.Timestamp(data.iloc[i]["timestamp"])
        if ts.hour < session_start or ts.hour >= session_end:
            continue
        c = data.iloc[i]["close"]
        if c > upper[i]:
            signals.append({"timestamp": data.iloc[i]["timestamp"], "side": "BUY", "price": c, "atr": atr[i]})
        elif c < lower[i]:
            signals.append({"timestamp": data.iloc[i]["timestamp"], "side": "SELL", "price": c, "atr": atr[i]})
    return signals


def run_backtest(data, signals, initial_capital=5000.0, risk_pct=0.01, max_dd=0.10, max_hold=100, atr_mult=2.0):
    signal_map = {}
    for sig in signals:
        ts = pd.Timestamp(sig["timestamp"])
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        signal_map[ts] = sig

    data_ts = data["timestamp"].apply(lambda x: pd.Timestamp(x).tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x))
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

        if daily_pnl < -initial_capital * 0.05:
            if position != 0:
                pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1 else (entry_price - c) * entry_lot * 10.0)
                net = pnl * 0.9996
                trades.append({"entry_price": entry_price, "exit_price": c, "pnl": net, "return_pct": net / equity, "bars_held": i - entry_bar, "exit_reason": "daily_limit"})
                equity += net
                position = 0
            equity_curve.append((ts, equity))
            continue

        if equity < peak_equity * (1 - max_dd):
            if position != 0:
                pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1 else (entry_price - c) * entry_lot * 10.0)
                net = pnl * 0.9996
                trades.append({"entry_price": entry_price, "exit_price": c, "pnl": net, "return_pct": net / equity, "bars_held": i - entry_bar, "exit_reason": "max_dd"})
                equity += net
                position = 0
            halted = True
            equity_curve.append((ts, equity))
            continue

        if equity > peak_equity:
            peak_equity = equity

        if position != 0:
            bars_held = i - entry_bar
            exit_trade = False
            exit_price = c
            if position == 1 and l <= entry_stop:
                exit_trade, exit_price = True, entry_stop
            elif position == -1 and h >= entry_stop:
                exit_trade, exit_price = True, entry_stop
            elif bars_held >= max_hold:
                exit_trade = True
            if exit_trade:
                pnl = ((exit_price - entry_price) * entry_lot * 10.0 if position == 1 else (entry_price - exit_price) * entry_lot * 10.0)
                net = pnl * 0.9996
                trades.append({"entry_price": entry_price, "exit_price": exit_price, "pnl": net, "return_pct": net / equity, "bars_held": bars_held, "exit_reason": "stop" if bars_held < max_hold else "max_hold"})
                equity += net
                position = 0

        if not halted and ts in signal_map:
            sig = signal_map[ts]
            sig_side = 1 if sig["side"] == "BUY" else -1
            if position == 0:
                stop_dist = sig["atr"] * atr_mult
                if stop_dist <= 0: stop_dist = c * 0.01
                risk_amount = equity * risk_pct
                pos_size = risk_amount / (stop_dist * 10.0)
                pos_size = max(0.01, min(pos_size, 10.0))
                pos_size = round(pos_size / 0.01) * 0.01
                position = sig_side
                entry_price = c
                entry_bar = i
                entry_stop = c - stop_dist if sig_side == 1 else c + stop_dist
                entry_lot = pos_size
            elif position != sig_side:
                pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1 else (entry_price - c) * entry_lot * 10.0)
                net = pnl * 0.9996
                trades.append({"entry_price": entry_price, "exit_price": c, "pnl": net, "return_pct": net / equity, "bars_held": i - entry_bar, "exit_reason": "reverse"})
                equity += net
                stop_dist = sig["atr"] * atr_mult
                if stop_dist <= 0: stop_dist = c * 0.01
                risk_amount = equity * risk_pct
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
        pnl = ((c - entry_price) * entry_lot * 10.0 if position == 1 else (entry_price - c) * entry_lot * 10.0)
        net = pnl * 0.9996
        trades.append({"entry_price": entry_price, "exit_price": c, "pnl": net, "return_pct": net / equity, "bars_held": len(data) - entry_bar, "exit_reason": "end"})
        equity += net
        equity_curve.append((data_ts.iloc[-1], equity))

    return {"trades": trades, "equity_curve": equity_curve, "halted": halted}


def compute_metrics(trades, equity_curve, initial_capital):
    if not trades: return {"error": "No trades"}
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
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 1e-8
    sharpe = float(np.mean(returns) / std * np.sqrt(252)) if std > 0 else 0.0
    equities = np.array([e[1] for e in equity_curve])
    peaks = np.maximum.accumulate(equities)
    drawdowns = (equities - peaks) / peaks
    max_dd = float(np.min(drawdowns)) * 100 if len(drawdowns) > 0 else 0.0
    return {
        "total_trades": len(trades), "total_pnl": total_pnl, "total_return_pct": total_return * 100,
        "win_rate": win_rate, "profit_factor": profit_factor, "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
    }


def main():
    print("="*80)
    print("  XAUUSD $5K PROP-FIRM RESEARCH")
    print("="*80)

    data = pd.read_parquet("data/store/max/XAUUSD_H1_MAX.parquet")
    test_data = data[data["timestamp"] >= "2025-01-01"].reset_index(drop=True)
    print(f"Test set: {len(test_data)} bars")

    signals = generate_signals(test_data, donchian_period=50, atr_mult=2.0, session_start=8, session_end=16)
    print(f"Signals: {len(signals)}")

    # Risk levels
    print("\n[RISK LEVELS]")
    for risk in [0.0025, 0.005, 0.01]:
        r = run_backtest(test_data, signals, risk_pct=risk, max_dd=0.10)
        m = compute_metrics(r["trades"], r["equity_curve"], 5000)
        if "error" not in m:
            status = "HALTED" if r["halted"] else "OK"
            print(f"  {risk*100:.2f}% (${5000*risk:.0f}): trades={m['total_trades']:>3}  PF={m['profit_factor']:.2f}  Sharpe={m['sharpe_ratio']:.2f}  DD={m['max_drawdown_pct']:.1f}%  Return={m['total_return_pct']:.1f}%  {status}")

    # Parameter perturbation
    print("\n[PARAMETER PERTURBATION] 0.5% risk")
    for period in [40, 45, 50, 55, 60]:
        for mult in [1.5, 1.75, 2.0, 2.25, 2.5]:
            sigs = generate_signals(test_data, donchian_period=period, atr_mult=mult)
            r = run_backtest(test_data, sigs, risk_pct=0.005, max_dd=0.10, atr_mult=mult)
            m = compute_metrics(r["trades"], r["equity_curve"], 5000)
            if "error" not in m:
                status = "HALTED" if r["halted"] else "OK"
                print(f"  D={period:>2} M={mult:.2f}: trades={m['total_trades']:>3}  PF={m['profit_factor']:.2f}  Sharpe={m['sharpe_ratio']:.2f}  DD={m['max_drawdown_pct']:.1f}%  {status}")

    # Walk-forward
    print("\n[WALK-FORWARD] 0.5% risk")
    windows = [
        ("2018-01-01", "2020-12-31", "2021-01-01", "2021-12-31"),
        ("2019-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
        ("2020-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
        ("2021-01-01", "2023-12-31", "2024-01-01", "2024-12-31"),
        ("2022-01-01", "2024-12-31", "2025-01-01", "2025-12-31"),
        ("2023-01-01", "2025-12-31", "2026-01-01", "2026-12-31"),
    ]
    for train_start, train_end, test_start, test_end in windows:
        test_mask = (data["timestamp"] >= test_start) & (data["timestamp"] <= test_end)
        test_data_wf = data[test_mask].reset_index(drop=True)
        if len(test_data_wf) < 50: continue
        sigs = generate_signals(test_data_wf, donchian_period=50, atr_mult=2.0)
        r = run_backtest(test_data_wf, sigs, risk_pct=0.005, max_dd=0.10, atr_mult=2.0)
        m = compute_metrics(r["trades"], r["equity_curve"], 5000)
        if "error" not in m:
            status = "HALTED" if r["halted"] else "OK"
            print(f"  {test_start}: trades={m['total_trades']:>3}  PF={m['profit_factor']:.2f}  Sharpe={m['sharpe_ratio']:.2f}  DD={m['max_drawdown_pct']:.1f}%  {status}")

    # Monte Carlo
    print("\n[MONTE CARLO 10K]")
    r = run_backtest(test_data, signals, risk_pct=0.005, max_dd=0.10)
    returns = np.array([t["return_pct"] for t in r["trades"]])
    max_dds = []
    breaches = 0
    for _ in range(10000):
        shuffled = np.random.permutation(returns)
        equity = 5000.0
        peak = equity
        max_dd = 0.0
        for ret in shuffled:
            equity *= (1 + ret)
            if equity > peak: peak = equity
            dd = (equity - peak) / peak
            if dd < max_dd: max_dd = dd
        max_dds.append(max_dd * 100)
        if abs(max_dd) > 0.10: breaches += 1
    max_dds = np.array(max_dds)
    print(f"  Median DD: {np.median(max_dds):.1f}%")
    print(f"  P95 DD: {np.percentile(max_dds, 95):.1f}%")
    print(f"  Breach probability: {breaches/100:.1f}%")

    print("\n" + "="*80)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
