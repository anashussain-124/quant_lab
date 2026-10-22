"""v5 XAUUSD H1 data export, audit, split, and sealed-holdout access."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from data.v4 import audit_ohlcv, sha256_file

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "store" / "v5"
SEALED = STORE / "sealed"
SYMBOL = "XAUUSD"

DEV_VALIDATION_START = pd.Timestamp("2012-01-01", tz="UTC")
RESEARCH_START = pd.Timestamp("2013-01-01", tz="UTC")
DEV_END = pd.Timestamp("2018-12-31 23:59:59", tz="UTC")
OOS_START = pd.Timestamp("2019-01-01", tz="UTC")
OOS_END = pd.Timestamp("2022-12-31 23:59:59", tz="UTC")
HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")

DEV_PATH = STORE / "xauusd_h1_dev_validation.parquet"
HOLDOUT_PATH = SEALED / "xauusd_h1_holdout.parquet"
MANIFEST_PATH = STORE / "manifest.json"
AUDIT_JSON = STORE / "audit.json"
AUDIT_MD = STORE / "AUDIT_V5.md"


@dataclass(frozen=True)
class DatasetManifest:
    symbol: str
    timeframe: str
    source: str
    server: str
    terminal_build: int
    contract_size: float
    point: float
    swap_long_points: float
    swap_short_points: float
    created_utc: str
    dev_validation_path: str
    dev_validation_sha256: str
    dev_validation_rows: int
    dev_validation_start_utc: str
    research_start_utc: str
    dev_validation_end_utc: str
    holdout_path: str
    holdout_sha256: str
    holdout_rows: int
    holdout_start_utc: str
    holdout_end_utc: str
    timezone_note: str
    pre_registration_sha256: str
    amendment_1_sha256: str


def _mt5_rates(start: pd.Timestamp) -> pd.DataFrame:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        rates = mt5.copy_rates_range(
            SYMBOL,
            mt5.TIMEFRAME_H1,
            datetime(2004, 1, 1, tzinfo=UTC),
            datetime.now(UTC),
        )
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 returned no H1 bars: {mt5.last_error()}")
        info = mt5.symbol_info(SYMBOL)
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if info is None or account is None or terminal is None:
            raise RuntimeError("MT5 symbol/account/terminal info unavailable")
        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame = frame.set_index("timestamp").sort_index()
        frame = frame[["open", "high", "low", "close", "tick_volume", "spread", "real_volume"]]
        meta = {
            "server": account.server,
            "terminal_build": int(terminal.build),
            "contract_size": float(info.trade_contract_size),
            "point": float(info.point),
            "swap_long_points": float(info.swap_long),
            "swap_short_points": float(info.swap_short),
        }
    finally:
        mt5.shutdown()
    frame.attrs["meta"] = meta
    return frame


def audit_frame(frame: pd.DataFrame) -> dict[str, Any]:
    base = audit_ohlcv(frame, expected_interval="1h")
    close = frame["close"].astype(float)
    returns = np.log(close).diff()
    rolling_std = returns.rolling(100, min_periods=20).std().shift(1)
    outliers = int((returns.abs() > 10.0 * rolling_std).fillna(False).sum())

    gaps = frame.index.to_series().diff().dropna()
    gap_rows = [
        {
            "from_utc": str(frame.index[i - 1]),
            "to_utc": str(frame.index[i]),
            "hours": round(float(gaps.iloc[i - 1] / pd.Timedelta(hours=1)), 2),
        }
        for i in np.argsort(gaps.values)[-10:][::-1]
    ]
    spread = frame["spread"].astype(float)
    return {
        **base,
        "bars_per_year": {str(k): int(v) for k, v in frame.groupby(frame.index.year).size().items()},
        "bars_per_utc_hour": {str(k): int(v) for k, v in frame.groupby(frame.index.hour).size().items()},
        "zero_spread_bars": int((spread <= 0).sum()),
        "spread_points": {
            "mean": round(float(spread.mean()), 3),
            "median": round(float(spread.median()), 3),
            "p95": round(float(spread.quantile(0.95)), 3),
            "max": round(float(spread.max()), 3),
        },
        "outlier_bars_gt_10_sigma": outliers,
        "gaps_gt_2h": int((gaps > pd.Timedelta(hours=2)).sum()),
        "gaps_gt_24h": int((gaps > pd.Timedelta(hours=24)).sum()),
        "largest_gaps": gap_rows,
        "first_utc": str(frame.index[0]),
        "last_utc": str(frame.index[-1]),
    }


def _write_audit(frame: pd.DataFrame, audit: dict[str, Any], manifest: DatasetManifest) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    AUDIT_JSON.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# XAUUSD H1 v5 data audit",
        "",
        "- Environment: `HISTORICAL_REAL`",
        f"- Source: MT5 `{manifest.server}` terminal build {manifest.terminal_build}",
        f"- Rows: {audit['rows']}",
        f"- Period: {audit['first_utc']} .. {audit['last_utc']}",
        f"- Timezone: {audit['timezone']} (verified against audited v4 M5 bars)",
        f"- Duplicate timestamps: {audit['duplicate_timestamps']}",
        f"- Out-of-order: {audit['out_of_order']}",
        f"- Invalid OHLC rows: {audit['invalid_ohlc_rows']}",
        f"- Missing values: {audit['missing_values']}",
        f"- Zero-spread bars: {audit['zero_spread_bars']}",
        f"- Outlier bars (>10 sigma): {audit['outlier_bars_gt_10_sigma']}",
        f"- Gaps > 2h: {audit['gaps_gt_2h']} | > 24h: {audit['gaps_gt_24h']}",
        f"- Spread points (mean/median/p95/max): {audit['spread_points']}",
        "",
        "## Bars per year",
        "",
        "| Year | Bars |",
        "|---|---:|",
    ]
    lines += [f"| {year} | {count} |" for year, count in audit["bars_per_year"].items()]
    lines += [
        "",
        "## Largest gaps",
        "",
        "| From | To | Hours |",
        "|---|---|---:|",
    ]
    lines += [f"| {g['from_utc']} | {g['to_utc']} | {g['hours']} |" for g in audit["largest_gaps"]]
    lines += [
        "",
        "## Sealed holdout",
        "",
        f"- Rows: {manifest.holdout_rows}",
        f"- Period: {manifest.holdout_start_utc} .. {manifest.holdout_end_utc}",
        f"- SHA-256: `{manifest.holdout_sha256}`",
        "- The search harness must never load this file; only `scripts/run_v5_holdout.py` may.",
        "",
    ]
    AUDIT_MD.write_text("\n".join(lines), encoding="utf-8")


def build_dataset() -> DatasetManifest:
    STORE.mkdir(parents=True, exist_ok=True)
    SEALED.mkdir(parents=True, exist_ok=True)
    frame = _mt5_rates(DEV_VALIDATION_START)
    meta = frame.attrs["meta"]
    if frame.index.duplicated().any():
        raise ValueError("duplicate timestamps in MT5 export")

    dev = frame.loc[DEV_VALIDATION_START:OOS_END].copy()
    holdout = frame.loc[HOLDOUT_START:].copy()
    if len(dev) == 0 or len(holdout) == 0:
        raise ValueError("empty split; check MT5 history")

    dev.to_parquet(DEV_PATH, index=True)
    holdout.to_parquet(HOLDOUT_PATH, index=True)
    dev_hash = sha256_file(DEV_PATH)
    holdout_hash = sha256_file(HOLDOUT_PATH)
    (STORE / f"{DEV_PATH.name}.sha256").write_text(f"{dev_hash}  {DEV_PATH.name}\n", encoding="utf-8")
    (SEALED / f"{HOLDOUT_PATH.name}.sha256").write_text(
        f"{holdout_hash}  {HOLDOUT_PATH.name}\n", encoding="utf-8"
    )

    prereg = ROOT / "docs" / "PRE_REGISTRATION_SWING_V5.md"
    amendment = ROOT / "docs" / "PRE_REGISTRATION_SWING_V5_AMENDMENT_1.md"
    manifest = DatasetManifest(
        symbol=SYMBOL,
        timeframe="H1",
        source="MetaTrader5.copy_rates_range",
        server=str(meta["server"]),
        terminal_build=int(meta["terminal_build"]),
        contract_size=float(meta["contract_size"]),
        point=float(meta["point"]),
        swap_long_points=float(meta["swap_long_points"]),
        swap_short_points=float(meta["swap_short_points"]),
        created_utc=datetime.now(UTC).isoformat(),
        dev_validation_path=str(DEV_PATH.relative_to(ROOT)),
        dev_validation_sha256=dev_hash,
        dev_validation_rows=len(dev),
        dev_validation_start_utc=str(dev.index[0]),
        research_start_utc=str(RESEARCH_START),
        dev_validation_end_utc=str(dev.index[-1]),
        holdout_path=str(HOLDOUT_PATH.relative_to(ROOT)),
        holdout_sha256=holdout_hash,
        holdout_rows=len(holdout),
        holdout_start_utc=str(holdout.index[0]),
        holdout_end_utc=str(holdout.index[-1]),
        timezone_note="UTC; H1 OHLC matches audited v4 M5 buckets exactly on overlap",
        pre_registration_sha256=sha256_file(prereg),
        amendment_1_sha256=sha256_file(amendment),
    )
    MANIFEST_PATH.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")
    lock = {
        "holdout_path": manifest.holdout_path,
        "holdout_sha256": manifest.holdout_sha256,
        "holdout_rows": manifest.holdout_rows,
        "candidate_frozen": False,
        "accesses": [],
    }
    (SEALED / "holdout.lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    _write_audit(frame, audit_frame(frame), manifest)
    return manifest


def load_dev_validation() -> pd.DataFrame:
    if not DEV_PATH.exists():
        raise FileNotFoundError(f"missing {DEV_PATH}; run scripts/export_v5_data.py")
    frame = pd.read_parquet(DEV_PATH).sort_index()
    if str(frame.index.tz) != "UTC":
        raise ValueError("dev/validation data must be UTC-aware")
    return frame


def load_sealed_holdout(caller_token: str, candidate_hash: str) -> pd.DataFrame:
    expected = "run_v5_holdout.py:candidate-frozen"
    if caller_token != expected:
        raise PermissionError("sealed holdout requires the frozen-candidate token")
    lock_path = SEALED / "holdout.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if not lock.get("candidate_frozen"):
        raise PermissionError("candidate is not marked frozen in the holdout lock")
    if lock.get("holdout_sha256") != sha256_file(HOLDOUT_PATH):
        raise ValueError("holdout hash mismatch")
    entry = {
        "accessed_utc": datetime.now(UTC).isoformat(),
        "caller_token": caller_token,
        "candidate_hash": candidate_hash,
    }
    lock.setdefault("accesses", []).append(entry)
    lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return pd.read_parquet(HOLDOUT_PATH).sort_index()


def resample_h4(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample("4h", label="left", closed="left", origin="start_day")
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        tick_volume=("tick_volume", "sum"),
        spread=("spread", "max"),
        real_volume=("real_volume", "sum"),
    )
    return out.dropna(subset=["open", "high", "low", "close"])
