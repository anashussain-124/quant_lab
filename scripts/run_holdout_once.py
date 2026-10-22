"""Explicit, one-shot final-holdout reader; never used by dry-run search."""

from __future__ import annotations

import argparse
from pathlib import Path

from data.holdout import read_final_holdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=Path("data/holdout.lock"))
    args = parser.parse_args()
    frame = read_final_holdout(args.input, args.lock, caller_token="run_holdout_once")
    print(f"holdout_rows={len(frame)} locked={args.lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
