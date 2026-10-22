"""Event-driven v5 backtester with next-open fills and explicit costs.

The simulator is intentionally small and deterministic. It only knows the
pre-registered Plan and the causal cost series; it has no access to the sealed
holdout.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.costs_v5 import CostModel, effective_spread_points
from research.strategies_v5 import Plan


@dataclass(frozen=True)
class Trade:
    config_id: str
    direction: int
    entry_index: int
    exit_index: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_distance: float
    atr_entry: float
    gross_r: float
    cost_r: float
    swap_r: float
    net_r: float
    reason: str
    bars_held: int
    open_fill: bool


@dataclass
class MarketData:
    index: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    hour: np.ndarray
    weekday: np.ndarray
    atr: np.ndarray
    spread_points: np.ndarray


def make_market(frame: pd.DataFrame, atr: np.ndarray, costs: CostModel) -> MarketData:
    return MarketData(
        index=frame.index,
        open=frame["open"].to_numpy(dtype=float),
        high=frame["high"].to_numpy(dtype=float),
        low=frame["low"].to_numpy(dtype=float),
        close=frame["close"].to_numpy(dtype=float),
        hour=frame.index.hour.to_numpy(),
        weekday=frame.index.weekday.to_numpy(),
        atr=np.asarray(atr, dtype=float),
        spread_points=effective_spread_points(frame, costs),
    )


def _cost_price(points: float, costs: CostModel) -> float:
    return (points * costs.point * 0.5 * costs.stress + costs.slippage_price
            + costs.commission_side_price)


def simulate_trade(
    md: MarketData,
    entry_index: int,
    direction: int,
    stop_distance: float,
    *,
    target_r: float | None,
    trail_atr: float | None,
    max_hold: int,
    exit_hour: int | None,
    exit_long: np.ndarray | None,
    exit_short: np.ndarray | None,
    costs: CostModel,
    config_id: str,
) -> Trade | None:
    """Simulate one trade from the open of `entry_index`.

    Stop-first, gaps at the open, trailing stop updated at bar close. Exit
    candidates at the same bar are resolved open-fill first, then stop, then
    target.
    """
    n = len(md.close)
    last = n - 1
    if entry_index < 0 or entry_index > last:
        return None
    if not np.isfinite(stop_distance) or stop_distance <= 0:
        return None
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")

    entry_price = float(md.open[entry_index])
    if not np.isfinite(entry_price):
        return None
    initial_stop = entry_price - direction * stop_distance
    target = None if target_r is None else entry_price + direction * target_r * stop_distance

    time_index = entry_index + max_hold
    if time_index > last:
        time_index = None
    check_end = (time_index - 1) if time_index is not None else last
    if check_end < entry_index:
        check_end = entry_index

    session_index = None
    if exit_hour is not None:
        search_end = time_index if time_index is not None else last
        hours = md.hour[entry_index:search_end + 1]
        hits = np.flatnonzero(hours == exit_hour)
        if hits.size:
            candidate = entry_index + int(hits[0])
            if candidate > entry_index:
                session_index = candidate

    count = check_end - entry_index + 1
    highs = md.high[entry_index:check_end + 1]
    lows = md.low[entry_index:check_end + 1]
    opens = md.open[entry_index:check_end + 1]
    closes = md.close[entry_index:check_end + 1]
    atrs = md.atr[entry_index:check_end + 1]

    if direction > 0:
        if trail_atr is not None:
            running = np.maximum.accumulate(closes)
            raw_trail = running - trail_atr * atrs
            prior = np.empty_like(raw_trail)
            prior[0] = -np.inf
            prior[1:] = raw_trail[:-1]
            stop_levels = np.maximum(initial_stop, np.maximum.accumulate(prior))
        else:
            stop_levels = np.full(count, initial_stop)
        stop_hits = np.flatnonzero(lows <= stop_levels)
        target_hits = (np.flatnonzero(highs >= target) if target is not None
                       else np.array([], dtype=int))
    else:
        if trail_atr is not None:
            running = np.minimum.accumulate(closes)
            raw_trail = running + trail_atr * atrs
            prior = np.empty_like(raw_trail)
            prior[0] = np.inf
            prior[1:] = raw_trail[:-1]
            stop_levels = np.minimum(initial_stop, np.minimum.accumulate(prior))
        else:
            stop_levels = np.full(count, initial_stop)
        stop_hits = np.flatnonzero(highs >= stop_levels)
        target_hits = (np.flatnonzero(lows <= target) if target is not None
                       else np.array([], dtype=int))

    events: list[tuple[int, int, float, str, bool]] = []
    if session_index is not None:
        events.append((session_index, 0, float(md.open[session_index]), "session", True))
    if time_index is not None:
        events.append((time_index, 1, float(md.open[time_index]), "time", True))

    if stop_hits.size:
        offset = int(stop_hits[0])
        index = entry_index + offset
        level = float(stop_levels[offset])
        gapped = opens[offset] <= level if direction > 0 else opens[offset] >= level
        price = float(opens[offset]) if gapped else level
        events.append((index, 2 if gapped else 3, price, "stop", bool(gapped)))

    if target_hits.size:
        offset = int(target_hits[0])
        index = entry_index + offset
        level = float(target)
        gapped = opens[offset] >= level if direction > 0 else opens[offset] <= level
        price = float(opens[offset]) if gapped else level
        events.append((index, 2 if gapped else 4, price, "target", bool(gapped)))

    signal_limit = min(check_end, last - 1)
    if signal_limit >= entry_index:
        signal_hits: np.ndarray | None = None
        if direction > 0 and exit_long is not None:
            signal_hits = np.flatnonzero(exit_long[entry_index:signal_limit + 1])
        elif direction < 0 and exit_short is not None:
            signal_hits = np.flatnonzero(exit_short[entry_index:signal_limit + 1])
        if signal_hits is not None and signal_hits.size:
            exit_bar = entry_index + int(signal_hits[0]) + 1
            events.append((exit_bar, 1, float(md.open[exit_bar]), "signal", True))

    if events:
        index, _, price, reason, open_fill = min(events, key=lambda item: (item[0], item[1]))
        exit_index = int(index)
        exit_price = float(price)
    else:
        exit_index = last
        exit_price = float(md.close[last])
        reason = "end_of_data"
        open_fill = False

    if exit_index < entry_index:
        exit_index = entry_index
        exit_price = entry_price

    gross_r = direction * (exit_price - entry_price) / stop_distance
    cost_r = (_cost_price(float(md.spread_points[entry_index]), costs)
              + _cost_price(float(md.spread_points[exit_index]), costs)) / stop_distance

    swap_end = exit_index if open_fill else exit_index + 1
    swap_end = min(swap_end, n)
    swap_window_hours = md.hour[entry_index:swap_end]
    swap_window_days = md.weekday[entry_index:swap_end]
    rollovers = swap_window_hours == costs.swap_rollover_hour_utc
    nights = int(np.count_nonzero(rollovers))
    triple_nights = int(np.count_nonzero(rollovers & (swap_window_days == 2)))
    swap_points = (costs.swap_long_points if direction > 0 else costs.swap_short_points)
    swap_r = swap_points * costs.point * costs.stress * (nights + 2 * triple_nights) / stop_distance

    net_r = gross_r - cost_r + swap_r
    bars_held = max(1, exit_index - entry_index)

    return Trade(
        config_id=config_id,
        direction=direction,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_time=md.index[entry_index],
        exit_time=md.index[exit_index],
        entry_price=entry_price,
        exit_price=exit_price,
        stop_distance=float(stop_distance),
        atr_entry=float(md.atr[entry_index - 1]) if entry_index > 0 else float("nan"),
        gross_r=float(gross_r),
        cost_r=float(cost_r),
        swap_r=float(swap_r),
        net_r=float(net_r),
        reason=reason,
        bars_held=bars_held,
        open_fill=open_fill,
    )


def run_backtest(
    frame: pd.DataFrame,
    plan: Plan,
    costs: CostModel,
    *,
    start_time: pd.Timestamp | None = None,
    md: MarketData | None = None,
) -> list[Trade]:
    """Scan signals once and take non-overlapping trades until the data ends."""
    market = md or make_market(frame, plan.atr, costs)
    n = len(frame)
    if n == 0:
        return []
    start = plan.warmup
    if start_time is not None:
        start = max(start, int(frame.index.searchsorted(start_time)))

    trades: list[Trade] = []
    signal_index = start
    while signal_index < n - 1:
        if plan.long_signal[signal_index]:
            direction = 1
        elif plan.short_signal[signal_index]:
            direction = -1
        else:
            signal_index += 1
            continue

        atr_value = float(plan.atr[signal_index])
        if not np.isfinite(atr_value) or atr_value <= 0:
            signal_index += 1
            continue

        trade = simulate_trade(
            market,
            signal_index + 1,
            direction,
            plan.stop_atr * atr_value,
            target_r=plan.target_r,
            trail_atr=plan.trail_atr,
            max_hold=plan.max_hold,
            exit_hour=plan.exit_hour,
            exit_long=plan.exit_long,
            exit_short=plan.exit_short,
            costs=costs,
            config_id=plan.spec.config_id,
        )
        if trade is None:
            signal_index += 1
            continue
        trades.append(trade)
        signal_index = max(signal_index + 1, trade.exit_index)
    return trades


def max_drawdown_r(net_r: Sequence[float]) -> float:
    if not len(net_r):
        return 0.0
    equity = np.concatenate([[0.0], np.cumsum(np.asarray(net_r, dtype=float))])
    peak = np.maximum.accumulate(equity)
    return float(np.max(peak - equity))


def summarize(
    trades: Sequence[Trade],
    *,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    window_bars: int | None = None,
) -> dict:
    subset = [t for t in trades
              if (start is None or t.entry_time >= start) and (end is None or t.entry_time <= end)]
    n = len(subset)
    if n == 0:
        return {
            "n": 0, "mean_net_r": float("nan"), "total_net_r": 0.0, "win_rate": float("nan"),
            "profit_factor": float("nan"), "sharpe_per_trade": float("nan"),
            "max_drawdown_r": 0.0, "avg_bars_held": float("nan"), "exposure": float("nan"),
            "mean_gross_r": float("nan"), "mean_cost_r": float("nan"),
            "mean_swap_r": float("nan"), "yearly": {}, "atr_tercile_positive_share": [],
        }

    net = np.array([t.net_r for t in subset], dtype=float)
    gross = np.array([t.gross_r for t in subset], dtype=float)
    costs = np.array([t.cost_r for t in subset], dtype=float)
    swaps = np.array([t.swap_r for t in subset], dtype=float)
    wins = net[net > 0]
    losses = net[net < 0]
    if losses.size and losses.sum() != 0:
        profit_factor = float(wins.sum() / abs(losses.sum()))
    elif wins.size:
        profit_factor = float("inf")
    else:
        profit_factor = float("nan")
    std = float(net.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(net.mean() / std) if std > 0 else float("nan")

    yearly: dict[int, dict] = {}
    for year in sorted({t.entry_time.year for t in subset}):
        year_net = np.array([t.net_r for t in subset if t.entry_time.year == year], dtype=float)
        yearly[year] = {
            "n": int(year_net.size),
            "mean_net_r": float(year_net.mean()),
            "total_net_r": float(year_net.sum()),
            "win_rate": float((year_net > 0).mean()),
        }

    atr_values = np.array([t.atr_entry for t in subset], dtype=float)
    valid = np.isfinite(atr_values)
    terciles: list[float] = []
    if valid.sum() >= 3:
        edges = np.quantile(atr_values[valid], [1.0 / 3.0, 2.0 / 3.0])
        bucket = np.digitize(atr_values, edges)
        positive_total = float(net[net > 0].sum())
        for k in range(3):
            mask = valid & (bucket == k) & (net > 0)
            terciles.append(float(net[mask].sum() / positive_total) if positive_total > 0 else float("nan"))

    exposure = (sum(t.bars_held for t in subset) / window_bars) if window_bars else float("nan")
    return {
        "n": n,
        "mean_net_r": float(net.mean()),
        "total_net_r": float(net.sum()),
        "win_rate": float((net > 0).mean()),
        "profit_factor": profit_factor,
        "sharpe_per_trade": sharpe,
        "max_drawdown_r": max_drawdown_r(net),
        "avg_bars_held": float(np.mean([t.bars_held for t in subset])),
        "exposure": float(exposure),
        "mean_gross_r": float(gross.mean()),
        "mean_cost_r": float(costs.mean()),
        "mean_swap_r": float(swaps.mean()),
        "yearly": yearly,
        "atr_tercile_positive_share": terciles,
    }


def fold_summaries(
    trades: Sequence[Trade],
    *,
    years: Sequence[int],
    frame_index: pd.DatetimeIndex,
    embargo_bars: int = 24,
) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for year in years:
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year}-12-31 23:59:59", tz="UTC")
        first_index = int(frame_index.searchsorted(start))
        embargo_end = frame_index[min(first_index + embargo_bars, len(frame_index) - 1)]
        subset = [t for t in trades
                  if start <= t.entry_time <= end and t.entry_index >= frame_index.searchsorted(embargo_end)]
        net = np.array([t.net_r for t in subset], dtype=float)
        out[year] = {
            "n": int(net.size),
            "mean_net_r": float(net.mean()) if net.size else float("nan"),
            "total_net_r": float(net.sum()) if net.size else 0.0,
            "win_rate": float((net > 0).mean()) if net.size else float("nan"),
        }
    return out
