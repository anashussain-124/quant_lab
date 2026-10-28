
from pathlib import Path
import json
import math
import sys

import numpy as np
import pandas as pd


# ======================== CONFIGURATION ========================

START_BALANCE = 5000.0
PHASE_TARGETS = {"Phase 1": 400.0, "Phase 2": 250.0}

DAILY_LOSS_LIMIT = 250.0
STATIC_LOSS_FLOOR = 4500.0

RISK_PER_TRADE = 8.0
CONTRACT_SIZE = 100.0       # XAUUSD: assumed 100 oz per lot
MIN_LOT = 0.01
LOT_STEP = 0.01

ATR_PERIOD = 14
BREAKOUT_LOOKBACK = 20
ATR_STOP_MULTIPLE = 1.6
REWARD_RISK = 2.0

# Estimated execution assumptions; adjust using verified broker data.
SLIPPAGE_PER_SIDE = 0.03    # dollars per ounce
COMMISSION_PER_LOT_ROUND_TURN = 5.0
COMMISSION_IS_ROUND_TURN = True

# Spread is read from spread_points in the CSV.
# Point value is 0.01 for this export.
POINT_VALUE = 0.01

# FundingPips daily reset is modeled at UTC+3.
PLATFORM_UTC_OFFSET_HOURS = 3

DATA_FILE = Path(
    r"research_bundle\mt5_export\XAUUSD_M5_20231022_20261022_UTC.csv"
)

OUTPUT_DIR = Path("fundingpips_research_output")


# ======================== DATA PREPARATION ========================

def load_data():
    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"Cannot find data file: {DATA_FILE.resolve()}\n"
            "Run this script from your quant_lab project directory."
        )

    df = pd.read_csv(DATA_FILE)

    required = {
        "time_utc", "open", "high", "low", "close", "spread_points"
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")

    df["time_utc"] = pd.to_datetime(
        df["time_utc"], utc=True, errors="coerce"
    )
    for col in ["open", "high", "low", "close", "spread_points"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=["time_utc", "open", "high", "low", "close",
                "spread_points"]
    )
    df = df.sort_values("time_utc")
    df = df.drop_duplicates("time_utc", keep="last")
    df = df.set_index("time_utc")

    valid_ohlc = (
        (df["high"] >= df[["open", "close", "low"]].max(axis=1))
        & (df["low"] <= df[["open", "close", "high"]].min(axis=1))
        & (df["spread_points"] >= 0)
    )
    df = df.loc[valid_ohlc].copy()

    if len(df) < 1000:
        raise ValueError(f"Not enough valid bars: {len(df)}")

    # Zero spreads are retained in the original data, but execution
    # assumes at least the smallest positive observed spread.
    positive = df.loc[df["spread_points"] > 0, "spread_points"]
    if positive.empty:
        raise ValueError("No positive spread observations in CSV.")

    minimum_positive_spread = float(positive.min())
    df["spread_points_used"] = df["spread_points"].where(
        df["spread_points"] > 0, minimum_positive_spread
    )
    df["spread_price"] = df["spread_points_used"] * POINT_VALUE

    return df


def add_indicators(df):
    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = true_range.rolling(ATR_PERIOD).mean()

    # Shifted channel: current candle is never included in its own
    # breakout threshold.
    df["prior_high"] = (
        df["high"].rolling(BREAKOUT_LOOKBACK).max().shift(1)
    )
    df["prior_low"] = (
        df["low"].rolling(BREAKOUT_LOOKBACK).min().shift(1)
    )

    df["long_signal"] = df["close"] > df["prior_high"]
    df["short_signal"] = df["close"] < df["prior_low"]

    return df


# ======================== EXECUTION MODEL ========================

def floor_lot(value):
    if not math.isfinite(value) or value < MIN_LOT:
        return 0.0
    return math.floor((value + 1e-10) / LOT_STEP) * LOT_STEP


def commission(lots):
    if COMMISSION_IS_ROUND_TURN:
        return lots * COMMISSION_PER_LOT_ROUND_TURN
    return lots * COMMISSION_PER_LOT_ROUND_TURN * 2.0


def platform_day(timestamp):
    return (timestamp + pd.Timedelta(
        hours=PLATFORM_UTC_OFFSET_HOURS
    )).date()


def run_phase(df, phase_name, target_profit):
    balance = START_BALANCE
    equity_peak = START_BALANCE
    daily_reference = START_BALANCE
    current_day = None

    trades = []
    active = None
    breach_reason = None
    target_reached = False
    trading_days = set()

    bars = df.reset_index()

    def close_trade(trade, timestamp, exit_price, reason):
        nonlocal balance

        lots = trade["lots"]
        direction = trade["direction"]
        entry_fill = trade["entry_fill"]

        if direction == "LONG":
            gross = (exit_price - entry_fill) * CONTRACT_SIZE * lots
        else:
            gross = (entry_fill - exit_price) * CONTRACT_SIZE * lots

        exit_commission = commission(lots)
        net = gross - trade["entry_commission"] - exit_commission
        balance += gross - exit_commission

        trades.append({
            "phase": phase_name,
            "entry_time_utc": trade["entry_time"].isoformat(),
            "exit_time_utc": timestamp.isoformat(),
            "direction": direction,
            "entry_price": trade["entry_fill"],
            "exit_price": exit_price,
            "lots": lots,
            "stop_distance": trade["stop_distance"],
            "gross_pnl": round(gross, 2),
            "entry_commission": round(trade["entry_commission"], 2),
            "exit_commission": round(exit_commission, 2),
            "net_pnl": round(net, 2),
            "balance_after": round(balance, 2),
            "exit_reason": reason,
        })

    for i in range(1, len(bars)):
        row = bars.iloc[i]
        prev = bars.iloc[i - 1]
        timestamp = row["time_utc"]

        if current_day != platform_day(timestamp):
            current_day = platform_day(timestamp)
            # Daily limit reference uses the higher of balance or
            # equity at the platform-day reset.
            if active is None:
                daily_reference = balance
            else:
                daily_reference = max(
                    balance,
                    balance + active.get("unrealized_pnl", 0.0)
                )

        # Update open-trade mark-to-market using this bar's spread.
        if active is not None:
            half_spread = float(row["spread_price"])

            if active["direction"] == "LONG":
                mark = float(row["close"]) - half_spread
                unrealized = (
                    mark - active["entry_fill"]
                ) * CONTRACT_SIZE * active["lots"]
            else:
                mark = float(row["close"]) + half_spread
                unrealized = (
                    active["entry_fill"] - mark
                ) * CONTRACT_SIZE * active["lots"]

            active["unrealized_pnl"] = unrealized
            equity = balance + unrealized
        else:
            equity = balance

        equity_peak = max(equity_peak, equity)

        # Check limits before processing the current bar's exits.
        # This is deliberately conservative, but bar data cannot
        # determine the exact sequence of intrabar events.
        if equity <= STATIC_LOSS_FLOOR:
            breach_reason = "static_loss_floor"
            break

        if equity <= daily_reference - DAILY_LOSS_LIMIT:
            breach_reason = "daily_loss_limit"
            break

        if active is not None:
            direction = active["direction"]
            spread = float(row["spread_price"])

            # Approximate ask-side prices from bid OHLC and spread.
            if direction == "LONG":
                stop_hit = float(row["low"]) - spread <= active["stop"]
                target_hit = float(row["high"]) - spread >= active["target"]

                # Conservative tie-break: stop first if both touched.
                if stop_hit:
                    exit_price = active["stop"] - SLIPPAGE_PER_SIDE
                    close_trade(active, timestamp, exit_price, "stop_loss")
                    active = None
                elif target_hit:
                    exit_price = active["target"] - SLIPPAGE_PER_SIDE
                    close_trade(active, timestamp, exit_price, "take_profit")
                    active = None

            else:
                stop_hit = float(row["high"]) + spread >= active["stop"]
                target_hit = float(row["low"]) + spread <= active["target"]

                if stop_hit:
                    exit_price = active["stop"] + SLIPPAGE_PER_SIDE
                    close_trade(active, timestamp, exit_price, "stop_loss")
                    active = None
                elif target_hit:
                    exit_price = active["target"] + SLIPPAGE_PER_SIDE
                    close_trade(active, timestamp, exit_price, "take_profit")
                    active = None

            if balance <= STATIC_LOSS_FLOOR:
                breach_reason = "static_loss_floor"
                break

            if balance <= daily_reference - DAILY_LOSS_LIMIT:
                breach_reason = "daily_loss_limit"
                break

            if balance >= START_BALANCE + target_profit:
                target_reached = True
                break

            # One trade at a time.
            if active is not None:
                continue

        # Signal is evaluated on the previous completed candle;
        # entry occurs at the current candle open.
        atr = float(prev["atr"]) if pd.notna(prev["atr"]) else np.nan
        if not math.isfinite(atr) or atr <= 0:
            continue

        long_signal = bool(prev["long_signal"])
        short_signal = bool(prev["short_signal"])

        if long_signal == short_signal:
            continue

        stop_distance = atr * ATR_STOP_MULTIPLE
        lots = floor_lot(
            RISK_PER_TRADE / (stop_distance * CONTRACT_SIZE)
        )

        if lots < MIN_LOT:
            continue

        # Don't enter if the minimum lot risks more than the configured
        # risk budget before costs.
        estimated_risk = stop_distance * CONTRACT_SIZE * lots
        if estimated_risk > RISK_PER_TRADE + 1e-8:
            continue

        spread = float(row["spread_price"])
        bar_open = float(row["open"])

        if long_signal:
            direction = "LONG"
            entry_fill = bar_open + spread + SLIPPAGE_PER_SIDE
            stop = entry_fill - stop_distance
            target = entry_fill + stop_distance * REWARD_RISK
        else:
            direction = "SHORT"
            entry_fill = bar_open - SLIPPAGE_PER_SIDE
            stop = entry_fill + stop_distance
            target = entry_fill - stop_distance * REWARD_RISK

        entry_commission = commission(lots)
        balance -= entry_commission

        active = {
            "direction": direction,
            "entry_time": timestamp,
            "entry_fill": entry_fill,
            "stop": stop,
            "target": target,
            "stop_distance": stop_distance,
            "lots": lots,
            "entry_commission": entry_commission,
            "unrealized_pnl": -entry_commission,
        }
        trading_days.add(platform_day(timestamp))

    # Force-close any position at the last available close so the
    # final balance includes an explicit end-of-data exit.
    open_trade_at_end = active is not None
    if active is not None:
        last = bars.iloc[-1]
        timestamp = last["time_utc"]
        spread = float(last["spread_price"])

        if active["direction"] == "LONG":
            exit_price = float(last["close"]) - spread - SLIPPAGE_PER_SIDE
        else:
            exit_price = float(last["close"]) + spread + SLIPPAGE_PER_SIDE

        close_trade(active, timestamp, exit_price, "end_of_data")

    trade_df = pd.DataFrame(trades)

    if not trade_df.empty:
        wins = int((trade_df["net_pnl"] > 0).sum())
        losses = int((trade_df["net_pnl"] < 0).sum())
        gross_profit = float(trade_df.loc[
            trade_df["net_pnl"] > 0, "net_pnl"
        ].sum())
        gross_loss = abs(float(trade_df.loc[
            trade_df["net_pnl"] < 0, "net_pnl"
        ].sum()))
        profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else None
        )
        win_rate = wins / len(trade_df) * 100
    else:
        wins = losses = 0
        gross_profit = gross_loss = 0.0
        profit_factor = None
        win_rate = 0.0

    summary = {
        "phase": phase_name,
        "starting_balance": START_BALANCE,
        "target_profit": target_profit,
        "final_balance": round(balance, 2),
        "net_profit": round(balance - START_BALANCE, 2),
        "target_reached": target_reached,
        "breach_reason": breach_reason,
        "trades": len(trade_df),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 2),
        "gross_profit_from_winners": round(gross_profit, 2),
        "gross_loss_from_losers": round(gross_loss, 2),
        "profit_factor": (
            round(profit_factor, 4)
            if profit_factor is not None else None
        ),
        "max_observed_equity": round(equity_peak, 2),
        "platform_trading_days": len(trading_days),
        "open_trade_at_end_forced_closed": open_trade_at_end,
        "commission_per_lot_round_turn_assumption":
            COMMISSION_PER_LOT_ROUND_TURN,
        "data_rows": len(df),
        "data_source": str(DATA_FILE),
        "warning": (
            "Research estimate only. Not a verified FundingPips "
            "execution or compliance report."
        ),
    }

    return trade_df, summary


# ======================== MAIN ========================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("FUNDINGPIPS $5,000 2-STEP | XAUUSD M5 RESEARCH BACKTEST")
    print("=" * 68)
    print(f"Data file: {DATA_FILE}")
    print("Loading data...")

    df = load_data()
    df = add_indicators(df)

    print(f"Valid M5 bars: {len(df):,}")
    print(f"Start UTC: {df.index.min()}")
    print(f"End UTC:   {df.index.max()}")
    print(
        "Spread median / 95th percentile ($): "
        f"{df['spread_price'].median():.3f} / "
        f"{df['spread_price'].quantile(0.95):.3f}"
    )
    print(f"Fixed risk per trade: ${RISK_PER_TRADE:.2f}")
    print(f"Stop: {ATR_STOP_MULTIPLE} x ATR")
    print(f"Target: {REWARD_RISK}R")
    print()

    all_summaries = []

    for phase, target in PHASE_TARGETS.items():
        print(f"Running {phase}...")
        trades, summary = run_phase(df.copy(), phase, target)

        filename = (
            "phase1" if phase == "Phase 1" else "phase2"
        )
        trade_path = OUTPUT_DIR / f"{filename}_trade_log.csv"
        trades.to_csv(trade_path, index=False)

        all_summaries.append(summary)

        print(f"  Trades:       {summary['trades']}")
        print(f"  Net P/L:      ${summary['net_profit']:.2f}")
        print(f"  Final balance:${summary['final_balance']:.2f}")
        print(f"  Win rate:     {summary['win_rate_pct']:.2f}%")
        print(f"  Profit factor:{summary['profit_factor']}")
        print(f"  Target hit:   {summary['target_reached']}")
        print(f"  Breach:       {summary['breach_reason']}")
        print(f"  Trade log:    {trade_path}")
        print()

    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(all_summaries, indent=2),
        encoding="utf-8"
    )

    print("=" * 68)
    print(f"Summary saved: {summary_path}")
    print(f"Output folder: {OUTPUT_DIR.resolve()}")
    print()
    print("IMPORTANT LIMITATIONS:")
    print("- MT5 demo spreads may differ from FundingPips spreads.")
    print("- Commission convention must be verified for your account.")
    print("- Intrabar equity breaches cannot be exactly reconstructed")
    print("  from M5 OHLC data.")
    print("- Phase 1 and Phase 2 are independent runs, not a sequential")
    print("  challenge simulation.")
    print("- A backtest result does not establish a genuine trading edge.")
    print("=" * 68)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise
