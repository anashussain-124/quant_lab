"""FundingPips Research Engine V3 — historical/demo research only.

No order routing or live-trading functions are implemented.

Model conventions
-----------------
* MT5 bar timestamps are treated as UTC (as documented by MetaQuotes).
* OHLC bars are treated as BID bars; spread column is in symbol points.
* Signals are decided at a completed M5 bar close and filled at next bar open.
* Long entry is ask + adverse slippage; short entry is bid - adverse slippage.
* Stops/targets are resolved conservatively when both touch in one M5 candle: stop first.
* FundingPips daily reset is platform midnight UTC+3 == 21:00 UTC.
* Commission default is an explicit research assumption; validate your account's
  precise per-side/round-turn convention using demo deal history before relying on it.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    symbol: str = "XAUUSD"
    point: float = 0.01
    contract_size: float = 100.0
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 50.0


@dataclass(frozen=True)
class Costs:
    commission_per_lot_side: float = 2.50  # assumption: $5 round turn per lot; VERIFY
    slippage: float = 0.05                # adverse price units per fill
    fixed_spread_points: float = 30.0     # used only if no spread column exists
    spread_mult: float = 1.0


@dataclass(frozen=True)
class PropRules:
    initial_balance: float = 5_000.0
    profit_target_pct: float = 0.08
    max_daily_loss_pct: float = 0.05
    max_total_loss_pct: float = 0.10
    min_trading_days: int = 3
    daily_reset_utc_hour: int = 21
    inactivity_days: int = 30
    max_calendar_days: Optional[int] = None  # None = no evaluation time limit


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.0025
    soft_daily_stop_pct: float = 0.01         # internal guard, stricter than firm limit
    soft_total_drawdown_pct: float = 0.04     # internal guard, stricter than firm limit
    cap_risk_to_daily_room: bool = True
    daily_room_fraction: float = 0.80
    max_entries_per_day: int = 1
    entry_start_london: int = 8
    entry_end_london: int = 12
    flatten_at_london_end: bool = True
    max_hold_bars: Optional[int] = 48


RULES: dict[str, PropRules] = {
    "fundingpips_2step_p1": PropRules(profit_target_pct=0.08),
    "fundingpips_2step_p2": PropRules(profit_target_pct=0.05),
    "fundingpips_2step_p1_3pct_daily": PropRules(profit_target_pct=0.08, max_daily_loss_pct=0.03, min_trading_days=0),
    "fundingpips_2step_p2_3pct_daily": PropRules(profit_target_pct=0.05, max_daily_loss_pct=0.03, min_trading_days=0),
}


# ---------------------------------------------------------------------------
# Data loading / validation
# ---------------------------------------------------------------------------
def _normalize_frame(df: pd.DataFrame, timestamps_utc: bool = True) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().strip("<>").lower() for c in out.columns]
    need = ["open", "high", "low", "close"]
    missing = [c for c in need if c not in out.columns]
    if missing:
        raise ValueError(f"Missing required OHLC columns: {missing}")
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is None:
        idx = idx.tz_localize("UTC" if timestamps_utc else "UTC")
    else:
        idx = idx.tz_convert("UTC")
    out.index = idx
    for c in need + (["spread"] if "spread" in out.columns else []):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if out[need].isna().any().any():
        raise ValueError("Missing/non-numeric OHLC values.")
    if ((out["high"] < out["low"]) | (out[need] <= 0).any(axis=1)).any():
        raise ValueError("Invalid OHLC rows: prices must be positive and high >= low.")
    if ((out["open"] > out["high"]) | (out["open"] < out["low"]) |
        (out["close"] > out["high"]) | (out["close"] < out["low"])).any():
        raise ValueError("Invalid OHLC rows: open/close outside high-low range.")
    if "spread" in out.columns and ((out["spread"] < 0) | out["spread"].isna()).any():
        raise ValueError("Spread must be numeric and non-negative.")
    out = out[~out.index.duplicated(keep="first")].sort_index()
    if len(out) < 500:
        raise ValueError(f"Only {len(out)} bars available; at least 500 required for warm-up.")
    if not out.index.is_monotonic_increasing:
        raise ValueError("Timestamps must be sorted.")
    return out


def load_csv(path: str | Path, timestamp_mode: str = "utc", server_utc_offset: float = 3.0) -> pd.DataFrame:
    """Load CSV. timestamp_mode='utc' for UTC timestamps; 'broker' for naive broker time.

    For broker-time CSV, broker_utc_offset is broker time minus UTC for the data
    period. This explicit option avoids silently applying an offset to UTC files.
    """
    raw = pd.read_csv(path, sep=None, engine="python")
    raw.columns = [str(c).strip().strip("<>").lower() for c in raw.columns]
    if "date" in raw.columns and "time" in raw.columns:
        # MT5 terminal export convention: separate DATE and TIME columns.
        ts = pd.to_datetime(raw["date"].astype(str) + " " + raw["time"].astype(str), errors="raise")
    elif "time" in raw.columns:
        ts = pd.to_datetime(raw["time"], errors="raise")
    elif "datetime" in raw.columns:
        ts = pd.to_datetime(raw["datetime"], errors="raise")
    else:
        raise ValueError("CSV needs 'time'/'datetime' or MT5 '<DATE>' and '<TIME>' columns.")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        mode = "utc"
    else:
        mode = timestamp_mode.lower()
    if mode not in {"utc", "broker"}:
        raise ValueError("timestamp_mode must be 'utc' or 'broker'.")
    if mode == "broker":
        ts = ts - pd.to_timedelta(server_utc_offset, unit="h")
    raw.index = pd.DatetimeIndex(ts).tz_localize("UTC")
    cols = [c for c in ["open", "high", "low", "close", "spread"] if c in raw.columns]
    frame = _normalize_frame(raw[cols])
    frame.attrs["source"] = f"csv:{path}"
    return frame


def load_mt5(symbol: str, days: int) -> tuple[pd.DataFrame, Instrument]:
    """Fetch M5 rates from MT5. MT5-returned bar times are UTC; do not shift them."""
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbol {symbol!r} not found in MT5 terminal.")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select {symbol}: {mt5.last_error()}")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=int(days))
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No MT5 rates returned: {mt5.last_error()}. Check chart history / Max bars in chart.")
        raw = pd.DataFrame(rates)
        raw.index = pd.to_datetime(raw["time"], unit="s", utc=True)
        cols = ["open", "high", "low", "close", "spread"]
        frame = _normalize_frame(raw[cols])
        frame.attrs["source"] = f"MT5:{symbol}"
        frame.attrs["requested_days"] = int(days)
        frame.attrs["returned_start"] = str(frame.index[0])
        frame.attrs["returned_end"] = str(frame.index[-1])
        inst = Instrument(symbol=symbol, point=float(info.point), contract_size=float(info.trade_contract_size or 100.0),
                          volume_min=float(info.volume_min or 0.01), volume_step=float(info.volume_step or 0.01),
                          volume_max=float(info.volume_max or 50.0))
        return frame, inst
    finally:
        mt5.shutdown()


def synthetic_data(days: int = 250, seed: int = 7) -> pd.DataFrame:
    """Random-walk M5 BID bars for plumbing tests only; not performance evidence."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days * 288, freq="5min", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    vol = 0.00025 * np.exp(np.clip(np.cumsum(rng.normal(0, 0.015, n)), -1.0, 1.0))
    rets = rng.normal(0, 1, n) * vol
    close = 2000 * np.exp(np.cumsum(rets))
    op = np.r_[close[0], close[:-1]]
    spread_points = rng.integers(18, 36, n)
    hl = np.abs(rng.normal(0, 1, n)) * vol * close * 0.7
    high, low = np.maximum(op, close) + hl, np.minimum(op, close) - hl
    return pd.DataFrame({"open": op, "high": high, "low": low, "close": close,
                         "spread": spread_points}, index=idx)


# ---------------------------------------------------------------------------
# Indicators and strategy interface
# ---------------------------------------------------------------------------
def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def _completed_htf_ema(df: pd.DataFrame, span: int, timeframe: str = "15min") -> pd.Series:
    # MT5 df index marks bar OPEN; close of M5 bar is available at open+5min.
    close_idx = df.index + pd.Timedelta(minutes=5)
    c = pd.Series(df["close"].to_numpy(float), index=close_idx)
    htf_close = c.resample(timeframe, label="right", closed="right").last()
    ema = htf_close.ewm(span=span, adjust=False, min_periods=span).mean()
    return pd.Series(ema.reindex(close_idx, method="ffill").to_numpy(), index=df.index)


def _base_signals(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"side": np.zeros(len(df), dtype=np.int8),
                         "stop_dist": np.full(len(df), np.nan),
                         "target": np.full(len(df), np.nan),
                         "exit_long": np.zeros(len(df), dtype=bool),
                         "exit_short": np.zeros(len(df), dtype=bool)}, index=df.index)


class Strategy:
    name = "base"
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError


@dataclass(frozen=True)
class AsianBreakoutParams:
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    reward_risk: float = 2.0
    fast_ema: int = 50
    slow_ema: int = 200
    buffer_atr: float = 0.05
    min_range_atr: float = 0.50
    max_range_atr: float = 12.0
    session_start: int = 8
    session_end: int = 12


class AsianRangeBreakout(Strategy):
    """Break and close beyond completed 00:00-08:00 Europe/London Asian range."""
    name = "asian_breakout"
    def __init__(self, p: AsianBreakoutParams = AsianBreakoutParams()): self.p = p
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p, out = self.p, _base_signals(df)
        close_idx = df.index + pd.Timedelta(minutes=5)
        local = close_idx.tz_convert("Europe/London")
        # Range membership uses bar OPEN time: the 07:55-08:00 candle belongs
        # to the Asian range and is fully known when London begins at 08:00.
        open_local = df.index.tz_convert("Europe/London")
        day_key = pd.Index(open_local.strftime("%Y-%m-%d"), name="local_day")
        open_min = open_local.hour * 60 + open_local.minute
        asian = (open_min >= 0) & (open_min < 8 * 60)
        range_high = df["high"].where(asian).groupby(day_key).transform("max")
        range_low = df["low"].where(asian).groupby(day_key).transform("min")
        local_min = local.hour * 60 + local.minute
        atr = _atr(df, p.atr_period)
        fast, slow = _completed_htf_ema(df, p.fast_ema), _completed_htf_ema(df, p.slow_ema)
        buf = atr * p.buffer_atr
        width = range_high - range_low
        in_session = (local_min >= p.session_start * 60) & (local_min < p.session_end * 60)
        width_ok = (width >= atr * p.min_range_atr) & (width <= atr * p.max_range_atr)
        up_level, dn_level = range_high + buf, range_low - buf
        prev_c, prev_up, prev_dn = df.close.shift(1), up_level.shift(1), dn_level.shift(1)
        long_sig = (df.close > up_level) & (prev_c <= prev_up) & (fast > slow) & in_session & width_ok
        short_sig = (df.close < dn_level) & (prev_c >= prev_dn) & (fast < slow) & in_session & width_ok
        stop = atr * p.stop_atr_mult
        side = np.where(long_sig, 1, np.where(short_sig, -1, 0)).astype(np.int8)
        valid = atr.notna() & fast.notna() & slow.notna() & (stop > 0) & range_high.notna() & range_low.notna()
        side[~valid.to_numpy()] = 0
        out["side"] = side
        out["stop_dist"] = stop.to_numpy(float)
        out["target"] = np.where(side != 0, df.close.to_numpy(float) + side * stop.to_numpy(float) * p.reward_risk, np.nan)
        out["exit_long"] = (df.close < fast).to_numpy(bool)
        out["exit_short"] = (df.close > fast).to_numpy(bool)
        return out


@dataclass(frozen=True)
class FailedBreakoutParams:
    atr_period: int = 14
    fast_ema: int = 50
    slow_ema: int = 200
    buffer_atr: float = 0.05
    min_target_r: float = 1.20
    session_start: int = 8
    session_end: int = 12


class FailedBreakoutReversal(Strategy):
    """Reversal after a same-bar sweep of Asian range boundary and close back inside."""
    name = "failed_breakout"
    def __init__(self, p: FailedBreakoutParams = FailedBreakoutParams()): self.p = p
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p, out = self.p, _base_signals(df)
        close_idx = df.index + pd.Timedelta(minutes=5)
        local = close_idx.tz_convert("Europe/London")
        open_local = df.index.tz_convert("Europe/London")
        day_key = pd.Index(open_local.strftime("%Y-%m-%d"), name="local_day")
        open_mins = open_local.hour * 60 + open_local.minute
        asian = (open_mins >= 0) & (open_mins < 8 * 60)
        rh = df.high.where(asian).groupby(day_key).transform("max")
        rl = df.low.where(asian).groupby(day_key).transform("min")
        mins = local.hour * 60 + local.minute
        atr = _atr(df, p.atr_period)
        fast, slow = _completed_htf_ema(df, p.fast_ema), _completed_htf_ema(df, p.slow_ema)
        buf = atr * p.buffer_atr
        in_session = (mins >= p.session_start * 60) & (mins < p.session_end * 60)
        upper = (df.high > rh + buf) & (df.close < rh)
        lower = (df.low < rl - buf) & (df.close > rl)
        # Skip a candle that sweeps both sides; OHLC cannot resolve path.
        both = upper & lower
        short_side = upper & ~both & in_session & (fast < slow)
        long_side = lower & ~both & in_session & (fast > slow)
        # Stops beyond the signal candle's sweep extreme; position stop distance is
        # translated from signal close and applied from next-open actual fill.
        short_dist = (df.high + buf - df.close).clip(lower=atr * 0.25)
        long_dist = (df.close - (df.low - buf)).clip(lower=atr * 0.25)
        mid = (rh + rl) / 2.0
        long_rr = (mid - df.close) / long_dist
        short_rr = (df.close - mid) / short_dist
        long_side &= (long_rr >= p.min_target_r).to_numpy() if hasattr(long_rr, "to_numpy") else (long_rr >= p.min_target_r)
        short_side &= (short_rr >= p.min_target_r).to_numpy() if hasattr(short_rr, "to_numpy") else (short_rr >= p.min_target_r)
        side = np.where(long_side, 1, np.where(short_side, -1, 0)).astype(np.int8)
        valid = atr.notna() & fast.notna() & slow.notna() & rh.notna() & rl.notna()
        side[~valid.to_numpy()] = 0
        dist = np.where(side == 1, long_dist.to_numpy(float), short_dist.to_numpy(float))
        out["side"] = side
        out["stop_dist"] = dist
        out["target"] = np.where(side != 0, mid.to_numpy(float), np.nan)
        # Exit if price closes across the Asian midpoint against our position.
        out["exit_long"] = (df.close >= mid).to_numpy(bool)
        out["exit_short"] = (df.close <= mid).to_numpy(bool)
        return out


@dataclass(frozen=True)
class TrendPullbackParams:
    atr_period: int = 14
    fast_ema: int = 50
    slow_ema: int = 200
    pullback_ema: int = 20
    stop_atr_mult: float = 1.50
    reward_risk: float = 2.0
    swing_lookback: int = 5
    session_start: int = 8
    session_end: int = 12


class TrendPullbackContinuation(Strategy):
    """Completed-M15 trend + M5 EMA pullback/reclaim during London session."""
    name = "trend_pullback"
    def __init__(self, p: TrendPullbackParams = TrendPullbackParams()): self.p = p
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p, out = self.p, _base_signals(df)
        close_idx = df.index + pd.Timedelta(minutes=5)
        local = close_idx.tz_convert("Europe/London")
        mins = local.hour * 60 + local.minute
        in_session = (mins >= p.session_start * 60) & (mins < p.session_end * 60)
        atr = _atr(df, p.atr_period)
        ema = df.close.ewm(span=p.pullback_ema, adjust=False, min_periods=p.pullback_ema).mean()
        fast, slow = _completed_htf_ema(df, p.fast_ema), _completed_htf_ema(df, p.slow_ema)
        uptrend, downtrend = fast > slow, fast < slow
        bullish_reclaim = (df.low <= ema) & (df.close > ema) & (df.close > df.open)
        bearish_reject = (df.high >= ema) & (df.close < ema) & (df.close < df.open)
        long_side = bullish_reclaim & uptrend & in_session
        short_side = bearish_reject & downtrend & in_session
        swing_low = df.low.shift(1).rolling(p.swing_lookback).min()
        swing_high = df.high.shift(1).rolling(p.swing_lookback).max()
        long_dist = np.maximum((atr * p.stop_atr_mult).to_numpy(float), (df.close - swing_low).to_numpy(float))
        short_dist = np.maximum((atr * p.stop_atr_mult).to_numpy(float), (swing_high - df.close).to_numpy(float))
        side = np.where(long_side, 1, np.where(short_side, -1, 0)).astype(np.int8)
        valid = atr.notna() & fast.notna() & slow.notna() & ema.notna() & swing_low.notna() & swing_high.notna()
        side[~valid.to_numpy()] = 0
        dist = np.where(side == 1, long_dist, short_dist)
        dist[~np.isfinite(dist) | (dist <= 0)] = np.nan
        side[~np.isfinite(dist)] = 0
        out["side"] = side
        out["stop_dist"] = dist
        out["target"] = np.where(side != 0, df.close.to_numpy(float) + side * dist * p.reward_risk, np.nan)
        out["exit_long"] = (df.close < ema).to_numpy(bool)
        out["exit_short"] = (df.close > ema).to_numpy(bool)
        return out


@dataclass(frozen=True)
class DonchianBaselineParams:
    breakout_bars: int = 20
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    reward_risk: float = 2.0
    fast_ema: int = 50
    slow_ema: int = 200
    session_start: int = 8
    session_end: int = 12


class DonchianBaseline(Strategy):
    """The prior V2 idea, retained unchanged in spirit as a baseline candidate."""
    name = "donchian_baseline"
    def __init__(self, p: DonchianBaselineParams = DonchianBaselineParams()): self.p = p
    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p, out = self.p, _base_signals(df)
        close_idx = df.index + pd.Timedelta(minutes=5)
        local = close_idx.tz_convert("Europe/London")
        mins = local.hour * 60 + local.minute
        in_session = (mins >= p.session_start * 60) & (mins < p.session_end * 60)
        prior_hi = df.high.shift(1).rolling(p.breakout_bars).max()
        prior_lo = df.low.shift(1).rolling(p.breakout_bars).min()
        atr = _atr(df, p.atr_period)
        fast, slow = _completed_htf_ema(df, p.fast_ema), _completed_htf_ema(df, p.slow_ema)
        long_side = (df.close > prior_hi) & (fast > slow) & in_session
        short_side = (df.close < prior_lo) & (fast < slow) & in_session
        side = np.where(long_side, 1, np.where(short_side, -1, 0)).astype(np.int8)
        stop = atr * p.stop_atr_mult
        valid = atr.notna() & fast.notna() & slow.notna() & stop.notna()
        side[~valid.to_numpy()] = 0
        out["side"] = side
        out["stop_dist"] = stop.to_numpy(float)
        out["target"] = np.where(side != 0, df.close.to_numpy(float) + side * stop.to_numpy(float) * p.reward_risk, np.nan)
        out["exit_long"] = (side == -1)
        out["exit_short"] = (side == 1)
        return out


STRATEGIES = {
    "asian_breakout": AsianRangeBreakout,
    "failed_breakout": FailedBreakoutReversal,
    "trend_pullback": TrendPullbackContinuation,
    "donchian_baseline": DonchianBaseline,
}


# ---------------------------------------------------------------------------
# Prepared data and backtest engine
# ---------------------------------------------------------------------------
@dataclass
class Prepared:
    index: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread: np.ndarray
    day_id: np.ndarray
    dow: np.ndarray
    hour_london: np.ndarray
    minute_london: np.ndarray
    ts_ns: np.ndarray
    side: np.ndarray
    stop_dist: np.ndarray
    target: np.ndarray
    exit_long: np.ndarray
    exit_short: np.ndarray


def prepare(df: pd.DataFrame, sig: pd.DataFrame, inst: Instrument, costs: Costs, rules: PropRules) -> Prepared:
    if not df.index.equals(sig.index):
        raise ValueError("Signals must align exactly with market data.")
    required = {"side", "stop_dist", "target", "exit_long", "exit_short"}
    if not required.issubset(sig.columns):
        raise ValueError(f"Signals missing columns: {sorted(required - set(sig.columns))}")
    if "spread" in df.columns:
        spread = df.spread.to_numpy(float) * inst.point
    else:
        spread = np.full(len(df), costs.fixed_spread_points * inst.point)
    spread *= costs.spread_mult
    local = (df.index + pd.Timedelta(minutes=5)).tz_convert("Europe/London")
    # Shift by daily reset UTC hour. With UTC+3 midnight reset, hour=21 means
    # subtract 21 hours before assigning a calendar-day bucket.
    shifted = df.index - pd.Timedelta(hours=rules.daily_reset_utc_hour)
    day_id = (shifted.year.to_numpy() * 10_000 + shifted.month.to_numpy() * 100 + shifted.day.to_numpy())
    return Prepared(df.index, df.open.to_numpy(float), df.high.to_numpy(float),
                    df.low.to_numpy(float), df.close.to_numpy(float), spread,
                    day_id.astype(np.int64), df.index.dayofweek.to_numpy(),
                    local.hour.to_numpy(), local.minute.to_numpy(), df.index.asi8,
                    sig.side.to_numpy(np.int8), sig.stop_dist.to_numpy(float), sig.target.to_numpy(float),
                    sig.exit_long.to_numpy(bool), sig.exit_short.to_numpy(bool))


@dataclass
class BacktestResult:
    outcome: str
    start: pd.Timestamp
    end: pd.Timestamp
    start_index: int
    end_index: int
    calendar_days: float
    trading_days: int
    initial_balance: float
    final_balance: float
    final_equity: float
    return_pct: float
    max_drawdown_pct: float
    worst_daily_loss_pct: float
    trades: pd.DataFrame
    equity: pd.Series
    hard_breach: bool


class Backtester:
    def __init__(self, inst: Instrument = Instrument(), costs: Costs = Costs(),
                 rules: PropRules = PropRules(), risk: RiskConfig = RiskConfig()):
        self.inst, self.costs, self.rules, self.risk = inst, costs, rules, risk

    def _volume_for_cash(self, risk_cash: float, stop_dist: float) -> tuple[float, float]:
        i, c = self.inst, self.costs
        risk_cash = max(0.0, float(risk_cash))
        loss_per_lot = (stop_dist + c.slippage) * i.contract_size + 2 * c.commission_per_lot_side
        if not math.isfinite(loss_per_lot) or loss_per_lot <= 0 or risk_cash <= 0:
            return 0.0, 0.0
        # Floor to the broker volume step. Never round up above risk allowance.
        vol = math.floor((risk_cash / loss_per_lot) / i.volume_step + 1e-10) * i.volume_step
        vol = min(round(vol, 8), i.volume_max)
        actual_budget = min(risk_cash, vol * loss_per_lot)
        return (vol, actual_budget) if vol >= i.volume_min else (0.0, 0.0)

    def run(self, p: Prepared, start_index: int = 0, end_index: Optional[int] = None,
            enforce_prop_rules: bool = True, stop_on_result: bool = True) -> BacktestResult:
        n_all = len(p.open)
        end_exclusive = n_all if end_index is None else min(int(end_index), n_all)
        if not 0 <= start_index < end_exclusive:
            raise ValueError("Invalid backtest index window.")
        inst, costs, rules, risk = self.inst, self.costs, self.rules, self.risk
        init = rules.initial_balance
        target_bal = init * (1 + rules.profit_target_pct)
        hard_daily_limit = rules.max_daily_loss_pct * init
        hard_floor = init * (1 - rules.max_total_loss_pct)
        soft_daily_limit = risk.soft_daily_stop_pct * init
        soft_total_floor = init * (1 - risk.soft_total_drawdown_pct)
        bal = init
        peak_equity = init
        daily_reference = init
        daily_loss_min = 0.0
        max_dd = 0.0
        cur_day = None
        day_locked = False
        daily_entries = 0
        trading_days: set[int] = set()
        last_closed_trade_day: Optional[int] = None
        last_closed_trade_ts: pd.Timestamp = p.index[start_index]  # inactivity clock starts at account start
        total_locked = False
        pos: Optional[dict] = None
        pending_entry: Optional[tuple[int, float, float]] = None
        pending_exit = False
        trades: list[dict] = []
        equity_values: list[float] = []
        equity_times: list[pd.Timestamp] = []
        outcome = "INCOMPLETE"
        hard_breach = False
        processed_end = start_index

        def mark_equity(mark_bid: float, mark_spread: float, bar_i: int) -> float:
            if pos is None:
                return bal
            quote = mark_bid if pos["side"] == 1 else mark_bid + mark_spread
            gross = pos["side"] * (quote - pos["entry"]) * pos["vol"] * inst.contract_size
            exit_commission = costs.commission_per_lot_side * pos["vol"]
            return bal + gross - exit_commission

        def close_position(i: int, px: float, reason: str):
            nonlocal bal, pos, last_closed_trade_day, last_closed_trade_ts
            if pos is None:
                return
            gross = pos["side"] * (px - pos["entry"]) * pos["vol"] * inst.contract_size
            exit_comm = costs.commission_per_lot_side * pos["vol"]
            net = gross - pos["entry_commission"] - exit_comm
            bal += gross - exit_comm  # entry commission was deducted on entry
            trades.append({
                "entry_time": p.index[pos["i"]], "exit_time": p.index[i],
                "side": "BUY" if pos["side"] == 1 else "SELL", "volume": pos["vol"],
                "entry": pos["entry"], "exit": px, "sl": pos["sl"], "tp": pos["tp"],
                "gross_pnl": gross, "commission": pos["entry_commission"] + exit_comm,
                "pnl": net, "risk_budget": pos["risk_budget"],
                "r": net / pos["risk_budget"] if pos["risk_budget"] else np.nan,
                "bars": i - pos["i"], "reason": reason,
                "entry_day_id": pos["day_id"], "exit_day_id": int(p.day_id[i]),
            })
            # Conservative interpretation: only a day with a completed trade
            # counts toward minimum trading days (entry-only activity is insufficient).
            trading_days.add(int(p.day_id[i]))
            last_closed_trade_day = int(p.day_id[i])
            last_closed_trade_ts = p.index[i]
            pos = None

        def close_px_for_market(i: int, bid: float, side: int, adverse: bool = True) -> float:
            # Exits are executed on Bid for longs, Ask for shorts.
            return bid - costs.slippage if side == 1 else bid + p.spread[i] + costs.slippage

        def breach_status(eq_worst: float, daily_ref: float) -> Optional[str]:
            if eq_worst <= hard_floor:
                return "FAIL_MAX_LOSS"
            if daily_ref - eq_worst >= rules.max_daily_loss_pct * daily_ref:
                return "FAIL_DAILY_LOSS"
            return None

        for i in range(start_index, end_exclusive):
            processed_end = i
            bid_o, bid_h, bid_l, bid_c = p.open[i], p.high[i], p.low[i], p.close[i]
            spr = p.spread[i]
            day = int(p.day_id[i])

            # New FundingPips day: higher of opening balance / opening equity is baseline.
            if day != cur_day:
                cur_day, daily_entries, day_locked = day, 0, False
                open_equity = mark_equity(bid_o, spr, i)
                daily_reference = max(bal, open_equity)

            # Evaluation timeout is optional; default is unlimited time.
            if rules.max_calendar_days is not None and (p.ts_ns[i] - p.ts_ns[start_index]) / 86_400e9 >= rules.max_calendar_days:
                if pos is not None:
                    close_position(i, close_px_for_market(i, bid_o, pos["side"]), "TIME_LIMIT")
                outcome = "TIMEOUT"
                break

            # Inactivity is a firm requirement: one completed trade within any 30-day window.
            if enforce_prop_rules and rules.inactivity_days and (p.index[i] - last_closed_trade_ts).total_seconds() >= rules.inactivity_days * 86400:
                if pos is not None:
                    close_position(i, close_px_for_market(i, bid_o, pos["side"]), "INACTIVITY_BREACH")
                outcome, hard_breach = "FAIL_INACTIVITY", True
                break

            # If the open gaps through a protective stop or target, handle it before discretionary queued exits.
            if pos is not None:
                side = pos["side"]
                stop_at_open = (bid_o <= pos["sl"]) if side == 1 else (bid_o + spr >= pos["sl"])
                target_at_open = (pos["tp"] is not None and ((bid_o >= pos["tp"]) if side == 1 else (bid_o + spr <= pos["tp"])))
                if stop_at_open:
                    px = min(bid_o, pos["sl"]) - costs.slippage if side == 1 else max(bid_o + spr, pos["sl"]) + costs.slippage
                    close_position(i, px, "SL_GAP")
                elif target_at_open:
                    px = max(bid_o, pos["tp"]) if side == 1 else min(bid_o + spr, pos["tp"])
                    close_position(i, px, "TP_GAP")

            # Scheduled strategy exit at next bar open; no duplicate close path.
            if pos is not None and pending_exit:
                close_position(i, close_px_for_market(i, bid_o, pos["side"]), "SIGNAL_EXIT")
            pending_exit = False

            # Market entry queued at prior completed bar close.
            if pos is None and pending_entry is not None and not day_locked and not total_locked:
                side, stop_dist, tgt = pending_entry
                # The bar index is its OPEN time; entry eligibility is checked at that open.
                london_hour_open = int(p.index[i].tz_convert("Europe/London").hour)
                in_time = risk.entry_start_london <= london_hour_open < risk.entry_end_london
                if risk.max_entries_per_day > 0 and daily_entries < risk.max_entries_per_day and in_time:
                    entry = bid_o + spr + costs.slippage if side == 1 else bid_o - costs.slippage
                    sl = entry - side * stop_dist
                    tp = float(tgt) if np.isfinite(tgt) else None
                    tp_ok = tp is None or (tp - entry) * side > 0
                    room = hard_daily_limit - max(0.0, daily_reference - bal)
                    risk_cash_cap = bal * risk.risk_per_trade
                    if risk.cap_risk_to_daily_room:
                        risk_cash_cap = min(risk_cash_cap, max(0.0, risk.daily_room_fraction * room))
                    vol, budget = self._volume_for_cash(risk_cash_cap, stop_dist)
                    if tp_ok and vol > 0:
                        entry_comm = costs.commission_per_lot_side * vol
                        bal -= entry_comm
                        pos = {"side": side, "vol": vol, "entry": entry, "sl": sl,
                               "tp": tp, "i": i, "risk_budget": min(budget, risk_cash_cap),
                               "entry_commission": entry_comm, "day_id": day}
                        daily_entries += 1
                pending_entry = None
            else:
                pending_entry = None

            # Intrabar orders. Bid OHLC + observed spread approximates Ask path.
            # Track the adverse excursion BEFORE accepting a target. If it would
            # breach an account rule, conservatively treat the breach as occurring
            # before a same-bar target could be reached.
            bar_worst_equity = bal
            if pos is not None:
                side, sl, tp = pos["side"], pos["sl"], pos["tp"]
                if side == 1:
                    stop_hit = bid_l <= sl
                    tp_hit = tp is not None and bid_h >= tp
                    if stop_hit:  # stop first whenever candle path is ambiguous
                        px = (bid_o if bid_o <= sl else sl) - costs.slippage
                        close_position(i, px, "SL")
                        bar_worst_equity = bal
                    else:
                        bar_worst_equity = mark_equity(bid_l, spr, i)
                        if enforce_prop_rules:
                            bar_breach = breach_status(bar_worst_equity, daily_reference)
                            soft_day = daily_reference - bar_worst_equity >= soft_daily_limit
                            soft_total = bar_worst_equity <= soft_total_floor
                            if bar_breach or soft_day or soft_total:
                                adverse_px = bid_l - costs.slippage
                                close_position(i, adverse_px, bar_breach or "SOFT_RISK_STOP")
                                if bar_breach:
                                    outcome, hard_breach = bar_breach, True
                                    equity_values.append(bal); equity_times.append(p.index[i])
                                    break
                                day_locked = True
                                if soft_total:
                                    total_locked = True
                                    outcome = "STOP_SOFT_TOTAL_GUARD"
                                    equity_values.append(bal); equity_times.append(p.index[i])
                                    break
                        if pos is not None and tp_hit:
                            close_position(i, max(bid_o, tp), "TP")
                else:
                    ask_h, ask_l, ask_o = bid_h + spr, bid_l + spr, bid_o + spr
                    stop_hit = ask_h >= sl
                    tp_hit = tp is not None and ask_l <= tp
                    if stop_hit:
                        px = (ask_o if ask_o >= sl else sl) + costs.slippage
                        close_position(i, px, "SL")
                        bar_worst_equity = bal
                    else:
                        bar_worst_equity = mark_equity(bid_h, spr, i)
                        if enforce_prop_rules:
                            bar_breach = breach_status(bar_worst_equity, daily_reference)
                            soft_day = daily_reference - bar_worst_equity >= soft_daily_limit
                            soft_total = bar_worst_equity <= soft_total_floor
                            if bar_breach or soft_day or soft_total:
                                adverse_px = bid_h + spr + costs.slippage
                                close_position(i, adverse_px, bar_breach or "SOFT_RISK_STOP")
                                if bar_breach:
                                    outcome, hard_breach = bar_breach, True
                                    equity_values.append(bal); equity_times.append(p.index[i])
                                    break
                                day_locked = True
                                if soft_total:
                                    total_locked = True
                                    outcome = "STOP_SOFT_TOTAL_GUARD"
                                    equity_values.append(bal); equity_times.append(p.index[i])
                                    break
                        if pos is not None and tp_hit:
                            close_position(i, min(ask_o, tp), "TP")

            # Intrabar risk check on open positions not already stopped/targeted.
            if pos is not None:
                adverse_bid = bid_l if pos["side"] == 1 else bid_h
                adverse_spread = spr
                eq_worst = min(bar_worst_equity, mark_equity(adverse_bid, adverse_spread, i))
            else:
                eq_worst = min(bar_worst_equity, bal)
            eq_close = mark_equity(bid_c, spr, i)
            eq_open = mark_equity(bid_o, spr, i)
            peak_equity = max(peak_equity, eq_open)
            max_dd = max(max_dd, max(0.0, (peak_equity - min(eq_worst, eq_close)) / max(peak_equity, 1e-9)))
            daily_loss_min = max(daily_loss_min, max(0.0, daily_reference - min(eq_worst, eq_close)) / init)

            # Hard account rules inspect equity, including floating loss.
            if enforce_prop_rules:
                breach = breach_status(min(eq_worst, eq_close), daily_reference)
                # Internal soft guards are intentionally stricter than firm rules.
                soft_daily_hit = daily_reference - min(eq_worst, eq_close) >= soft_daily_limit
                soft_total_hit = min(eq_worst, eq_close) <= soft_total_floor
                if breach:
                    if pos is not None:
                        adverse_bid = bid_l if pos["side"] == 1 else bid_h
                        adverse_px = adverse_bid - costs.slippage if pos["side"] == 1 else adverse_bid + spr + costs.slippage
                        close_position(i, adverse_px, breach)
                    outcome, hard_breach = breach, True
                    equity_values.append(bal); equity_times.append(p.index[i])
                    break
                if soft_daily_hit or soft_total_hit:
                    if pos is not None:
                        side = pos["side"]
                        adverse_bid = bid_l if side == 1 else bid_h
                        px = adverse_bid - costs.slippage if side == 1 else adverse_bid + spr + costs.slippage
                        close_position(i, px, "SOFT_RISK_STOP")
                        eq_close = bal
                    day_locked = True
                    if soft_total_hit:
                        total_locked = True
                        outcome = "STOP_SOFT_TOTAL_GUARD"
                        equity_values.append(bal); equity_times.append(p.index[i])
                        break

            # Scheduled end-of-session / max-hold exits execute at bar close, only
            # after the intrabar worst-equity / rule-breach check above.
            if pos is not None:
                held_bars = i - pos["i"]
                hit_hold_cap = risk.max_hold_bars is not None and held_bars >= risk.max_hold_bars
                hit_session_end = risk.flatten_at_london_end and int(p.hour_london[i]) >= risk.entry_end_london
                if hit_hold_cap or hit_session_end:
                    side = pos["side"]
                    px = bid_c - costs.slippage if side == 1 else bid_c + spr + costs.slippage
                    close_position(i, px, "MAX_HOLD" if hit_hold_cap else "SESSION_FLAT")
                    eq_close = bal

            # Target only counts on realized balance while flat; this is conservative.
            if enforce_prop_rules and pos is None and bal >= target_bal and len(trading_days) >= rules.min_trading_days:
                outcome = "PASS"
                equity_values.append(bal); equity_times.append(p.index[i])
                if stop_on_result:
                    break

            # Detect strategy exit at this completed bar; order executes next bar open.
            if pos is not None:
                pending_exit = bool((pos["side"] == 1 and p.exit_long[i]) or
                                    (pos["side"] == -1 and p.exit_short[i]))
            elif not day_locked and not total_locked and i < end_exclusive - 1 and p.side[i] != 0 and np.isfinite(p.stop_dist[i]) and p.stop_dist[i] > 0:
                signal_close_idx = p.index[i] + pd.Timedelta(minutes=5)
                local_signal = signal_close_idx.tz_convert("Europe/London")
                if risk.entry_start_london <= local_signal.hour < risk.entry_end_london:
                    pending_entry = (int(p.side[i]), float(p.stop_dist[i]), float(p.target[i]))
            equity_values.append(eq_close); equity_times.append(p.index[i])

        # If the data simply ends, close any remaining trade only for reporting;
        # this does not convert an unfinished challenge into a PASS.
        if outcome == "INCOMPLETE" and pos is not None:
            i = min(processed_end, n_all - 1)
            close_position(i, close_px_for_market(i, p.close[i], pos["side"]), "END_OF_DATA")
            if equity_times and equity_times[-1] == p.index[i]:
                equity_values[-1] = bal
        last_i = min(processed_end, n_all - 1)
        end_ts = p.index[last_i]
        if equity_times:
            eq = pd.Series(equity_values[:len(equity_times)], index=pd.DatetimeIndex(equity_times)).groupby(level=0).last()
        else:
            eq = pd.Series([init], index=pd.DatetimeIndex([p.index[start_index]]))
        final_equity = float(eq.iloc[-1]) if not eq.empty else float(bal)
        calendar_days = max(0.0, (p.ts_ns[last_i] - p.ts_ns[start_index]) / 86_400e9)
        return BacktestResult(outcome, p.index[start_index], end_ts, start_index, last_i,
                              calendar_days, len(trading_days), init, bal, final_equity,
                              (bal / init - 1) * 100, max_dd * 100, daily_loss_min * 100,
                              pd.DataFrame(trades), eq, hard_breach)


# ---------------------------------------------------------------------------
# Metrics and sequential challenge simulation
# ---------------------------------------------------------------------------
def trade_metrics(res: BacktestResult) -> dict:
    t = res.trades
    if t.empty:
        return {"trades": 0, "net_return_%": round(res.return_pct, 2),
                "max_drawdown_%": round(res.max_drawdown_pct, 2),
                "worst_daily_loss_%": round(res.worst_daily_loss_pct, 2)}
    wins = float(t.loc[t.pnl > 0, "pnl"].sum())
    losses = float(-t.loc[t.pnl < 0, "pnl"].sum())
    daily = res.equity.resample("1D").last().dropna()
    daily_ret = daily.pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * math.sqrt(252)) if len(daily_ret) > 2 and daily_ret.std() > 0 else np.nan
    return {
        "trades": int(len(t)), "win_rate_%": round(float((t.pnl > 0).mean() * 100), 1),
        "profit_factor": round(wins / losses, 3) if losses else (float("inf") if wins else np.nan),
        "avg_R": round(float(t.r.mean()), 3), "expectancy_$": round(float(t.pnl.mean()), 2),
        "net_return_%": round(float(res.return_pct), 2), "max_drawdown_%": round(float(res.max_drawdown_pct), 2),
        "worst_daily_loss_%": round(float(res.worst_daily_loss_pct), 2),
        "sharpe_daily": round(float(sharpe), 3) if np.isfinite(sharpe) else np.nan,
        "exit_reasons": t.reason.value_counts().to_dict(),
    }


def sequential_challenge(p: Prepared, inst: Instrument, costs: Costs, risk: RiskConfig,
                         start_index: int = 0, end_index: Optional[int] = None,
                         rules_p1: PropRules = RULES["fundingpips_2step_p1"],
                         rules_p2: PropRules = RULES["fundingpips_2step_p2"]) -> dict:
    """Run Phase 1 then Phase 2 on subsequent bars; each phase resets balance to $5k.

    Historical end without a decisive result is INCOMPLETE, not failure or pass.
    """
    p1bt = Backtester(inst, costs, rules_p1, risk)
    p1 = p1bt.run(p, start_index, end_index, enforce_prop_rules=True, stop_on_result=True)
    if p1.outcome != "PASS":
        return {"outcome": p1.outcome, "p1": p1, "p2": None,
                "start": p.index[start_index], "end": p1.end,
                "calendar_days": p1.calendar_days, "trades": len(p1.trades)}
    p2_start = p1.end_index + 1
    if end_index is not None and p2_start >= end_index or p2_start >= len(p.index) - 1:
        return {"outcome": "INCOMPLETE_AFTER_P1", "p1": p1, "p2": None,
                "start": p.index[start_index], "end": p1.end,
                "calendar_days": p1.calendar_days, "trades": len(p1.trades)}
    p2bt = Backtester(inst, costs, rules_p2, risk)
    p2 = p2bt.run(p, p2_start, end_index, enforce_prop_rules=True, stop_on_result=True)
    outcome = "PASS_BOTH" if p2.outcome == "PASS" else f"P2_{p2.outcome}"
    return {"outcome": outcome, "p1": p1, "p2": p2, "start": p.index[start_index],
            "end": p2.end, "calendar_days": (p.ts_ns[p2.end_index] - p.ts_ns[start_index]) / 86_400e9,
            "trades": len(p1.trades) + len(p2.trades)}


def rolling_sequential_challenges(p: Prepared, inst: Instrument, costs: Costs, risk: RiskConfig,
                                  step_days: int = 14, minimum_future_days: int = 180,
                                  max_attempts: int = 200,
                                  rules_p1: PropRules = RULES["fundingpips_2step_p1"],
                                  rules_p2: PropRules = RULES["fundingpips_2step_p2"]) -> pd.DataFrame:
    idx = p.index
    first = idx[0] + pd.Timedelta(days=10)
    last = idx[-1] - pd.Timedelta(days=minimum_future_days)
    if last <= first:
        return pd.DataFrame()
    starts = pd.date_range(first.normalize(), last.normalize(), freq=f"{step_days}D", tz="UTC")
    if len(starts) > max_attempts:
        starts = starts[-max_attempts:]
    rows = []
    for s in starts:
        i0 = int(idx.searchsorted(s))
        if i0 >= len(idx) - 10:
            continue
        result = sequential_challenge(p, inst, costs, risk, i0, rules_p1=rules_p1, rules_p2=rules_p2)
        rows.append({"start": result["start"], "end": result["end"], "outcome": result["outcome"],
                     "calendar_days": round(float(result["calendar_days"]), 1),
                     "trades": result["trades"],
                     "p1_outcome": result["p1"].outcome,
                     "p2_outcome": result["p2"].outcome if result["p2"] is not None else "NOT_STARTED"})
    return pd.DataFrame(rows)


def walk_forward_evaluation(p: Prepared, bt: Backtester, train_days: int = 365,
                           test_days: int = 90, step_days: int = 90,
                           minimum_bars: int = 500) -> pd.DataFrame:
    """Evaluate fixed, predeclared rules across rolling future windows.

    The strategy is not fitted on each train window; `train_days` is a warm-up /
    development window before each test window. Test windows are chronologically
    out-of-sample, and each starts with a fresh notional balance. For actual model
    selection, log every variant tried and reserve genuinely newer data afterwards.
    """
    if min(train_days, test_days, step_days) <= 0:
        raise ValueError("Walk-forward train/test/step day counts must be positive.")
    idx = p.index
    next_test = idx[0] + pd.Timedelta(days=train_days)
    last = idx[-1]
    rows = []
    fold = 1
    while next_test < last:
        test_end = next_test + pd.Timedelta(days=test_days)
        lo = int(idx.searchsorted(next_test))
        hi = int(idx.searchsorted(test_end))
        if hi - lo >= minimum_bars:
            res = bt.run(p, lo, min(hi, len(idx)), enforce_prop_rules=False, stop_on_result=False)
            m = trade_metrics(res)
            rows.append({"fold": fold, "test_start": res.start, "test_end": res.end,
                         "train_warmup_days": train_days, "test_days": test_days, **m})
            fold += 1
        next_test += pd.Timedelta(days=step_days)
    return pd.DataFrame(rows)


def print_sequential_summary(df: pd.DataFrame) -> None:
    if df.empty:
        print("No challenge attempts: not enough data after start dates.")
        return
    counts = df.outcome.value_counts()
    print(f"Attempts: {len(df)} (overlapping start dates; not independent samples)")
    print(f"Both phases passed: {(df.outcome == 'PASS_BOTH').sum()} / {len(df)} ({(df.outcome == 'PASS_BOTH').mean()*100:.1f}%)")
    resolved = ~df.outcome.str.contains("INCOMPLETE|STOP_", regex=True)
    if resolved.any():
        print(f"Resolved outcome pass rate: {(df.loc[resolved, 'outcome'] == 'PASS_BOTH').mean()*100:.1f}% over {int(resolved.sum())} resolved attempts")
    for outcome, count in counts.items():
        print(f"  {outcome:<24} {count:>4} ({count/len(df)*100:.1f}%)")
    passed = df[df.outcome == "PASS_BOTH"]
    if not passed.empty:
        print(f"Median days to pass both: {passed.calendar_days.median():.1f}")


def apply_news_blackout(df: pd.DataFrame, sig: pd.DataFrame, csv_path: str | Path,
                        before_minutes: int = 15, after_minutes: int = 15) -> tuple[pd.DataFrame, int]:
    """Suppress new entries around caller-supplied high-impact event times in UTC.

    CSV must have one column named time_utc, time or datetime. Naive timestamps
    are explicitly interpreted as UTC. Existing exits are left untouched.
    """
    if before_minutes < 0 or after_minutes < 0:
        raise ValueError("News blackout minutes must be non-negative.")
    raw = pd.read_csv(csv_path)
    cols = {str(c).strip().lower(): c for c in raw.columns}
    column = next((cols[k] for k in ("time_utc", "time", "datetime") if k in cols), None)
    if column is None:
        raise ValueError("News CSV requires a time_utc, time or datetime column.")
    events = pd.DatetimeIndex(pd.to_datetime(raw[column], errors="raise", utc=True)).sort_values()
    close_times = df.index + pd.Timedelta(minutes=5)
    blocked = np.zeros(len(df), dtype=bool)
    for event in events:
        left, right = event - pd.Timedelta(minutes=before_minutes), event + pd.Timedelta(minutes=after_minutes)
        blocked |= np.asarray((close_times >= left) & (close_times <= right))
    out = sig.copy()
    suppressed = int(((out.side != 0).to_numpy() & blocked).sum())
    out.loc[blocked, "side"] = 0
    out.loc[blocked, "target"] = np.nan
    return out, suppressed


def print_strategy_report(df: pd.DataFrame, inst: Instrument, costs: Costs, risk: RiskConfig,
                          strategy: Strategy, rules_p1: PropRules, rules_p2: PropRules, step_days: int,
                          do_oos: bool, run_challenges: bool, do_stress: bool, outdir: Path,
                          news_blackout_csv: Optional[str] = None,
                          news_before_minutes: int = 15, news_after_minutes: int = 15) -> None:
    print(f"\n{'='*72}\nSTRATEGY: {strategy.name}\n{'='*72}")
    sig = strategy.generate(df)
    suppressed_news_signals = 0
    if news_blackout_csv:
        sig, suppressed_news_signals = apply_news_blackout(df, sig, news_blackout_csv,
                                                           news_before_minutes, news_after_minutes)
        print(f"News blackout active: {news_blackout_csv}; suppressed {suppressed_news_signals} candidate entries")
    bt = Backtester(inst, costs, rules_p1, risk)
    prepared = prepare(df, sig, inst, costs, rules_p1)
    # Unconstrained research performance measures strategy expectancy without
    # turning target/limit hits into an early stop. Prop and internal guard stops are disabled.
    research = bt.run(prepared, enforce_prop_rules=False, stop_on_result=False)
    metrics = trade_metrics(research)
    print("RESEARCH RUN (full sample; not a challenge pass-rate estimate)")
    for k, v in metrics.items(): print(f"  {k:<24} {v}")
    outdir.mkdir(parents=True, exist_ok=True)
    trades_path = outdir / f"{strategy.name}_research_trades.csv"
    equity_path = outdir / f"{strategy.name}_research_equity.csv"
    research.trades.to_csv(trades_path, index=False)
    research.equity.rename("equity").to_csv(equity_path, index_label="time")
    print(f"Trade log saved: {trades_path}")
    print(f"Equity curve saved: {equity_path}")
    report_data = {"strategy": strategy.name, "source": str(df.attrs.get("source", "in-memory")),
                   "bars": len(df), "data_start_utc": str(df.index[0]), "data_end_utc": str(df.index[-1]),
                   "instrument": inst.__dict__, "costs": costs.__dict__, "research_metrics": metrics,
                   "news_blackout_csv": news_blackout_csv,
                   "news_signals_suppressed": suppressed_news_signals,
                   "research_outcome": research.outcome, "note": "Historical research only; metrics are not a verified challenge pass probability."}
    if do_oos:
        n = len(df); cuts = [int(n * .60), int(n * .80), n]
        print("CHRONOLOGICAL 60/20/20 SPLIT (segments start with fresh balance)")
        labels = [("DEVELOPMENT", 0, cuts[0]), ("VALIDATION", cuts[0], cuts[1]), ("LOCKBOX", cuts[1], cuts[2])]
        report_data["chronological_segments"] = {}
        for label, lo, hi in labels:
            if hi - lo < 500:
                print(f"  {label}: insufficient bars")
                continue
            # Firm and internal challenge stop rules disabled so segment expectancy can be measured.
            r = bt.run(prepared, lo, hi, enforce_prop_rules=False, stop_on_result=False)
            seg_metrics = trade_metrics(r)
            report_data["chronological_segments"][label.lower()] = seg_metrics
            print(f"  {label} {r.start:%Y-%m-%d} -> {r.end:%Y-%m-%d}: {seg_metrics}")
    print("ROLLING WALK-FORWARD EVALUATION (fixed strategy; 365-day warm-up, 90-day test folds)")
    wf = walk_forward_evaluation(prepared, bt)
    report_data["walk_forward"] = []
    if wf.empty:
        print("  Not enough chronological history for the default 365/90/90-day folds.")
    else:
        positive = 0
        for _, row in wf.iterrows():
            fold_metrics = {k: row[k] for k in wf.columns if k not in {"fold", "test_start", "test_end", "train_warmup_days", "test_days"}}
            row_obj = {"fold": int(row["fold"]), "test_start": str(row["test_start"]),
                       "test_end": str(row["test_end"]), **fold_metrics}
            report_data["walk_forward"].append(row_obj)
            expectancy = row.get("expectancy_$", np.nan)
            if pd.notna(expectancy) and expectancy > 0:
                positive += 1
            print(f"  Fold {int(row['fold'])}: {row['test_start']:%Y-%m-%d} -> {row['test_end']:%Y-%m-%d}; "
                  f"trades={row.get('trades', 0)}, PF={row.get('profit_factor', np.nan)}, "
                  f"expectancy=${expectancy if pd.notna(expectancy) else float('nan'):.2f}, "
                  f"net={row.get('net_return_%', np.nan)}%")
        report_data["walk_forward_summary"] = {"folds": int(len(wf)), "positive_expectancy_folds": int(positive),
                                                "positive_fold_pct": round(100 * positive / len(wf), 2)}
        print(f"  Positive-expectancy folds: {positive}/{len(wf)} ({positive/len(wf)*100:.1f}%)")
    if not wf.empty:
        wf_path = outdir / f"{strategy.name}_walk_forward.csv"
        wf.to_csv(wf_path, index=False)
        print(f"Walk-forward details saved: {wf_path}")
    if run_challenges:
        print("SEQUENTIAL FUNDINGPIPS CHALLENGE SIMULATION (P1 then P2)")
        attempts = rolling_sequential_challenges(prepared, inst, costs, risk, step_days=step_days,
                                                 rules_p1=rules_p1, rules_p2=rules_p2)
        print_sequential_summary(attempts)
        if not attempts.empty:
            path = outdir / f"{strategy.name}_sequential_attempts.csv"
            attempts.to_csv(path, index=False)
            print(f"Attempt-level details saved: {path}")
            report_data["sequential_challenge_summary"] = {
                "attempts": int(len(attempts)),
                "both_passed": int((attempts.outcome == "PASS_BOTH").sum()),
                "pass_rate_all_starts_pct": round(float((attempts.outcome == "PASS_BOTH").mean() * 100), 3),
                "outcomes": {str(k): int(v) for k, v in attempts.outcome.value_counts().items()},
            }
        if do_stress:
            print("STRESS TEST: spread x2 and slippage x2 (commission unchanged)")
            stress_costs = replace(costs, spread_mult=costs.spread_mult * 2.0, slippage=costs.slippage * 2.0)
            stress_prepared = prepare(df, sig, inst, stress_costs, rules_p1)
            stress_attempts = rolling_sequential_challenges(stress_prepared, inst, stress_costs, risk,
                                                            step_days=step_days, rules_p1=rules_p1, rules_p2=rules_p2)
            print_sequential_summary(stress_attempts)
            stress_path = outdir / f"{strategy.name}_stress_attempts.csv"
            stress_attempts.to_csv(stress_path, index=False)
            report_data["stress_summary"] = {
                "stress_costs": stress_costs.__dict__, "attempts": int(len(stress_attempts)),
                "both_passed": int((stress_attempts.outcome == "PASS_BOTH").sum()) if not stress_attempts.empty else 0,
                "outcomes": {str(k): int(v) for k, v in stress_attempts.outcome.value_counts().items()} if not stress_attempts.empty else {},
            }
            print(f"Stress attempt details saved: {stress_path}")
    report_path = outdir / f"{strategy.name}_research_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, default=str, allow_nan=True)
    print(f"Summary saved: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="FundingPips strategy research V3 (historical/demo only)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--mt5", metavar="SYMBOL", help="Fetch M5 history from running MT5 terminal")
    source.add_argument("--csv", help="OHLC CSV file")
    source.add_argument("--synthetic", action="store_true", help="Synthetic data for plumbing tests only")
    parser.add_argument("--days", type=int, default=1095, help="History days for --mt5 (default 1095 = 3 years)")
    parser.add_argument("--csv-timezone", choices=("utc", "broker"), default="utc", help="Interpret naive CSV timestamps as UTC or broker-server time")
    parser.add_argument("--server-utc-offset", type=float, default=3.0, help="Broker time minus UTC, used only for --csv-timezone broker")
    parser.add_argument("--strategy", choices=tuple(STRATEGIES) + ("all",), default="all")
    parser.add_argument("--daily-loss-config", choices=("5pct", "3pct"), default="5pct",
                        help="Choose the FundingPips daily-loss configuration purchased for the evaluation")
    parser.add_argument("--step-days", type=int, default=14, help="Calendar days between rolling challenge start dates")
    parser.add_argument("--no-oos", action="store_true", help="Skip chronological research report")
    parser.add_argument("--no-challenges", action="store_true", help="Skip sequential challenge simulations and cost stress")
    parser.add_argument("--no-stress", action="store_true", help="Skip spread/slippage stress tests")
    parser.add_argument("--news-blackout-csv", help="Optional CSV of high-impact event timestamps in UTC")
    parser.add_argument("--news-before-minutes", type=int, default=15)
    parser.add_argument("--news-after-minutes", type=int, default=15)
    parser.add_argument("--point", type=float, default=0.01, help="Instrument point; used for CSV input")
    parser.add_argument("--contract-size", type=float, default=100.0, help="Contract size per lot; used for CSV input")
    parser.add_argument("--volume-min", type=float, default=0.01, help="Minimum lot volume; used for CSV input")
    parser.add_argument("--volume-step", type=float, default=0.01, help="Lot volume step; used for CSV input")
    parser.add_argument("--volume-max", type=float, default=50.0, help="Maximum lot volume; used for CSV input")
    parser.add_argument("--commission-per-lot-side", type=float, default=2.50)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--fallback-spread-points", type=float, default=30.0)
    parser.add_argument("--spread-mult", type=float, default=1.0)
    parser.add_argument("--risk-pct", type=float, default=0.25, help="Risk percent per trade; default 0.25")
    parser.add_argument("--daily-guard-pct", type=float, default=1.0, help="Internal daily equity guard percent")
    parser.add_argument("--total-guard-pct", type=float, default=4.0, help="Internal peak/start equity guard percent")
    parser.add_argument("--outdir", default=".", help="Directory for reports")
    a = parser.parse_args()

    if a.synthetic:
        df, inst = synthetic_data(), Instrument()
        print("WARNING: synthetic random-walk data; plumbing tests only, not trading evidence.")
    elif a.mt5:
        df, inst = load_mt5(a.mt5, a.days)
        print(f"MT5 data: {a.mt5}; requested {a.days} days, received {len(df):,} M5 bars from {df.index[0]} to {df.index[-1]} UTC")
        if (df.index[-1] - df.index[0]).days < min(a.days - 14, int(a.days * 0.8)):
            print("WARNING: received history is materially shorter than requested. Check MT5 'Max bars in chart' and wait for history download.")
    else:
        df = load_csv(a.csv, a.csv_timezone, a.server_utc_offset)
        inst = Instrument(point=a.point, contract_size=a.contract_size, volume_min=a.volume_min,
                          volume_step=a.volume_step, volume_max=a.volume_max)
        print(f"CSV data: {len(df):,} bars from {df.index[0]} to {df.index[-1]} UTC")
    costs = Costs(a.commission_per_lot_side, a.slippage, a.fallback_spread_points, a.spread_mult)
    risk = RiskConfig(risk_per_trade=a.risk_pct / 100.0,
                      soft_daily_stop_pct=a.daily_guard_pct / 100.0,
                      soft_total_drawdown_pct=a.total_guard_pct / 100.0)
    selected = list(STRATEGIES) if a.strategy == "all" else [a.strategy]
    outdir = Path(a.outdir); outdir.mkdir(parents=True, exist_ok=True)
    if a.daily_loss_config == "3pct":
        rules_p1, rules_p2 = RULES["fundingpips_2step_p1_3pct_daily"], RULES["fundingpips_2step_p2_3pct_daily"]
    else:
        rules_p1, rules_p2 = RULES["fundingpips_2step_p1"], RULES["fundingpips_2step_p2"]
    for key in selected:
        strat = STRATEGIES[key]()
        print_strategy_report(df, inst, costs, risk, strat, rules_p1, rules_p2, a.step_days,
                              do_oos=not a.no_oos, run_challenges=not a.no_challenges,
                              do_stress=(not a.no_stress and not a.no_challenges), outdir=outdir,
                              news_blackout_csv=a.news_blackout_csv,
                              news_before_minutes=a.news_before_minutes,
                              news_after_minutes=a.news_after_minutes)


if __name__ == "__main__":
    main()
