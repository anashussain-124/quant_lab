"""Build and audit the H4 research frame for program v6.

The H4 frame is derived deterministically from the frozen v5 H1 dev/validation
parquet. It never touches the sealed holdout. The resampler keeps the label
convention of the parent H1 file (broker server time labelled as UTC); v6 uses
no session-dependent rule, so only bucket boundaries are affected.

Outputs:
  data/store/v6/xauusd_h4_dev_validation.parquet
  data/store/v6/xauusd_h4_dev_validation.parquet.sha256
  data/store/v6/audit_v6.json
  data/store/v6/manifest_v6.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.v4 import sha256_file  # noqa: E402
from research.data_v5 import (  # noqa: E402
    DEV_PATH,
    MANIFEST_PATH,
    load_dev_validation,
    resample_h4,
)

STORE = ROOT / "data" / "store" / "v6"
H4_PATH = STORE / "xauusd_h4_dev_validation.parquet"
AUDIT_PATH = STORE / "audit_v6.json"
MANIFEST_OUT = STORE / "manifest_v6.json"


def audit(frame: pd.DataFrame, parent: pd.DataFrame) -> dict[str, Any]:
    prices = frame[["open", "high", "low", "close"]]
    invalid = (
        prices.isna().any(axis=1)
        | (prices <= 0).any(axis=1)
        | (frame["high"] < frame["low"])
        | (frame["open"] < frame["low"])
        | (frame["open"] > frame["high"])
        | (frame["close"] < frame["low"])
        | (frame["close"] > frame["high"])
    )
    duplicate = frame.index.duplicated()
    spread = frame["spread"].to_numpy(dtype=float)
    by_year = {}
    for year, part in frame.groupby(frame.index.year):
        by_year[str(year)] = {
            "bars": int(len(part)),
            "zero_spread": int((part["spread"] <= 0).sum()),
        }

    # Every H4 OHLC field must be reproducible from the parent H1 bars.
    grouped = parent.resample("4h", label="left", closed="left", origin="start_day")
    check = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).dropna()
    joined = check.join(frame[["open", "high", "low", "close"]], how="inner", lsuffix="_h1")
    mismatch = (
        (joined["open_h1"] != joined["open"])
        | (joined["high_h1"] != joined["high"])
        | (joined["low_h1"] != joined["low"])
        | (joined["close_h1"] != joined["close"])
    )
    index_delta = parent.index.to_series().diff().dt.total_seconds()
    gaps_4h = int((index_delta > 6 * 3600).sum())

    fractions = {
        "invalid_row": float(invalid.mean()),
        "duplicate_timestamp": float(duplicate.mean()),
        "missing_value": float(prices.isna().any(axis=1).mean()),
        "zero_or_negative_price": float((prices <= 0).any(axis=1).mean()),
    }
    return {
        "rows": int(len(frame)),
        "start_utc": str(frame.index.min()),
        "end_utc": str(frame.index.max()),
        "timezone": str(frame.index.tz),
        "duplicate_timestamps": int(duplicate.sum()),
        "invalid_rows": int(invalid.sum()),
        "fractions": fractions,
        "max_fraction": max(fractions.values()),
        "passed": bool(max(fractions.values()) < 0.01),
        "bars_per_year": by_year,
        "spread": {
            "mean": float(np.mean(spread)),
            "median": float(np.median(spread)),
            "p95": float(np.quantile(spread, 0.95)),
            "max": float(np.max(spread)),
            "zero_share": float((spread <= 0).mean()),
        },
        "h1_reconstruction": {
            "checked_bars": int(len(joined)),
            "mismatches": int(mismatch.sum()),
            "passed": bool(len(joined) == len(frame) and not mismatch.any()),
        },
        "parent_gaps_gt_6h": gaps_4h,
    }


def main() -> int:
    STORE.mkdir(parents=True, exist_ok=True)
    parent = load_dev_validation()
    manifest_v5 = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    parent_hash = sha256_file(DEV_PATH)
    if parent_hash != manifest_v5["dev_validation_sha256"]:
        raise SystemExit("parent H1 hash does not match the v5 manifest")

    frame = resample_h4(parent)
    report = audit(frame, parent)

    frame.to_parquet(H4_PATH, index=True)
    h4_hash = sha256_file(H4_PATH)
    (STORE / f"{H4_PATH.name}.sha256").write_text(
        f"{h4_hash}  {H4_PATH.name}\n", encoding="utf-8"
    )

    manifest = {
        "program": "v6_h4_trend",
        "symbol": "XAUUSD",
        "timeframe": "H4",
        "source": "derived by research.data_v5.resample_h4 from frozen v5 H1",
        "parent_path": str(DEV_PATH.relative_to(ROOT)),
        "parent_sha256": parent_hash,
        "parent_rows": int(len(parent)),
        "created_utc": datetime.now(UTC).isoformat(),
        "path": str(H4_PATH.relative_to(ROOT)),
        "sha256": h4_hash,
        "rows": int(len(frame)),
        "start_utc": str(frame.index.min()),
        "end_utc": str(frame.index.max()),
        "resample_rule": "4h, label=left, closed=left, origin=start_day",
        "spread_rule": "max of the four H1 spread samples (conservative)",
        "timezone_note": (
            "Labels inherit the frozen H1 broker-server-time convention. No "
            "session-dependent rule is used; H4 bucket boundaries are labels only."
        ),
        "holdout_accessed": False,
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    AUDIT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps({"sha256": h4_hash, **report}, indent=2))
    if not report["passed"]:
        raise SystemExit("H4 data-quality gate failed")
    if not report["h1_reconstruction"]["passed"]:
        raise SystemExit("H4 frame is not reproducible from H1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
