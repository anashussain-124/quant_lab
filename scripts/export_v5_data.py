"""Export and audit the v5 XAUUSD H1 development/validation and sealed holdout data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.data_v5 import (  # noqa: E402
    AUDIT_JSON,
    MANIFEST_PATH,
    build_dataset,
)


def main() -> int:
    manifest = build_dataset()
    audit = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    print("v5 dataset built")
    print(f"dev_validation_rows={manifest.dev_validation_rows}")
    print(f"dev_validation_start={manifest.dev_validation_start_utc}")
    print(f"dev_validation_end={manifest.dev_validation_end_utc}")
    print(f"holdout_rows={manifest.holdout_rows}")
    print(f"holdout_start={manifest.holdout_start_utc}")
    print(f"holdout_end={manifest.holdout_end_utc}")
    print(f"dev_sha256={manifest.dev_validation_sha256}")
    print(f"holdout_sha256={manifest.holdout_sha256}")
    print(f"duplicates={audit['duplicate_timestamps']} invalid={audit['invalid_ohlc_rows']} "
          f"missing={audit['missing_values']} zero_spread={audit['zero_spread_bars']} "
          f"outliers={audit['outlier_bars_gt_10_sigma']} gaps_gt_2h={audit['gaps_gt_2h']}")
    print(f"manifest={MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
