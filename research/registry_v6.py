"""Append-only v6 trial registry with provenance and duplicate-run protection."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from research.constants_v6 import (
    V6_COST_MODEL_VERSION,
    V6_DATASET_END_UTC,
    V6_DATASET_ID,
    V6_DATASET_SHA256,
    V6_DATASET_START_UTC,
    V6_GATE_IDS,
    V6_PROTOCOL_SHA256,
    V6Environment,
)
from research.strategies_v6 import all_specs

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}(?:-dirty)?|unknown)$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VALID_GATE_STATUSES = {
    "PASS",
    "FAIL",
    "NOT EVALUABLE",
    "NOT EVALUATED",
}


class V6RegistryError(RuntimeError):
    """The v6 registry cannot safely append or validate a trial record."""


class TrialStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True)
class V6Provenance:
    code_commit: str
    dataset_id: str
    dataset_sha256: str
    dataset_start_utc: str
    dataset_end_utc: str
    protocol_sha256: str
    cost_model_version: str
    random_seed: int
    engine_version: str


@dataclass(frozen=True)
class V6TrialRecord:
    run_id: str
    created_at_utc: datetime
    environment: V6Environment
    strategy_name: str
    config_id: str
    parameters: Mapping[str, object]
    command: str
    status: TrialStatus
    provenance: V6Provenance
    gate_results: Mapping[str, str]
    outcome: Mapping[str, object] | None = None
    failure_reason: str | None = None
    result_paths: tuple[str, ...] = ()


def validate_v6_provenance(provenance: V6Provenance) -> None:
    if not _COMMIT_RE.fullmatch(provenance.code_commit):
        raise ValueError("code_commit must be a Git SHA, SHA-dirty, or unknown")
    if provenance.dataset_id != V6_DATASET_ID:
        raise ValueError("dataset_id is not the frozen v6 development dataset")
    if not _SHA256_RE.fullmatch(provenance.dataset_sha256):
        raise ValueError("dataset_sha256 must be a lowercase SHA-256 hex digest")
    if provenance.dataset_sha256 != V6_DATASET_SHA256:
        raise ValueError("dataset_sha256 does not match the frozen H4 artifact")
    if not _SHA256_RE.fullmatch(provenance.protocol_sha256):
        raise ValueError("protocol_sha256 must be a lowercase SHA-256 hex digest")
    if provenance.protocol_sha256 != V6_PROTOCOL_SHA256:
        raise ValueError("protocol_sha256 does not match the frozen preregistration")
    if provenance.cost_model_version != V6_COST_MODEL_VERSION:
        raise ValueError("cost_model_version does not identify the v6 cost interface")
    if isinstance(provenance.random_seed, bool) or not isinstance(
        provenance.random_seed, int
    ):
        raise ValueError("random_seed must be an integer")
    if provenance.random_seed != 0:
        raise ValueError("random_seed must match the frozen seed 0")
    if not provenance.engine_version.strip():
        raise ValueError("engine_version is required")

    start = _parse_utc(provenance.dataset_start_utc, "dataset_start_utc")
    end = _parse_utc(provenance.dataset_end_utc, "dataset_end_utc")
    if end < start:
        raise ValueError("dataset_end_utc precedes dataset_start_utc")
    if start != _parse_utc(V6_DATASET_START_UTC, "frozen dataset start"):
        raise ValueError("dataset_start_utc does not match the frozen H4 artifact")
    if end != _parse_utc(V6_DATASET_END_UTC, "frozen dataset end"):
        raise ValueError("dataset_end_utc does not match the frozen H4 artifact")


def _parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return parsed


def validate_v6_trial_record(record: V6TrialRecord) -> None:
    if not _RUN_ID_RE.fullmatch(record.run_id):
        raise ValueError("run_id must be a non-empty stable identifier")
    if (
        record.created_at_utc.tzinfo is None
        or record.created_at_utc.utcoffset() != timedelta(0)
    ):
        raise ValueError("created_at_utc must be timezone-aware UTC")
    if record.environment not in (V6Environment.BACKTEST, V6Environment.SIMULATED):
        raise ValueError("v6 trial records must be labeled BACKTEST or SIMULATED")
    if not record.strategy_name.strip() or not record.command.strip():
        raise ValueError("strategy_name and command are required")
    frozen = {spec.config_id: spec for spec in all_specs()}
    spec = frozen.get(record.config_id)
    if spec is None:
        raise ValueError("config_id is not one of the 24 frozen v6 configurations")
    if dict(record.parameters) != spec.params:
        raise ValueError("trial parameters differ from the frozen base configuration")
    validate_v6_provenance(record.provenance)

    if set(record.gate_results) != set(V6_GATE_IDS):
        raise ValueError("gate_results must preserve outcomes for all ten gates")
    if any(value not in _VALID_GATE_STATUSES for value in record.gate_results.values()):
        raise ValueError("gate_results contains an unsupported status")
    if record.status is TrialStatus.FAILED and not (
        record.failure_reason and record.failure_reason.strip()
    ):
        raise ValueError("failed trials require a failure_reason")
    if record.status is TrialStatus.NOT_EVALUABLE and not (
        record.failure_reason and record.failure_reason.strip()
    ):
        raise ValueError("not-evaluable trials require a reason")
    if record.status is TrialStatus.COMPLETED and record.outcome is None:
        raise ValueError("completed trials require an outcome mapping")
    if any("holdout" in path.casefold() for path in record.result_paths):
        raise ValueError("result_paths may not reference holdout artifacts")

    try:
        json.dumps(_record_payload(record), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("trial record must be finite JSON-compatible data") from exc


def _record_payload(record: V6TrialRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "created_at_utc": record.created_at_utc.isoformat(),
        "environment": record.environment.value,
        "strategy_name": record.strategy_name,
        "config_id": record.config_id,
        "parameters": dict(record.parameters),
        "command": record.command,
        "status": record.status.value,
        "provenance": {
            "code_commit": record.provenance.code_commit,
            "dataset_id": record.provenance.dataset_id,
            "dataset_sha256": record.provenance.dataset_sha256,
            "dataset_start_utc": record.provenance.dataset_start_utc,
            "dataset_end_utc": record.provenance.dataset_end_utc,
            "protocol_sha256": record.provenance.protocol_sha256,
            "cost_model_version": record.provenance.cost_model_version,
            "random_seed": record.provenance.random_seed,
            "engine_version": record.provenance.engine_version,
        },
        "gate_results": dict(record.gate_results),
        "outcome": None if record.outcome is None else dict(record.outcome),
        "failure_reason": record.failure_reason,
        "result_paths": list(record.result_paths),
    }


class V6Registry:
    """Append one validated record per unique run id; never rewrite history."""

    def __init__(self, path: Path | str) -> None:
        resolved = Path(path).resolve(strict=False)
        if resolved.name.casefold() != "registry_v6.jsonl":
            raise V6RegistryError(
                "registry path must be named registry_v6.jsonl; v5 history is protected"
            )
        self.path = resolved
        self.lock_path = resolved.with_name(resolved.name + ".lock")

    def append(self, record: V6TrialRecord) -> None:
        validate_v6_trial_record(record)
        payload = json.dumps(
            _record_payload(record),
            allow_nan=False,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise V6RegistryError(
                f"registry lock exists; refusing concurrent/stale write: {self.lock_path}"
            ) from exc

        try:
            os.write(lock_fd, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(lock_fd)
            if self._contains_run_id(record.run_id):
                raise V6RegistryError(f"duplicate run_id: {record.run_id}")
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(lock_fd)
            with suppress(FileNotFoundError):
                self.lock_path.unlink()

    def _contains_run_id(self, run_id: str) -> bool:
        if not self.path.exists():
            return False
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise V6RegistryError(
                        f"blank registry line at {line_number}; refusing append"
                    )
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise V6RegistryError(
                        f"malformed registry record at line {line_number}"
                    ) from exc
                if not isinstance(existing, dict) or not isinstance(
                    existing.get("run_id"), str
                ):
                    raise V6RegistryError(
                        f"registry record at line {line_number} has no run_id"
                    )
                if existing["run_id"] == run_id:
                    return True
        return False
