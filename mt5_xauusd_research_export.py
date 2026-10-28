#!/usr/bin/env python3
"""Export and quality-audit XAUUSD history from an already-installed MT5 terminal.

Read-only: this script does not place, modify, or close orders.

Requirements (Windows, in the Python environment used by VS Code):
    py -m pip install MetaTrader5 pandas

Keep MetaTrader 5 open and logged into the broker/demo server first.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError as exc:
    print("Missing dependency. Run: py -m pip install MetaTrader5 pandas")
    raise SystemExit(2) from exc

UTC = timezone.utc
TIMEFRAMES = {
    "M1": (mt5.TIMEFRAME_M1, 60),
    "M5": (mt5.TIMEFRAME_M5, 300),
}


def json_safe(value: Any) -> Any:
    if hasattr(value, "_asdict"):
        return {k: json_safe(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return str(value)
        return value
    return str(value)


def choose_symbol(requested: str | None) -> str:
    if requested:
        info = mt5.symbol_info(requested)
        if info is None:
            raise RuntimeError(f"Symbol '{requested}' was not found on this MT5 server. Check Market Watch spelling.")
        if not info.visible and not mt5.symbol_select(requested, True):
            raise RuntimeError(f"Found '{requested}', but failed to select it: {mt5.last_error()}")
        return requested

    symbols = mt5.symbols_get()
    if symbols is None:
        raise RuntimeError(f"Could not list MT5 symbols: {mt5.last_error()}")

    names = [s.name for s in symbols]
    candidates = [n for n in names if "xauusd" in n.lower() or n.lower() == "gold" or n.lower().startswith("gold.") or n.lower().startswith("gold")]
    if not candidates:
        raise RuntimeError("No XAUUSD/GOLD symbol found. Find the broker's gold symbol in Market Watch and rerun with --symbol SYMBOL_NAME.")

    # Prefer the most conventional symbol names, but print the selected exact feed.
    def rank(name: str) -> tuple[int, int, str]:
        low = name.lower()
        if low == "xauusd":
            pref = 0
        elif low == "gold":
            pref = 1
        elif low.startswith("xauusd"):
            pref = 2
        elif "xauusd" in low:
            pref = 3
        elif low.startswith("gold"):
            pref = 4
        else:
            pref = 5
        return (pref, len(name), low)

    candidates.sort(key=rank)
    selected = candidates[0]
    if len(candidates) > 1:
        print("Gold-like symbols found:", ", ".join(candidates[:20]))
        print(f"Auto-selecting '{selected}'. If this is not your intended feed, rerun with --symbol EXACT_NAME.")
    info = mt5.symbol_info(selected)
    if info is None:
        raise RuntimeError(f"Could not read selected symbol '{selected}'.")
    if not info.visible and not mt5.symbol_select(selected, True):
        raise RuntimeError(f"Could not add '{selected}' to Market Watch: {mt5.last_error()}")
    return selected


def bars_to_frame(raw: Any, seconds_per_bar: int, now_utc: datetime) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame(columns=["time_epoch", "time_utc", "open", "high", "low", "close", "tick_volume", "spread_points", "real_volume"])
    frame = pd.DataFrame(raw)
    if frame.empty:
        return frame
    frame["time_epoch"] = frame["time"].astype("int64")
    frame["time_utc"] = pd.to_datetime(frame["time_epoch"], unit="s", utc=True)
    # Exclude the current still-forming candle, which would otherwise be incomplete.
    cutoff = pd.Timestamp(now_utc)
    frame = frame[frame["time_utc"] + pd.to_timedelta(seconds_per_bar, unit="s") <= cutoff].copy()
    frame.rename(columns={"spread": "spread_points"}, inplace=True)
    keep = ["time_epoch", "time_utc", "open", "high", "low", "close", "tick_volume", "spread_points", "real_volume"]
    for col in keep:
        if col not in frame.columns:
            frame[col] = pd.NA
    return frame[keep].sort_values("time_epoch").reset_index(drop=True)


def audit_frame(frame: pd.DataFrame, timeframe_name: str, seconds_per_bar: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "timeframe": timeframe_name,
        "rows": int(len(frame)),
        "start_utc": None,
        "end_utc": None,
        "duplicate_timestamps": 0,
        "invalid_ohlc_rows": 0,
        "nonpositive_price_rows": 0,
        "time_gaps_over_expected_bar": 0,
        "largest_time_gap_minutes": None,
        "notes": [
            "MT5 bar timestamps are UTC bar-open times.",
            "Tick volume is the broker's tick count, not necessarily centralized exchange volume.",
            "Time gaps can represent weekends, market breaks, holidays, or unavailable history; inspect before treating them as missing data.",
        ],
    }
    if frame.empty:
        result["notes"].append("No bars returned for this timeframe and date range.")
        return result

    result["start_utc"] = frame["time_utc"].iloc[0].isoformat()
    result["end_utc"] = frame["time_utc"].iloc[-1].isoformat()
    result["duplicate_timestamps"] = int(frame["time_epoch"].duplicated().sum())
    numeric = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    invalid = (
        numeric.isna().any(axis=1)
        | (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
    )
    result["invalid_ohlc_rows"] = int(invalid.sum())
    result["nonpositive_price_rows"] = int((numeric <= 0).any(axis=1).sum())
    if len(frame) > 1:
        diffs = frame["time_epoch"].diff().dropna()
        result["time_gaps_over_expected_bar"] = int((diffs > seconds_per_bar).sum())
        result["largest_time_gap_minutes"] = round(float(diffs.max()) / 60.0, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MT5 gold M1/M5 data and create a provenance/audit report.")
    parser.add_argument("--symbol", help="Exact broker symbol, e.g. XAUUSD, XAUUSD.a, or GOLD")
    parser.add_argument("--years", type=float, default=3.0, help="History depth in years (default: 3)")
    parser.add_argument("--outdir", default="research_bundle/mt5_export", help="Output folder (default: research_bundle/mt5_export)")
    parser.add_argument("--skip-m1", action="store_true", help="Skip M1 download; export M5 only")
    args = parser.parse_args()

    if args.years <= 0 or args.years > 15:
        parser.error("--years must be > 0 and <= 15")

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        print("Open MetaTrader 5, log into the intended broker/demo server, then rerun this script.")
        return 2

    try:
        terminal = mt5.terminal_info()
        if terminal is None:
            raise RuntimeError(f"Could not read MT5 terminal info: {mt5.last_error()}")

        symbol = choose_symbol(args.symbol)
        sinfo = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if sinfo is None:
            raise RuntimeError(f"Could not read symbol details for '{symbol}'.")

        now = datetime.now(UTC)
        start = now - timedelta(days=int(round(args.years * 365.25)))
        symbol_info_fields = [
            "name", "description", "path", "digits", "point", "trade_tick_size",
            "trade_tick_value", "trade_tick_value_profit", "trade_tick_value_loss",
            "trade_contract_size", "volume_min", "volume_max", "volume_step",
            "currency_base", "currency_profit", "currency_margin", "trade_calc_mode",
            "spread", "trade_stops_level", "trade_freeze_level", "swap_long", "swap_short",
        ]
        symbol_snapshot = {field: json_safe(getattr(sinfo, field, None)) for field in symbol_info_fields}
        if tick is not None:
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            symbol_snapshot["snapshot_bid"] = bid
            symbol_snapshot["snapshot_ask"] = ask
            symbol_snapshot["snapshot_spread_price_units"] = (ask - bid) if bid and ask else None
            symbol_snapshot["snapshot_time_utc"] = datetime.fromtimestamp(int(tick.time), UTC).isoformat() if getattr(tick, "time", 0) else None
        symbol_snapshot["note"] = "Current spread/bid/ask are a snapshot, not historical spread statistics. Check commission, swaps, and symbol contract with the broker." 

        exports: dict[str, Any] = {}
        timeframes = ["M5"] if args.skip_m1 else ["M1", "M5"]
        for tf_name in timeframes:
            tf_enum, seconds_per_bar = TIMEFRAMES[tf_name]
            print(f"Downloading {symbol} {tf_name} bars from {start.isoformat()} to {now.isoformat()} ...")
            raw = mt5.copy_rates_range(symbol, tf_enum, start, now)
            if raw is None:
                raise RuntimeError(f"copy_rates_range failed for {tf_name}: {mt5.last_error()}")
            frame = bars_to_frame(raw, seconds_per_bar, now)
            csv_path = outdir / f"{symbol}_{tf_name}_{start:%Y%m%d}_{now:%Y%m%d}_UTC.csv"
            frame.to_csv(csv_path, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
            audit = audit_frame(frame, tf_name, seconds_per_bar)
            audit["csv_file"] = csv_path.name
            audit["file_bytes"] = int(csv_path.stat().st_size)
            exports[tf_name] = audit
            print(f"  {len(frame):,} rows -> {csv_path}")
            if len(frame) < 1000:
                print("  WARNING: fewer than 1,000 bars returned; increase MT5 Max bars in chart / request history, then rerun.")

        server_name = None
        try:
            account = mt5.account_info()
            if account is not None:
                server_name = getattr(account, "server", None)
        except Exception:
            pass

        metadata = {
            "created_utc": now.isoformat(),
            "requested_lookback_years": args.years,
            "requested_start_utc": start.isoformat(),
            "requested_end_utc": now.isoformat(),
            "selected_symbol": symbol,
            "terminal": {
                "name": getattr(terminal, "name", None),
                "company": getattr(terminal, "company", None),
                "connected": getattr(terminal, "connected", None),
                "maxbars": getattr(terminal, "maxbars", None),
                "data_path_available": bool(getattr(terminal, "data_path", None)),
                "broker_server": server_name,
            },
            "symbol_snapshot": symbol_snapshot,
            "exports": exports,
            "limitations": [
                "Only history available to the connected MT5 terminal/server is returned.",
                "MT5 CopyRates uses UTC timestamps; exported time_utc is UTC and time_epoch retains the raw epoch seconds.",
                "M1 and M5 are broker-feed bars; broker candles and costs may differ from TradingView TVC:GOLD.",
                "This script does not backtest the Pine Script. Pine Script cannot be run directly by MT5; a Python backtester or MQL5 EA equivalent is required for a strategy performance report.",
                "This script only reads/export data and does not send orders.",
            ],
        }
        (outdir / "metadata.json").write_text(json.dumps(json_safe(metadata), indent=2), encoding="utf-8")

        fp = {
            "as_of_date": now.date().isoformat(),
            "model_assumption": "FundingPips 2-Step Standard; confirm the exact purchased model and daily-loss add-on in the dashboard",
            "account_size_usd": 5000,
            "phase_1_profit_target_percent": 8,
            "phase_1_profit_target_usd": 400,
            "phase_2_profit_target_percent": 5,
            "phase_2_profit_target_usd": 250,
            "overall_max_loss_percent": 10,
            "overall_max_loss_usd": 500,
            "static_equity_balance_floor_usd": 4500,
            "daily_loss_configuration_options_percent": [3, 5],
            "daily_loss_configuration_options_usd_at_5000_reference": [150, 250],
            "daily_loss_baseline": "Higher of opening balance or opening equity; includes floating and closed losses; resets at 00:00 platform time UTC+3",
            "standard_metals_commission_usd_per_lot": 5,
            "swap_free_or_swing_metals_commission_usd_per_lot": 10,
            "gold_contract_size": "1 lot = 100 oz, according to official instrument list; verify symbol specification in MT5",
            "standard_evaluation_metals_leverage": "1:30 (check dashboard/current terms)",
            "official_source": "https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard",
            "important_note": "Use these as research assumptions only. Exact loss configuration and add-ons are account-specific; do not trade based on this file without checking the dashboard.",
        }
        (outdir / "fundingpips_5k_2step_standard_assumptions.json").write_text(json.dumps(fp, indent=2), encoding="utf-8")

        lines = [
            "# MT5 XAUUSD Historical Data Export & Initial Audit",
            "",
            f"- Created (UTC): {now.isoformat()}",
            f"- Broker symbol: `{symbol}`",
            f"- Requested lookback: {args.years:g} years",
            f"- Broker/server: `{server_name or 'not available'}`",
            "- Data use: research and offline backtesting only; no orders are placed.",
            "",
            "## Export audit",
            "",
            "| Timeframe | Rows | Actual start UTC | Actual end UTC | Duplicate timestamps | Invalid OHLC rows | Gaps > one bar | Largest gap (minutes) |",
            "|---|---:|---|---|---:|---:|---:|---:|",
        ]
        for tf_name, audit in exports.items():
            lines.append(
                f"| {tf_name} | {audit['rows']:,} | {audit['start_utc'] or 'N/A'} | {audit['end_utc'] or 'N/A'} | "
                f"{audit['duplicate_timestamps']} | {audit['invalid_ohlc_rows']} | {audit['time_gaps_over_expected_bar']} | {audit['largest_time_gap_minutes']} |"
            )
        lines += [
            "",
            "Gap counts are diagnostic only: normal weekends, market breaks, holidays, and missing broker history may all create timestamp gaps. Inspect them before classifying gaps as missing data.",
            "",
            "## Symbol snapshot",
            "",
            f"- Digits: `{symbol_snapshot.get('digits')}`",
            f"- Point: `{symbol_snapshot.get('point')}`",
            f"- Trade tick size: `{symbol_snapshot.get('trade_tick_size')}`",
            f"- Tick value: `{symbol_snapshot.get('trade_tick_value')}`",
            f"- Contract size: `{symbol_snapshot.get('trade_contract_size')}`",
            f"- Volume min/step/max: `{symbol_snapshot.get('volume_min')}` / `{symbol_snapshot.get('volume_step')}` / `{symbol_snapshot.get('volume_max')}`",
            f"- Snapshot spread (price units): `{symbol_snapshot.get('snapshot_spread_price_units')}`",
            "- Snapshot spread is not a historical spread distribution; historical tick data or an execution-cost assumption is needed for that.",
            "",
            "## FundingPips assumption",
            "",
            "The file `fundingpips_5k_2step_standard_assumptions.json` records the current official 2-Step Standard figures as research assumptions. Confirm the account model and daily-loss add-on on the actual dashboard before using them.",
            "",
            "## Not yet a strategy-performance report",
            "",
            "This export establishes the historical-data foundation. A Pine Script strategy cannot be run directly in MT5. Next, the strategy rules must be ported faithfully to a Python bar backtester or an MQL5 Expert Advisor, then validated against these exported bars with realistic costs. Do not describe this data audit as a completed strategy backtest.",
            "",
            "## Source documentation",
            "",
            "- MetaTrader 5 Python `copy_rates_range`: https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py",
            "- MT5 Market Watch/history export: https://www.metatrader5.com/en/terminal/help/trading/market_watch",
            "- FundingPips official 2-Step Standard rules: https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard",
            "",
        ]
        (outdir / "initial_data_audit.md").write_text("\n".join(lines), encoding="utf-8")

        print("\nExport complete.")
        print(f"Output folder: {outdir}")
        print(f"Audit report: {outdir / 'initial_data_audit.md'}")
        print(f"Metadata: {outdir / 'metadata.json'}")
        print(f"FundingPips assumptions: {outdir / 'fundingpips_5k_2step_standard_assumptions.json'}")
        if exports.get("M5", {}).get("rows", 0) < 100_000:
            print("\nNOTE: M5 returned fewer than 100,000 bars. This may be normal for limited history, but check the actual coverage and MT5 Max bars in chart setting.")
        print("\nNEXT: provide the research_bundle/mt5_export folder (zip it if easier). The strategy must then be ported to a separate backtester; no performance metrics were fabricated by this exporter.")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
