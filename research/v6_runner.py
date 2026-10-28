"""Fail-closed H4 v6 runner shell; grid execution is not implemented."""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from research.constants_v6 import (
    V6_COST_MODEL_VERSION,
    V6_DATASET_END_UTC,
    V6_DATASET_ID,
    V6_DATASET_SHA256,
    V6_DATASET_START_UTC,
    V6_PROTOCOL_SHA256,
    V6Environment,
)
from research.registry_v6 import V6Provenance, V6Registry, validate_v6_provenance
from research.v6_safety import (
    V6RunBlockedError,
    require_authorized_v6_dataset_path,
    require_v6_run_ready,
)

V6_ENGINE_VERSION = "v6-runner-shell-1"


class V6GridExecutionUnavailable(RuntimeError):
    """The fail-closed shell does not contain a grid execution implementation."""


@dataclass(frozen=True)
class V6Preflight:
    dataset_path: Path
    dataset_sha256: str
    protocol_sha256: str
    environment: V6Environment
    timezone_alignment_claim: str


class V6Runner:
    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve(strict=False)
        self.registry = V6Registry(
            self.project_root / "experiments" / "registry_v6.jsonl"
        )

    def preflight(
        self,
        dataset_path: Path | str,
        *,
        code_commit: str,
        random_seed: int,
    ) -> tuple[V6Preflight, V6Provenance]:
        require_v6_run_ready()
        approved_path = require_authorized_v6_dataset_path(
            self.project_root, dataset_path
        )
        protocol_path = self.project_root / "docs" / "PRE_REGISTRATION_H4_V6.md"
        dataset_hash = _sha256_file(approved_path)
        protocol_hash = _sha256_file(protocol_path)
        if dataset_hash != V6_DATASET_SHA256:
            raise ValueError("frozen v6 dataset hash mismatch")
        if protocol_hash != V6_PROTOCOL_SHA256:
            raise ValueError("frozen v6 preregistration hash mismatch")

        provenance = V6Provenance(
            code_commit=code_commit,
            dataset_id=V6_DATASET_ID,
            dataset_sha256=dataset_hash,
            dataset_start_utc=V6_DATASET_START_UTC,
            dataset_end_utc=V6_DATASET_END_UTC,
            protocol_sha256=protocol_hash,
            cost_model_version=V6_COST_MODEL_VERSION,
            random_seed=random_seed,
            engine_version=V6_ENGINE_VERSION,
        )
        validate_v6_provenance(provenance)
        return (
            V6Preflight(
                dataset_path=approved_path,
                dataset_sha256=dataset_hash,
                protocol_sha256=protocol_hash,
                environment=V6Environment.HISTORICAL_REAL,
                timezone_alignment_claim=(
                    "existing H4 artifact is simulation-authoritative; "
                    "true-UTC alignment is unverified"
                ),
            ),
            provenance,
        )

    def execute(
        self,
        dataset_path: Path | str,
        *,
        code_commit: str,
        random_seed: int,
    ) -> None:
        self.preflight(
            dataset_path, code_commit=code_commit, random_seed=random_seed
        )
        raise V6GridExecutionUnavailable(
            "v6 grid execution is intentionally absent pending reviewed implementation"
        )


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"required frozen input is unavailable: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed H4 v6 runner shell. It does not run a grid while "
            "partial-bin and rollover policies are unresolved."
        )
    )
    parser.parse_args(argv)
    try:
        require_v6_run_ready()
    except V6RunBlockedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "H4 v6 grid execution is not implemented; no configurations were run.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
