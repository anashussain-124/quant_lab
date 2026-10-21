"""Data Quality Engine — per AGENTS.md A4.

Computes data quality score (0-1) per dataset and per live stream.
Below threshold means NO_TRADE.

Checks: gaps, duplicates, out-of-order, stale quotes, crossed books,
zero/negative prices, spread anomalies, price jumps, clock skew, disconnects.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
import hashlib
from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass, field

from adapters import DataQualityScore, OrderBook
from engine.logger import AuditLogger


@dataclass
class QualityReport:
    """Full quality report for a dataset."""
    dataset_id: str
    data_hash: str
    score: float
    checks: DataQualityScore
    passed: bool
    timestamp: datetime = field(default_factory=datetime.now)
    details: dict[str, Any] = field(default_factory=dict)


class DataQualityEngine:
    """Computes data quality scores and flags bad data.

    Per AGENTS.md A4:
    - Data quality score (0–1) per dataset and per live stream
    - Below threshold means NO_TRADE
    - Leakage canary tests mandatory in CI
    """

    def __init__(
        self,
        threshold: float = 0.7,
        audit: AuditLogger | None = None,
    ) -> None:
        self.threshold = threshold
        self.audit = audit or AuditLogger()
        self._reports: list[QualityReport] = []

    def evaluate(self, data: pd.DataFrame, dataset_id: str) -> QualityReport:
        """Evaluate dataset quality and return report."""
        if len(data) == 0:
            report = QualityReport(
                dataset_id=dataset_id,
                data_hash="empty",
                score=0.0,
                checks=DataQualityScore(score=0.0, gaps=0),
                passed=False,
            )
            self._reports.append(report)
            return report

        checks = self._compute_all_checks(data)
        passed = checks.score >= self.threshold
        data_hash = self._compute_hash(data)

        report = QualityReport(
            dataset_id=dataset_id,
            data_hash=self._compute_hash(data),
            score=checks.score,
            checks=checks,
            passed=passed,
        )

        self._reports.append(report)

        if not passed:
            self.audit.log(
                event="DATA_QUALITY_FAILED",
                action=f"Dataset {dataset_id} quality {checks.score:.2f} < {self.threshold}",
                details=checks.to_dict(),
                actor="quality_engine",
            )

        return report

    def _compute_all_checks(self, data: pd.DataFrame) -> DataQualityScore:
        """Compute all quality checks."""
        score = 1.0
        checks = DataQualityScore(score=score)

        # 1. Check gaps (missing timestamps) — use absolute 1-hour threshold
        if "timestamp" in data.columns and len(data) > 1:
            ts = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
            time_diffs = ts.diff().dropna()
            if len(time_diffs) > 0:
                # Count diffs > 2 hours as gaps
                gaps = (time_diffs > pd.Timedelta(hours=2)).sum()
                checks.gaps = int(gaps)
                score -= min(gaps * 0.02, 0.2)

        # Check for duplicates
        if "timestamp" in data.columns:
            ts = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
            duplicates = ts.duplicated().sum()
            checks.duplicates = int(duplicates)
            score -= min(duplicates * 0.05, 0.2)

        # Check for out-of-order timestamps
        if "timestamp" in data.columns and len(data) > 1:
            ts = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
            out_of_order = (ts.diff().dropna() < pd.Timedelta(0)).sum()
            checks.out_of_order = int(out_of_order)
            score -= min(out_of_order * 0.02, 0.1)

        # Check for stale quotes
        if "timestamp" in data.columns:
            latest = pd.to_datetime(data["timestamp"], utc=True, errors="coerce").max()
            now = pd.Timestamp.now(tz="UTC")
            stale = (now - latest).total_seconds()
            if stale > 300:
                checks.stale_quotes = 1
                score -= 0.1

        # Check for crossed books
        if "ask" in data.columns and "bid" in data.columns:
            crossed = (data["ask"] < data["bid"]).sum()
            checks.crossed_books = int(crossed)
            score -= min(crossed * 0.05, 0.15)

        # Check for zero/negative prices
        price_cols = ["close", "bid", "ask", "open", "high", "low"]
        bad_prices = 0
        for col in price_cols:
            if col in data.columns:
                bad_prices += int((data[col] <= 0).sum())
        checks.zero_negative_prices = bad_prices
        score -= min(bad_prices * 0.05, 0.15)

        # Check spread anomalies
        if "ask" in data.columns and "bid" in data.columns:
            spreads = data["ask"] - data["bid"]
            if len(spreads) > 0:
                avg_spread = spreads.mean()
                anomalies = int((spreads > avg_spread * 5).sum())
                checks.spread_anomalies = anomalies
                score -= min(anomalies * 0.02, 0.1)

        # Check price jumps
        if "close" in data.columns and len(data) > 1:
            returns = data["close"].pct_change().dropna()
            jumps = int((returns.abs() > 0.1).sum())  # >10%
            checks.price_jumps = jumps
            score -= min(jumps * 0.03, 0.15)

        # Clock skew detection
        if "timestamp" in data.columns:
            latest = pd.to_datetime(data["timestamp"], utc=True, errors="coerce").max()
            now = pd.Timestamp.now(tz="UTC")
            skew = abs((now - latest).total_seconds())
            if skew > 60:
                checks.clock_skew = 1
                score -= 0.05

        # Disconnect detection
        if len(data) < 10:
            checks.disconnects = 1
            score -= 0.1

        checks.score = max(0.0, min(1.0, score))
        return checks

    def _compute_hash(self, data: pd.DataFrame) -> str:
        """Compute content hash."""
        content = data.to_json(orient="records", date_format="iso")
        return hashlib.sha256(content.encode()).hexdigest()

    def is_tradeable(self, dataset_id: str) -> bool:
        """Check if dataset passes quality threshold."""
        for report in self._reports:
            if report.dataset_id == dataset_id:
                return report.passed and report.score >= self.threshold
        return False

    def get_quality_report(self, dataset_id: str) -> QualityReport | None:
        """Get quality report by dataset ID."""
        for report in self._reports:
            if report.dataset_id == dataset_id:
                return report
        return None

    def inject_fault(
        self, data: pd.DataFrame, fault_type: str
    ) -> pd.DataFrame:
        """Inject faults for testing (A4 leakage canary).

        Fault types:
        - 'gaps': Remove random rows
        - 'duplicates': Add duplicate rows
        - 'out_of_order': Shuffle timestamps
        - 'crossed_books': Swap bid/ask
        - 'stale_quotes': Replace recent with old data
        - 'dst_shift': Add timezone offset
        """
        import copy
        faulty = copy.deepcopy(data)

        if fault_type == "gaps":
            drop_idx = np.random.choice(
                len(faulty), size=max(1, len(faulty) // 10), replace=False
            )
            faulty = faulty.drop(drop_idx)

        elif fault_type == "duplicates":
            dup_idx = np.random.choice(len(faulty), size=5, replace=False)
            duplicates = faulty.iloc[dup_idx]
            faulty = pd.concat([faulty, duplicates], ignore_index=True)

        elif fault_type == "out_of_order":
            faulty = faulty.sample(frac=1).reset_index(drop=True)

        elif fault_type == "crossed_books":
            if "ask" in faulty.columns and "bid" in faulty.columns:
                faulty["ask"], faulty["bid"] = faulty["bid"], faulty["ask"]

        elif fault_type == "stale_quotes":
            if "close" in faulty.columns:
                faulty["close"] = faulty["close"].shift(100)

        elif fault_type == "dst_shift":
            if "timestamp" in faulty.columns:
                faulty["timestamp"] = faulty["timestamp"] + pd.Timedelta(hours=3)

        return faulty

    @property
    def all_passed(self) -> bool:
        """Return True if all reports passed."""
        return all(r.passed for r in self._reports)

    @property
    def reports(self) -> list[QualityReport]:
        return list(self._reports)
