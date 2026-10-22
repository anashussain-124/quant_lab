"""Fail-closed loader for externally sourced prop-firm profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_profile(path: Path) -> dict[str, Any]:
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or not record.get("source_url") or "values" not in record:
        raise ValueError(f"invalid risk profile: {path}")
    for key, item in record["values"].items():
        if not isinstance(item, dict) or item.get("tag") not in {"ASSUMPTION", "VERIFIED"}:
            raise ValueError(f"missing provenance tag for {key}")
        if item["tag"] == "ASSUMPTION" and item.get("value") is not None:
            raise ValueError(f"unverified profile value must be null: {key}")
    return record


def require_numeric(record: dict[str, Any], key: str) -> float:
    item = record["values"].get(key, {})
    if item.get("tag") != "VERIFIED" or item.get("value") is None:
        raise ValueError(f"risk value {key} is not verified")
    return float(item["value"])
