"""Fail-closed run authorization and dataset-path boundary for H4 v6."""

from __future__ import annotations

import os
from pathlib import Path

from research.constants_v6 import V6_DATASET_RELATIVE_PATH

UNRESOLVED_V6_DECISIONS = (
    "partial_h4_bin_policy",
    "h4_rollover_stop_ordering",
)


class V6RunBlockedError(RuntimeError):
    """The frozen v6 run is prohibited by unresolved protocol decisions."""

    def __init__(self, blockers: tuple[str, ...] = UNRESOLVED_V6_DECISIONS) -> None:
        self.blockers = blockers
        details = ", ".join(blockers)
        super().__init__(f"H4 v6 execution is blocked pending: {details}")


class V6DatasetPathError(ValueError):
    """A requested dataset path is outside the frozen v6 development artifact."""


def require_v6_run_ready() -> None:
    if UNRESOLVED_V6_DECISIONS:
        raise V6RunBlockedError()


def require_authorized_v6_dataset_path(
    project_root: Path, dataset_path: Path | str
) -> Path:
    """Accept only the exact frozen H4 development/validation artifact path."""
    root = project_root.resolve(strict=False)
    candidate = Path(dataset_path)
    if not candidate.is_absolute():
        candidate = root / candidate

    resolved_candidate = candidate.resolve(strict=False)
    expected = (root / V6_DATASET_RELATIVE_PATH).resolve(strict=False)
    try:
        resolved_candidate.relative_to(root)
    except ValueError as exc:
        raise V6DatasetPathError(
            "dataset path must remain inside the project root"
        ) from exc

    if os.path.normcase(str(resolved_candidate)) != os.path.normcase(str(expected)):
        raise V6DatasetPathError(
            "dataset path is not the allowlisted frozen v6 development artifact"
        )
    return resolved_candidate
