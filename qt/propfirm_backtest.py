"""
propfirm_backtest.py  --  Prop-firm-aware backtesting engine (XAUUSD-ready)

Research only. No order routing, no broker connection required.

WHAT THIS GIVES YOU
  * Strategy interface: a strategy only emits signals. The engine owns fills,
    costs, sizing, risk and prop-firm rule enforcement. Swap in an ICT
    strategy later without touching the engine.
  * Honest fills: signal at bar close -> fill at NEXT bar open. Bid-based MT5
    bars: longs buy at ask (bid + spread), shorts sell at bid.
  * Conservative intrabar logic: SL and TP in the same bar -> SL assumed first.
    Gaps through stops fill at the open (worse price), not at the stop.
  * Prop rules enforced on WORST-CASE intrabar equity: daily loss, max loss
    (static or trailing), profit target, minimum trading days, time limit.
  * Rolling challenge simulation: start a fresh challenge on many different
    dates and report pass rate, failure reasons and days-to-pass. This is the
    number that matters for a challenge, not one equity curve.

USAGE
  python propfirm_backtest.py --synthetic                 # plumbing test only
  python propfirm_backtest.py --csv XAUUSD_M5.csv --server-utc-offset 2
  python propfirm_backtest.py --mt5 XAUUSD --days 365 --server-utc-offset 2

FundingPips 2-Step Standard presets model the 5% daily-loss configuration.
Verify the daily-loss option on your purchase checkout before trusting results.
This remains historical research only; no order routing is implemented.
"""

from __future__ import annotations

import argparse
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


# =============================================================================
# 1. CONFIGURATION
# =============================================================================

@dataclass(frozen=True)
class Instrument:
    symbol: str = "XAUUSD"
    point: float = 0.01            # price increment of 1 "point"
    contract_size: float = 100.0   # ounces per 1.00 lot (check your broker)
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 50.0


@dataclass(frozen=True)
class Costs:
    commission_per_lot_side: float = 3.5   # account currency, per lot, per side
    slippage: float = 0.05                 # adverse price units on market fills/stops
    fixed_spread_points: float = 25.0      # used if the data has no spread column
    spread_mult: float = 1.0               # stress test: 2.0 doubles every spread


@dataclass(frozen=True)
class PropRules:
    initial_balance: float = 100_000.0
    profit_target_pct: float = 0.08
    max_daily_loss_pct: float = 0.05       # % of INITIAL balance
    max_total_loss_pct: float = 0.10       # % of INITIAL balance
    trailing_total: bool = False           # True = floor trails peak closed equity
    min_trading_days: int = 4
    max_calendar_days: int | None = 30     # None = unlimited
    reset_tz: str = "Europe/Prague"        # daily-loss reset timezone
    reset_hour: int = 0


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.0025         # fraction of current balance at the stop
    soft_daily_stop_pct: float = 0.025     # OWN limit, well inside the firm's 5%
    cap_risk_to_daily_room: bool = True    # never risk more than 80% of remaining room
    entry_hours_utc: tuple[int, int] = (7, 20)
    flatten_hour_utc: int | None = 20      # day-trade: flat by this hour every day
    friday_flatten_hour_utc: int | None = 19
    max_hold_bars: int | None = None


# FundingPips 2-Step Standard. The firm offers a 5% daily-loss configuration
# (3 minimum trading days) and a 3% daily-loss add-on (no minimum days).
# Platform midnight is UTC+3, represented as UTC timestamps grouped after
# shifting by 21 hours. Phase 1 and Phase 2 are separate runs, not a combined
# two-phase pass simulation.
FUNDINGPIPS_STANDARD_5PCT = dict(
    initial_balance=5_000.0,
    max_daily_loss_pct=0.05,
    max_total_loss_pct=0.10,
    trailing_total=False,
    min_trading_days=3,
    max_calendar_days=None,
    reset_tz="UTC",
    reset_hour=21,
)
FUNDINGPIPS_STANDARD_3PCT = {
    **FUNDINGPIPS_STANDARD_5PCT,
    "max_daily_loss_pct": 0.03,
    "min_trading_days": 0,
}

PRESETS = {
    "fundingpips_2step_p1": PropRules(
        **FUNDINGPIPS_STANDARD_5PCT, profit_target_pct=0.08),
    "fundingpips_2step_p2": PropRules(
        **FUNDINGPIPS_STANDARD_5PCT, profit_target_pct=0.05),
    "fundingpips_2step_p1_3pct_daily": PropRules(
        **FUNDINGPIPS_STANDARD_3PCT, profit_target_pct=0.08),
    "fundingpips_2step_p2_3pct_daily": PropRules(
        **FUNDINGPIPS_STANDARD_3PCT, profit_target_pct=0.05),
    "generic_2step_p1": PropRules(profit_target_pct=0.08),
    "generic_2step_p2": PropRules(profit_target_pct=0.05, min_trading_days=4),
    "generic_1step_trailing": PropRules(
        profit_target_pct=0.10, max_daily_loss_pct=0.04,
        max_total_loss_pct=0.06, trailing_total=True,
        min_trading_days=3, max_calendar_days=None),
}


# =============================================================================
# 2. DATA
# =============================================================================

def _finalize(df: pd.DataFrame, server_offset_hours: float) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index) - pd.Timedelta(hours=server_offset_hours)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df[~df.index.duplicated(keep="first")].sort_index()
    need = ["open", "high", "low", "close"]
    if df[need].isna().any().any():
        raise ValueError("Missing OHLC values.")
    if ((df["high"] < df["low"]) | (df[need] <= 0).any(axis=1)).any():
        raise ValueError("Invalid OHLC rows.")
    return df


def load_csv(path: str, server_offset_hours: float = 0.0) -> pd.DataFrame:
    """MT5 export (<DATE> <TIME> ... <SPREAD>) or a CSV with a 'time' column.
    MT5 server time is usually UTC+2/+3: pass --server-utc-offset."""
    raw = pd.read_csv(path, sep=None, engine="python")
    raw.columns = [c.strip("<>").lower() for c in raw.columns]
    if "date" in raw.columns and "time" in raw.columns:
        ts = pd.to_datetime(raw["date"].astype(str) + " " + raw["time"].astype(str))
    else:
        ts = pd.to_datetime(raw["time"])
    raw.index = ts
    cols = [c for c in ["open", "high", "low", "close", "spread"] if c in raw.columns]
    return _finalize(raw[cols], server_offset_hours)


def load_mt5(symbol: str, days: int, server_offset_hours: float = 0.0) -> pd.DataFrame:
    import MetaTrader5 as mt5  # Windows + installed terminal only
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        end = datetime.now(timezone.utc) + timedelta(hours=server_offset_hours)
        start = end - timedelta(days=days)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No rates: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df.index = pd.to_datetime(df["time"], unit="s")
        return _finalize(df[["open", "high", "low", "close", "spread"]],
                         server_offset_hours)
    finally:
        mt5.shutdown()


def synthetic_data(days: int = 250, seed: int = 7) -> pd.DataFrame:
    """Random-walk M5 bars with volatility clustering. NO edge by construction:
    use it to test plumbing and to see what a no-edge strategy does to a challenge."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days * 288, freq="5min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    vol = 0.00035 * np.exp(0.3 * np.cumsum(rng.normal(0, 0.02, n)) % 1.5)
    rets = rng.normal(0, 1, n) * vol
    close = 2000 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    rng_hl = np.abs(rng.normal(0, 1, n)) * vol * close * 0.6
    high = np.maximum(open_, close) + rng_hl
    low = np.minimum(open_, close) - rng_hl
    spread = rng.integers(18, 35, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "spread": spread}, index=idx)


# =============================================================================
# 3. STRATEGY INTERFACE  (the only thing you replace for ICT)
# =============================================================================

class Strategy(ABC):
    """generate() must be CAUSAL: row i may use only bars <= i.
    Returns a DataFrame aligned to df with columns:
        side        int  : +1 long, -1 short, 0 none  (decided at bar-i close)
        stop_dist   float: stop distance in price units (> 0 where side != 0)
        target      float: absolute take-profit price, NaN for none
        exit_long   bool : close an open long at next open
        exit_short  bool : close an open short at next open
    The engine fills at bar i+1's open.
    """

    @abstractmethod
    def generate(self, df: pd.DataFrame) -> pd.DataFrame: ...


@dataclass(frozen=True)
class MeanReversionParams:
    ema_period: int = 50
    z_window: int = 50
    atr_period: int = 14
    entry_z: float = 2.0
    exit_z: float = 0.3
    stop_atr_mult: float = 2.0
    min_rr: float = 0.5        # skip if (distance to mean) / stop < min_rr


class EmaZMeanReversion(Strategy):
    """Refactor of the MT5 prototype. EMA-centred z-score + ATR stop.
    A candidate signal, NOT a fitted OU process and NOT a proven edge."""

    def __init__(self, p: MeanReversionParams = MeanReversionParams()):
        self.p = p

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        c, h, l = df["close"], df["high"], df["low"]
        ema = c.ewm(span=p.ema_period, adjust=False).mean()
        dev = c - ema
        z = dev / dev.rolling(p.z_window).std(ddof=1)
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(p.atr_period).mean()

        stop = atr * p.stop_atr_mult
        side = np.where(z <= -p.entry_z, 1, np.where(z >= p.entry_z, -1, 0))
        reward = (ema - c).abs()
        side = np.where(reward / stop >= p.min_rr, side, 0)
        side = np.where(z.isna() | atr.isna(), 0, side)

        return pd.DataFrame({
            "side": side.astype(np.int8),
            "stop_dist": stop,
            "target": ema,
            "exit_long": (z >= -p.exit_z).fillna(False),
            "exit_short": (z <= p.exit_z).fillna(False),
        }, index=df.index)


@dataclass(frozen=True)
class DonchianTrendParams:
    breakout_bars: int = 20
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    reward_risk: float = 2.0
    ema_fast: int = 50
    ema_slow: int = 200
    london_start_hour: int = 8
    london_end_hour: int = 12


class DonchianTrendBreakout(Strategy):
    """Causal M5 Donchian breakout with a completed-M15 EMA trend filter.

    Signal is decided at M5 bar close and filled by the engine at next bar open.
    London window uses Europe/London local time so DST is handled. This is a
    testable hypothesis, not a claim of profitability.
    """

    def __init__(self, p: DonchianTrendParams = DonchianTrendParams()):
        if p.breakout_bars < 2 or p.atr_period < 2 or p.ema_fast < 2 or p.ema_slow <= p.ema_fast:
            raise ValueError("Invalid Donchian/ATR/EMA periods.")
        if not 0 <= p.london_start_hour < p.london_end_hour <= 24:
            raise ValueError("London session must be a valid same-day hour range.")
        self.p = p

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        c, h, l = df["close"], df["high"], df["low"]
        prior_high = h.shift(1).rolling(p.breakout_bars).max()
        prior_low = l.shift(1).rolling(p.breakout_bars).min()
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = tr.rolling(p.atr_period).mean()

        # Resample timestamped M5 bars to M15. Shift EMA series by one full
        # M15 candle before forward filling, so a signal never sees an
        # unfinished/current higher-timeframe candle.
        m15_close = c.resample("15min", label="right", closed="right").last()
        fast = m15_close.ewm(span=p.ema_fast, adjust=False).mean().shift(1)
        slow = m15_close.ewm(span=p.ema_slow, adjust=False).mean().shift(1)
        fast_m5 = fast.reindex(df.index, method="ffill")
        slow_m5 = slow.reindex(df.index, method="ffill")

        local = df.index.tz_convert("Europe/London")
        in_session = (local.hour >= p.london_start_hour) & (local.hour < p.london_end_hour)
        long_signal = (c > prior_high) & (fast_m5 > slow_m5) & in_session
        short_signal = (c < prior_low) & (fast_m5 < slow_m5) & in_session
        side = np.where(long_signal, 1, np.where(short_signal, -1, 0))
        stop = atr * p.stop_atr_mult
        side = np.where(atr.notna() & fast_m5.notna() & slow_m5.notna() & (stop > 0), side, 0)
        side = side.astype(np.int8)
        target = c.to_numpy(float) + side * stop.to_numpy(float) * p.reward_risk
        target = np.where(side != 0, target, np.nan)

        # Close an existing position on a confirmed opposite trend break.
        exit_long = (side == -1)
        exit_short = (side == 1)
        return pd.DataFrame({
            "side": side, "stop_dist": stop, "target": target,
            "exit_long": exit_long, "exit_short": exit_short,
        }, index=df.index)


class ICTStrategy(Strategy):
    """PLACEHOLDER for the ICT / day-trade conversion. Plug in, same contract:
      1. HTF bias        (H1/H4 resample, shifted by one bar to stay causal)
      2. Session/killzone window (London 07-10 UTC, NY 12-15 UTC)
      3. Liquidity sweep (prior session/day high-low taken, then rejected)
      4. Displacement + Fair Value Gap / order block (3-candle rule, causal)
      5. Entry limit at FVG/OB, stop beyond sweep extreme, target at opposite
         liquidity or fixed R.
    Needs a limit-order fill model and (ideally) M1 data -- extend the engine's
    entry step before trusting results."""

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError("Implement ICT rules here.")


# =============================================================================
# 4. PREPARED ARRAYS  (built once, reused for hundreds of challenge attempts)
# =============================================================================

@dataclass
class Prepared:
    index: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    sp: np.ndarray        # spread in price units
    hour: np.ndarray      # UTC hour
    dow: np.ndarray
    day_id: np.ndarray    # prop-firm day (reset tz / reset hour)
    ts_ns: np.ndarray
    side: np.ndarray
    stop_dist: np.ndarray
    target: np.ndarray
    exit_long: np.ndarray
    exit_short: np.ndarray


def prepare(df: pd.DataFrame, sig: pd.DataFrame, inst: Instrument,
            costs: Costs, rules: PropRules) -> Prepared:
    if not df.index.equals(sig.index):
        raise ValueError("Signals must align with market data index.")
    if "spread" in df.columns:
        sp = df["spread"].to_numpy(float) * inst.point
    else:
        sp = np.full(len(df), costs.fixed_spread_points * inst.point)
    sp = sp * costs.spread_mult
    local = df.index.tz_convert(rules.reset_tz) - pd.Timedelta(hours=rules.reset_hour)
    day_id = (local.year.to_numpy() * 10_000 + local.month.to_numpy() * 100
              + local.day.to_numpy())
    return Prepared(
        index=df.index,
        o=df["open"].to_numpy(float), h=df["high"].to_numpy(float),
        l=df["low"].to_numpy(float), c=df["close"].to_numpy(float),
        sp=sp, hour=df.index.hour.to_numpy(), dow=df.index.dayofweek.to_numpy(),
        day_id=day_id, ts_ns=df.index.asi8,
        side=sig["side"].to_numpy(np.int8),
        stop_dist=sig["stop_dist"].to_numpy(float),
        target=sig["target"].to_numpy(float),
        exit_long=sig["exit_long"].to_numpy(bool),
        exit_short=sig["exit_short"].to_numpy(bool),
    )


# =============================================================================
# 5. ENGINE
# =============================================================================

@dataclass
class BacktestResult:
    outcome: str                 # PASS / FAIL_DAILY_LOSS / FAIL_MAX_LOSS / TIMEOUT / INCOMPLETE
    start: pd.Timestamp
    end: pd.Timestamp
    calendar_days: float
    trading_days: int
    final_balance: float
    return_pct: float
    max_drawdown_pct: float
    worst_daily_loss_pct: float
    trades: pd.DataFrame
    equity: pd.Series


class Backtester:
    def __init__(self, inst: Instrument = Instrument(), costs: Costs = Costs(),
                 rules: PropRules = PropRules(), risk: RiskConfig = RiskConfig()):
        self.inst, self.costs, self.rules, self.risk = inst, costs, rules, risk

    # ---- sizing -------------------------------------------------------------
    def _volume(self, balance: float, stop_dist: float, daily_room: float) -> tuple[float, float]:
        i, k, r = self.inst, self.costs, self.risk
        risk_cash = balance * r.risk_per_trade
        if r.cap_risk_to_daily_room:
            risk_cash = min(risk_cash, 0.8 * daily_room)
        if risk_cash <= 0:
            return 0.0, 0.0
        # worst-case loss per lot: stop + slippage + both commissions
        loss_per_lot = (stop_dist + k.slippage) * i.contract_size + 2 * k.commission_per_lot_side
        vol = math.floor(risk_cash / loss_per_lot / i.volume_step + 1e-9) * i.volume_step
        vol = min(round(vol, 8), i.volume_max)
        return (vol, risk_cash) if vol >= i.volume_min else (0.0, 0.0)

    # ---- main loop ----------------------------------------------------------
    def run(self, P: Prepared, i0: int = 0, i1: int | None = None,
            stop_on_result: bool = True) -> BacktestResult:
        inst, k, R, rk = self.inst, self.costs, self.rules, self.risk
        n = len(P.o) if i1 is None else i1
        cs, slip = inst.contract_size, k.slippage
        init = R.initial_balance
        daily_lim = R.max_daily_loss_pct * init
        soft_lim = rk.soft_daily_stop_pct * init
        target_bal = init * (1 + R.profit_target_pct)

        bal = init
        peak_eq = init
        day_start_ref = init
        cur_day = -1
        locked = False
        pos = None                       # dict
        pend = None                      # (side, stop_dist, target)
        pend_exit = False
        trading_days: set[int] = set()
        trades: list[dict] = []
        eq_close = np.full(n - i0, np.nan)
        daily_min_dd = 0.0
        max_dd = 0.0
        outcome = "INCOMPLETE"

        def float_pnl(px_exit: float) -> float:
            d = 1 if pos["side"] == 1 else -1
            return d * (px_exit - pos["entry"]) * pos["vol"] * cs

        def close_pos(i: int, px: float, reason: str):
            nonlocal bal, pos
            gross = float_pnl(px)
            comm = 2 * k.commission_per_lot_side * pos["vol"]
            pnl = gross - comm
            bal += pnl
            trades.append({
                "entry_time": P.index[pos["i"]], "exit_time": P.index[i],
                "side": "BUY" if pos["side"] == 1 else "SELL",
                "volume": pos["vol"], "entry": pos["entry"], "exit": px,
                "sl": pos["sl"], "tp": pos["tp"], "pnl": pnl,
                "r": pnl / pos["risk"] if pos["risk"] else np.nan,
                "bars": i - pos["i"], "reason": reason})
            pos = None

        def exit_px(i: int, side: int, base: float, adverse: bool = True) -> float:
            """Price at which an open position is closed at market from `base` (bid)."""
            if side == 1:                       # long closes at bid
                return base - (slip if adverse else 0.0)
            return base + P.sp[i] + (slip if adverse else 0.0)   # short closes at ask

        for i in range(i0, n):
            d = int(P.day_id[i])
            sp = P.sp[i]
            o, h, l, c = P.o[i], P.h[i], P.l[i], P.c[i]

            # --- new prop-firm day ------------------------------------------
            if d != cur_day:
                cur_day = d
                locked = False
                fl = 0.0
                if pos is not None:
                    fl = float_pnl(exit_px(i, pos["side"], o, False)) \
                        - 2 * k.commission_per_lot_side * pos["vol"]
                opening_equity = bal + fl
                day_start_ref = max(bal, opening_equity)
                daily_lim = R.max_daily_loss_pct * day_start_ref

            # --- time limit ---------------------------------------------------
            if R.max_calendar_days is not None and \
                    (P.ts_ns[i] - P.ts_ns[i0]) / 86_400e9 >= R.max_calendar_days:
                if pos is not None:
                    close_pos(i, exit_px(i, pos["side"], o), "TIME_LIMIT")
                outcome = "PASS" if (bal >= target_bal and
                                     len(trading_days) >= R.min_trading_days) else "TIMEOUT"
                n = i
                break

            # --- scheduled exit at open --------------------------------------
            if pos is not None and pend_exit:
                close_pos(i, exit_px(i, pos["side"], o), "SIGNAL_EXIT")
            pend_exit = False

            # --- scheduled entry at open -------------------------------------
            if pend is not None and pos is None and not locked:
                side, sdist, tgt = pend
                entry = (o + sp + slip) if side == 1 else (o - slip)
                sl = entry - side * sdist
                tp = tgt if np.isfinite(tgt) else None
                tp_ok = tp is None or (tp - entry) * side > 0
                room = daily_lim - (day_start_ref - bal)
                vol, risk_cash = self._volume(bal, sdist, room)
                if tp_ok and vol > 0:
                    pos = {"side": side, "vol": vol, "entry": entry, "sl": sl,
                           "tp": tp, "i": i, "risk": risk_cash}
                    trading_days.add(d)
            pend = None

            # --- intrabar SL / TP (SL first when both touch) -----------------
            if pos is not None:
                s, sl, tp = pos["side"], pos["sl"], pos["tp"]
                if s == 1:
                    if l <= sl:
                        close_pos(i, (sl if o > sl else o) - slip, "SL")
                    elif tp is not None and h >= tp:
                        close_pos(i, tp if o < tp else o, "TP")
                else:
                    if h + sp >= sl:
                        ask_o = o + sp
                        close_pos(i, (sl if ask_o < sl else ask_o) + slip, "SL")
                    elif tp is not None and l + sp <= tp:
                        ask_o = o + sp
                        close_pos(i, tp if ask_o > tp else ask_o, "TP")

            # --- worst-case equity this bar vs prop rules --------------------
            if pos is not None:
                worst_px = l if pos["side"] == 1 else h + sp
                eq_worst = bal + float_pnl(worst_px) \
                    - 2 * k.commission_per_lot_side * pos["vol"]
            else:
                eq_worst = bal
            floor = (peak_eq - R.max_total_loss_pct * init) if R.trailing_total \
                else init * (1 - R.max_total_loss_pct)
            daily_dd = day_start_ref - eq_worst
            fail = None
            if eq_worst <= floor:
                fail = "FAIL_MAX_LOSS"
            elif daily_dd >= daily_lim:
                fail = "FAIL_DAILY_LOSS"
            if fail:
                if pos is not None:
                    close_pos(i, l if pos["side"] == 1 else h + sp, fail)
                outcome = fail
                eq_close[i - i0] = bal
                n = i + 1
                break

            # --- bar-close management ---------------------------------------
            if pos is not None:
                flat_now = False
                if rk.max_hold_bars and i - pos["i"] >= rk.max_hold_bars:
                    flat_now = True
                if rk.flatten_hour_utc is not None and P.hour[i] >= rk.flatten_hour_utc:
                    flat_now = True
                if (rk.friday_flatten_hour_utc is not None and P.dow[i] == 4
                        and P.hour[i] >= rk.friday_flatten_hour_utc):
                    flat_now = True
                if flat_now:
                    close_pos(i, exit_px(i, pos["side"], c), "FLATTEN")

            if pos is not None:
                eq_c = bal + float_pnl(exit_px(i, pos["side"], c, False)) \
                    - 2 * k.commission_per_lot_side * pos["vol"]
            else:
                eq_c = bal

            # soft (own) daily stop: flatten and lock until next prop day
            if not locked and day_start_ref - eq_c >= soft_lim:
                if pos is not None:
                    close_pos(i, exit_px(i, pos["side"], c), "SOFT_DAILY_STOP")
                    eq_c = bal
                locked = True

            eq_close[i - i0] = eq_c
            peak_eq = max(peak_eq, eq_c)
            max_dd = max(max_dd, (peak_eq - eq_c) / peak_eq)
            daily_min_dd = max(daily_min_dd, (day_start_ref - eq_c) / init)

            # --- profit target (only when flat) ------------------------------
            if (pos is None and bal >= target_bal
                    and len(trading_days) >= R.min_trading_days):
                outcome = "PASS"
                n = i + 1
                if stop_on_result:
                    break

            # --- queue next-bar actions --------------------------------------
            if pos is not None:
                if (pos["side"] == 1 and P.exit_long[i]) or \
                        (pos["side"] == -1 and P.exit_short[i]):
                    pend_exit = True
            elif (not locked and P.side[i] != 0 and i < n - 1
                  and rk.entry_hours_utc[0] <= P.hour[i] < rk.entry_hours_utc[1]
                  and np.isfinite(P.stop_dist[i]) and P.stop_dist[i] > 0):
                pend = (int(P.side[i]), float(P.stop_dist[i]), float(P.target[i]))

        else:
            n = n  # loop ran to the end

        last = min(n, len(P.o)) - 1
        if pos is not None:
            close_pos(last, exit_px(last, pos["side"], P.c[last]), "END_OF_DATA")

        eq = pd.Series(eq_close[: max(last - i0 + 1, 0)], index=P.index[i0: last + 1])
        eq = eq.ffill().fillna(init)
        tdf = pd.DataFrame(trades)
        return BacktestResult(
            outcome=outcome, start=P.index[i0], end=P.index[last],
            calendar_days=(P.ts_ns[last] - P.ts_ns[i0]) / 86_400e9,
            trading_days=len(trading_days), final_balance=bal,
            return_pct=(bal / init - 1) * 100, max_drawdown_pct=max_dd * 100,
            worst_daily_loss_pct=daily_min_dd * 100, trades=tdf, equity=eq)


# =============================================================================
# 6. METRICS + ROLLING CHALLENGE SIMULATION
# =============================================================================

def trade_metrics(res: BacktestResult) -> dict:
    t = res.trades
    if t.empty:
        return {"trades": 0}
    wins, losses = t[t.pnl > 0].pnl.sum(), -t[t.pnl < 0].pnl.sum()
    daily = res.equity.resample("1D").last().dropna()
    dr = daily.pct_change().dropna()
    sharpe = dr.mean() / dr.std() * math.sqrt(252) if len(dr) > 2 and dr.std() > 0 else float("nan")
    return {
        "trades": len(t),
        "win_rate_%": round((t.pnl > 0).mean() * 100, 1),
        "profit_factor": round(wins / losses, 2) if losses > 0 else float("inf"),
        "avg_R": round(t.r.mean(), 3),
        "expectancy_$": round(t.pnl.mean(), 2),
        "net_return_%": round(res.return_pct, 2),
        "max_dd_%": round(res.max_drawdown_pct, 2),
        "worst_daily_loss_%": round(res.worst_daily_loss_pct, 2),
        "sharpe_daily": round(sharpe, 2),
        "exit_reasons": t.reason.value_counts().to_dict(),
    }


def rolling_challenges(bt: Backtester, P: Prepared, step_days: int = 7,
                       warmup_bars: int = 300) -> pd.DataFrame:
    """Start a fresh challenge every `step_days` and record the outcome.
    Attempts overlap, so they are NOT independent samples: treat the pass
    rate as a rough guide with wide uncertainty."""
    idx = P.index
    day0 = idx[warmup_bars].normalize()
    # For unlimited-time evaluations, keep a 30-day data-observation window
    # after each start so very recent starts are not counted as short attempts.
    # This does not impose a 30-day timeout on the challenge itself.
    observation_days = (bt.rules.max_calendar_days
                        if bt.rules.max_calendar_days is not None else 30)
    last_start = idx[-1] - pd.Timedelta(days=observation_days)
    starts = pd.date_range(day0, last_start, freq=f"{step_days}D")
    rows = []
    for s in starts:
        i0 = int(idx.searchsorted(s))
        if i0 >= len(idx) - 10:
            continue
        r = bt.run(P, i0=i0, stop_on_result=True)
        outcome = r.outcome
        # Guard against impossible reported passes (e.g. 3 required trading
        # days completed in a few hours). A pass must satisfy both the count
        # and a conservative elapsed-time sanity check.
        minimum_elapsed_days = max(0, bt.rules.min_trading_days - 2)
        if outcome == "PASS" and (
                r.trading_days < bt.rules.min_trading_days or
                r.calendar_days + 1e-9 < minimum_elapsed_days):
            outcome = "INVALID_SIMULATION"
        rows.append({"start": r.start, "outcome": outcome,
                     "days": round(r.calendar_days, 1),
                     "trading_days": r.trading_days,
                     "return_%": round(r.return_pct, 2),
                     "max_dd_%": round(r.max_drawdown_pct, 2),
                     "trades": len(r.trades)})
    return pd.DataFrame(rows)


def summarize_challenges(df: pd.DataFrame) -> str:
    if df.empty:
        return "No attempts."
    vc = df.outcome.value_counts()
    n = len(df)
    p = (df.outcome == "PASS").mean()
    se = math.sqrt(p * (1 - p) / n)
    lines = [f"Attempts: {n}", f"Pass rate: {p*100:.1f}%  (naive SE ~ ±{se*100:.1f}pp, overlapping attempts)"]
    lines += [f"  {k:<16}{v:>4}  ({v/n*100:.0f}%)" for k, v in vc.items()]
    passed = df[df.outcome == "PASS"]
    if not passed.empty:
        lines.append(f"Median days to pass: {passed.days.median():.1f}")
    return "\n".join(lines)


def run_all(df: pd.DataFrame, strategy: Strategy, bt: Backtester,
            step_days: int = 7) -> None:
    sig = strategy.generate(df)
    P = prepare(df, sig, bt.inst, bt.costs, bt.rules)

    full_rules = replace(bt.rules, max_calendar_days=None)
    full = Backtester(bt.inst, bt.costs, full_rules, bt.risk).run(P, stop_on_result=False)
    print("\n=== FULL-PERIOD RUN (no time limit, no stop on pass) ===")
    print(f"Outcome: {full.outcome} | {full.start:%Y-%m-%d} -> {full.end:%Y-%m-%d}")
    for k_, v in trade_metrics(full).items():
        print(f"  {k_:<20}{v}")

    print("\n=== ROLLING CHALLENGE SIMULATION ===")
    att = rolling_challenges(bt, P, step_days=step_days)
    print(summarize_challenges(att))

    print("\n=== COST STRESS (spread x2, slippage x2) ===")
    stressed = Backtester(bt.inst,
                          replace(bt.costs, spread_mult=2.0, slippage=bt.costs.slippage * 2),
                          bt.rules, bt.risk)
    P2 = prepare(df, sig, bt.inst, stressed.costs, bt.rules)
    print(summarize_challenges(rolling_challenges(stressed, P2, step_days=step_days)))


# =============================================================================
# 7. CLI
# =============================================================================

def report_out_of_sample(df: pd.DataFrame, strategy: Strategy, bt: Backtester) -> None:
    """Chronological 60/20/20 split; the last segment is untouched holdout."""
    sig = strategy.generate(df)
    P = prepare(df, sig, bt.inst, bt.costs, bt.rules)
    n = len(df)
    cuts = (int(n * 0.60), int(n * 0.80), n)
    segments = [("DEVELOPMENT (first 60%)", 0, cuts[0]),
                ("VALIDATION (next 20%)", cuts[0], cuts[1]),
                ("FINAL HOLDOUT (last 20%)", cuts[1], cuts[2])]
    print("\n=== CHRONOLOGICAL OUT-OF-SAMPLE REPORT ===")
    print("Indicators are calculated causally across history; each segment starts with fresh challenge balance.")
    for label, start, end in segments:
        if end - start < 100:
            print(f"{label}: skipped (insufficient bars)")
            continue
        r = bt.run(P, i0=start, i1=end, stop_on_result=False)
        print(f"\n{label}: {r.start:%Y-%m-%d} -> {r.end:%Y-%m-%d}")
        for key, value in trade_metrics(r).items():
            print(f"  {key:<20}{value}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Prop-firm challenge backtester")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--csv")
    g.add_argument("--mt5", metavar="SYMBOL")
    g.add_argument("--synthetic", action="store_true")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--server-utc-offset", type=float, default=0.0,
                    help="Broker server time minus UTC, in hours (often 2 or 3)")
    ap.add_argument("--preset", choices=PRESETS, default="fundingpips_2step_p1")
    ap.add_argument("--step-days", type=int, default=7)
    ap.add_argument("--strategy", choices=("donchian", "meanreversion"), default="donchian")
    ap.add_argument("--oos", action="store_true", help="also report chronological 60/20/20 development/validation/holdout results")
    ap.add_argument("--commission-per-lot-side", type=float, default=3.5, help="account currency per lot per side; verify broker convention")
    ap.add_argument("--slippage", type=float, default=0.05, help="adverse price units per market fill/stop")
    ap.add_argument("--fallback-spread-points", type=float, default=25.0, help="used only if source data has no spread column")
    a = ap.parse_args()

    if a.csv:
        df = load_csv(a.csv, a.server_utc_offset)
    elif a.mt5:
        df = load_mt5(a.mt5, a.days, a.server_utc_offset)
    else:
        df = synthetic_data()
        print("SYNTHETIC random-walk data: expect NO edge. Plumbing test only.")

    costs = Costs(commission_per_lot_side=a.commission_per_lot_side, slippage=a.slippage, fixed_spread_points=a.fallback_spread_points)
    risk = RiskConfig(risk_per_trade=0.0025, soft_daily_stop_pct=0.025)
    bt = Backtester(costs=costs, rules=PRESETS[a.preset], risk=risk)
    strategy = DonchianTrendBreakout() if a.strategy == "donchian" else EmaZMeanReversion()
    run_all(df, strategy, bt, a.step_days)
    if a.oos:
        report_out_of_sample(df, strategy, bt)


if __name__ == "__main__":
    main()
