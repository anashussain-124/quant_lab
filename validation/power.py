"""Detector-power calculations for explicitly labelled synthetic controls."""

from __future__ import annotations

import math


def minimum_detectable_mean_r(n: int, sample_std_r: float = 1.0, t_threshold: float = 3.0) -> float:
    """Mean R required for t > threshold under the supplied sample scale."""
    if n < 1 or sample_std_r < 0 or t_threshold <= 0:
        raise ValueError("n, sample_std_r, and t_threshold must be valid")
    return t_threshold * sample_std_r / math.sqrt(n)
