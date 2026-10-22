"""Append-only experiment registry for multiple-testing accounting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def code_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if ".venv" not in path.parts and ".git" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


class ExperimentRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, *, strategy: str, params: dict[str, Any], data_hash: str,
               code_hash_value: str, results: dict[str, Any], seed: int) -> dict[str, Any]:
        if not strategy or not data_hash or not code_hash_value:
            raise ValueError("strategy, data hash, and code hash are required")
        record = {"strategy": strategy, "params": params, "data_hash": data_hash,
                  "code_hash": code_hash_value, "seed": seed,
                  "timestamp_utc": datetime.now(timezone.utc).isoformat(), "results": results}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def trial_count(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())
