"""Run simplified backtest with proper risk management."""
from __future__ import annotations

import numpy as np
import pandas as pd


def run_backtest(data: pd.DataFrame, signals: list[dict[str, Any]],
                 initial_capital: float = 5000.0,
                 contract_size: float = 100.0,
                 lot_step: float = 0.01,
                 min_lot: float = 0.01,
                 taker_fee: float = 0.0004,
                 slippage_bps: float = 2.0,
                 max_holding_bars: int = 100,
                 risk_per_trade: float = 0.01,
                 atr_period: int = 14,
                 atr_mult: float = 2.0) -> dict[str, Any]:
    """Run simplified backtest with ATR-based stops and position sizing."""

    if not signals or len(data) < 50:
        return {"error": "Insufficient data"}

    # Pre-calculate ATR for all bars using pandas
    df = data.copy()
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = (df['high'] - df['prev_close']).abs()
    df['tr3'] = (df['low'] - df['prev_close']).abs()
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(atr_period).mean()
    df['atr'] = df['atr'].fillna(df['close'] * 0.01)

    atr = df['atr'].values
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values

    # Create signal lookup
    signal_map = {}
    for sig in signals:
        ts = pd.Timestamp(sig["timestamp"])
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        signal_map[ts] = sig

    # Convert data timestamps to naive
    data_ts = df["timestamp"].apply(
        lambda x: pd.Timestamp(x).tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x)
    )

    equity = initial_capital
    position = 0  # 0=none, 1=long, -1=short
    entry_price = 0.0
    entry_bar = 0
    entry_stop = 0.0
    entry_lot = 0.0
    trades = []
    equity_curve = [(data_ts.iloc[0], equity)]

    for i in range(len(data)):
        ts = data_ts.iloc[i]
        c = close[i]
        h = high[i]
        l = low[i]

        # Check exit conditions
        if position != 0:
            bars_held = i - entry_bar
            exit_trade = False
            exit_price = c
            exit_reason = "end_of_data"

            # Check for stop loss hit intrabar
            if position == 1 and l <= entry_stop:
                exit_trade = True
                exit_price = entry_stop
                exit_reason = "stop_loss"
            elif position == -1 and h >= entry_stop:
                exit_trade = True
                exit_price = entry_stop
                exit_reason = "stop_loss"

            # Check for max holding bars
            if not exit_trade and bars_held >= max_holding_bars:
                exit_trade = True
                exit_reason = "max_holding"

            if exit_trade:
                if position == 1:
                    pnl = (exit_price - entry_price) * entry_lot * contract_size
                else:
                    pnl = (entry_price - exit_price) * entry_lot * contract_size

                fee = exit_price * contract_size * entry_lot * taker_fee
                slippage = exit_price * contract_size * entry_lot * (slippage_bps / 10000)
                net_pnl = pnl - fee - slippage

                trades.append({
                    "side": "LONG" if position == 1 else "SHORT",
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": net_pnl,
                    "return_pct": net_pnl / equity,
                    "bars_held": bars_held,
                    "exit_reason": exit_reason,
                })

                equity += net_pnl
                position = 0
                entry_price = 0.0
                entry_bar = 0

        # Check for signal
        if ts in signal_map:
            sig = signal_map[ts]
            sig_side = 1 if sig["side"] == "BUY" else -1

            if position == 0:
                # Calculate position size based on risk
                risk_amount = equity * risk_per_trade
                sl_distance = atr[i] * atr_mult
                if sl_distance <= 0:
                    sl_distance = c * 0.01

                # Position size = risk / stop_distance
                pos_size = risk_amount / sl_distance
                pos_size = max(min_lot, min(pos_size, 100.0))
                pos_size = round(pos_size / lot_step) * lot_step

                # Open new position with stop
                position = sig_side
                entry_price = c
                entry_bar = i
                entry_stop = sl_distance
                entry_lot = pos_size
            elif position != sig_side:
                # Close and reverse
                if position == 1:
                    pnl = (c - entry_price) * entry_lot * contract_size
                else:
                    pnl = (entry_price - c) * entry_lot * contract_size

                fee = c * contract_size * entry_lot * taker_fee
                slippage = c * contract_size * entry_lot * (slippage_bps / 10000)
                net_pnl = pnl - fee - slippage

                trades.append({
                    "side": "LONG" if position == 1 else "SHORT",
                    "entry_price": entry_price,
                    "exit_price": c,
                    "pnl": net_pnl,
                    "return_pct": net_pnl / equity,
                    "bars_held": i - entry_bar,
                    "exit_reason": "signal_reverse",
                })

                equity += net_pnl

                # Open reversed position
                risk_amount = equity * risk_per_trade
                sl_distance = atr[i] * atr_mult
                if sl_distance <= 0:
                    sl_distance = c * 0.01

                pos_size = risk_amount / sl_distance
                pos_size = max(min_lot, min(pos_size, 100.0))
                pos_size = round(pos_size / lot_step) * lot_step

                position = sig_side
                entry_price = c
                entry_bar = i
                entry_stop = sl_distance
                entry_lot = pos_size

        equity_curve.append((ts, equity))

    # Close final position
    if position != 0:
        c = close[-1]
        if position == 1:
            pnl = (c - entry_price) * entry_lot * contract_size
        else:
            pnl = (entry_price - c) * entry_lot * contract_size

        fee = c * contract_size * entry_lot * taker_fee
        slippage = c * contract_size * entry_lot * (slippage_bps / 10000)
        net_pnl = pnl - fee - slippage

        trades.append({
            "side": "LONG" if position == 1 else "SHORT",
            "entry_price": entry_price,
            "exit_price": c,
            "pnl": net_pnl,
            "return_pct": net_pnl / equity,
            "bars_held": len(data) - entry_bar,
            "exit_reason": "end_of_data",
        })

        equity += net_pnl
        equity_curve.append((data_ts.iloc[-1], equity))

    # Compute metrics
    metrics = compute_metrics(trades, equity_curve, initial_capital)

    return {
        "trades": trades,
        "equity_curve": equity_curve,
        "metrics": metrics,
    }


def compute_metrics(trades: list[dict], equity_curve: list, initial_capital: float) -> dict[str, float]:
    """Compute comprehensive performance metrics."""
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

    max_consec_loss = 0
    current_loss = 0
    for t in trades:
        if t["pnl"] < 0:
            current_loss += 1
            max_consec_loss = max(max_consec_loss, current_loss)
        else:
            current_loss = 0

    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

    avg_bars = float(np.mean([t["bars_held"] for t in trades])) if trades else 0.0

    # Days held
    total_days = sum(t["bars_held"] for t in trades) / 24.0  # H1 data

    return {
        "total_trades": len(trades),
        "total_pnl": total_pnl,
        "total_return_pct": total_return * 100,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": max_dd,
        "max_consecutive_losses": max_consec_loss,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_bars_held": avg_bars,
        "total_days_trading": total_days,
    }
