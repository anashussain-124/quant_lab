"""Phase 10: Monitoring Dashboard — Metrics, Health, Alerts.

Per AGENTS.md A4:
- Every record, log line, metric, and dashboard panel carries an environment label
- Data quality score (0-1) per dataset/stream
- Below threshold means NO_TRADE

Provides:
- Metrics collector for all system components
- Health checker with GREEN/YELLOW/ORANGE/RED/CRITICAL levels
- Alert manager with deduplication
- Dashboard state snapshot
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np

from engine.logger import AuditLogger


class AlertLevel(str, Enum):
    """Alert severity levels."""
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"
    CRITICAL = "CRITICAL"


@dataclass
class MetricSample:
    """A single metric sample."""
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "labels": self.labels,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Alert:
    """System alert."""
    level: AlertLevel
    component: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "component": self.component,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged,
        }


@dataclass
class DashboardSnapshot:
    """System health snapshot for dashboard rendering."""
    system_health: AlertLevel = AlertLevel.GREEN
    environment: str = "SIMULATED"
    uptime_seconds: float = 0.0
    active_alerts: int = 0
    critical_alerts: int = 0
    latest_metrics: dict[str, float] = field(default_factory=dict)
    components: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_health": self.system_health.value,
            "environment": self.environment,
            "uptime_seconds": self.uptime_seconds,
            "active_alerts": self.active_alerts,
            "critical_alerts": self.critical_alerts,
            "latest_metrics": self.latest_metrics,
            "components": self.components,
            "timestamp": self.timestamp.isoformat(),
        }


class MetricsCollector:
    """Collects and stores metric samples."""

    def __init__(self, max_history: int = 10000) -> None:
        self._metrics: dict[str, list[MetricSample]] = {}
        self._max_history = max_history

    def record(self, name: str, value: float, labels: dict[str, str] | None = None) -> MetricSample:
        """Record a metric sample."""
        sample = MetricSample(
            name=name,
            value=float(value),
            labels=labels or {},
        )
        if name not in self._metrics:
            self._metrics[name] = []
        self._metrics[name].append(sample)
        # Trim history
        if len(self._metrics[name]) > self._max_history:
            self._metrics[name] = self._metrics[name][-self._max_history:]
        return sample

    def get_latest(self, name: str) -> MetricSample | None:
        """Get latest sample for a metric."""
        samples = self._metrics.get(name)
        return samples[-1] if samples else None

    def get_history(self, name: str, last_n: int = 100) -> list[MetricSample]:
        """Get history for a metric."""
        samples = self._metrics.get(name, [])
        return samples[-last_n:]

    def get_latest_values(self) -> dict[str, float]:
        """Get latest value for all metrics."""
        result = {}
        for name, samples in self._metrics.items():
            if samples:
                result[name] = samples[-1].value
        return result

    def get_all_names(self) -> list[str]:
        """Get all metric names."""
        return list(self._metrics.keys())


class AlertManager:
    """Manages system alerts."""

    def __init__(self, max_alerts: int = 1000) -> None:
        self._alerts: list[Alert] = []
        self._max_alerts = max_alerts
        self._suppressed: set[str] = set()

    def trigger(
        self,
        level: AlertLevel,
        component: str,
        message: str,
        details: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> Alert | None:
        """Trigger an alert."""
        # Deduplication
        if dedup_key and dedup_key in self._suppressed:
            return None

        alert = Alert(
            level=level,
            component=component,
            message=message,
            details=details or {},
        )
        self._alerts.append(alert)

        # Suppress future duplicates
        if dedup_key:
            self._suppressed.add(dedup_key)

        # Trim history
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

        return alert

    def acknowledge(self, index: int) -> None:
        """Acknowledge an alert."""
        if 0 <= index < len(self._alerts):
            self._alerts[index].acknowledged = True

    def acknowledge_all(self) -> None:
        """Acknowledge all alerts."""
        for alert in self._alerts:
            alert.acknowledged = True

    def clear(self) -> None:
        """Clear all alerts."""
        self._alerts.clear()
        self._suppressed.clear()

    def get_active(self) -> list[Alert]:
        """Get unacknowledged alerts."""
        return [a for a in self._alerts if not a.acknowledged]

    def get_all(self) -> list[Alert]:
        """Get all alerts."""
        return list(self._alerts)

    def get_by_level(self, level: AlertLevel) -> list[Alert]:
        """Get alerts by level."""
        return [a for a in self._alerts if a.level == level]

    def has_critical(self) -> bool:
        """Check if any critical alerts exist."""
        return any(
            a.level in [AlertLevel.CRITICAL, AlertLevel.RED] and not a.acknowledged
            for a in self._alerts
        )


class HealthChecker:
    """Checks component health and determines system status."""

    def __init__(self) -> None:
        self._checks: dict[str, callable] = {}

    def register_check(self, name: str, check_fn: callable) -> None:
        """Register a health check function."""
        self._checks[name] = check_fn

    def check(self, name: str) -> tuple[bool, str]:
        """Run a health check."""
        fn = self._checks.get(name)
        if not fn:
            return False, f"Unknown check: {name}"
        try:
            return fn()
        except Exception as e:
            return False, f"Check failed: {e}"

    def check_all(self) -> dict[str, tuple[bool, str]]:
        """Run all health checks."""
        return {name: self.check(name) for name in self._checks}

    def get_overall_health(self) -> AlertLevel:
        """Get overall system health."""
        results = self.check_all()
        if not results:
            return AlertLevel.GREEN

        failed = [r for r in results.values() if not r[0]]
        if any("CRITICAL" in r[1] for r in failed):
            return AlertLevel.CRITICAL
        fail_rate = len(failed) / len(results)
        if fail_rate >= 0.75:
            return AlertLevel.RED
        if fail_rate >= 0.5:
            return AlertLevel.ORANGE
        if fail_rate > 0:
            return AlertLevel.YELLOW
        return AlertLevel.GREEN


class Dashboard:
    """Monitoring dashboard — aggregates metrics, alerts, and health."""

    def __init__(
        self,
        metrics: MetricsCollector | None = None,
        alerts: AlertManager | None = None,
        health: HealthChecker | None = None,
        audit: AuditLogger | None = None,
        environment: str = "SIMULATED",
    ) -> None:
        self.metrics = metrics or MetricsCollector()
        self.alerts = alerts or AlertManager()
        self.health = health or HealthChecker()
        self.audit = audit or AuditLogger()
        self.environment = environment
        self._start_time = time.time()

    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> MetricSample:
        """Record a metric."""
        return self.metrics.record(name, value, labels)

    def trigger_alert(
        self,
        level: AlertLevel,
        component: str,
        message: str,
        details: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> Alert | None:
        """Trigger an alert."""
        alert = self.alerts.trigger(level, component, message, details, dedup_key)
        if alert:
            self.audit.log(
                event="ALERT",
                action=f"{level.value}: {component} - {message}",
                details=alert.to_dict(),
                actor="dashboard",
            )
        return alert

    def register_health_check(self, name: str, check_fn: callable) -> None:
        """Register a health check."""
        self.health.register_check(name, check_fn)

    def get_snapshot(self) -> DashboardSnapshot:
        """Get dashboard snapshot."""
        active = self.alerts.get_active()
        critical = [a for a in active if a.level in [AlertLevel.CRITICAL, AlertLevel.RED]]
        health_results = self.health.check_all()
        components = {name: "UP" if ok else "DOWN" for name, (ok, _) in health_results.items()}

        return DashboardSnapshot(
            system_health=self.health.get_overall_health(),
            environment=self.environment,
            uptime_seconds=time.time() - self._start_time,
            active_alerts=len(active),
            critical_alerts=len(critical),
            latest_metrics=self.metrics.get_latest_values(),
            components=components,
        )

    def get_metrics_summary(self) -> dict[str, Any]:
        """Get metrics summary."""
        return {
            "total_metrics": len(self.metrics.get_all_names()),
            "latest_values": self.metrics.get_latest_values(),
        }

    def get_alerts_summary(self) -> dict[str, Any]:
        """Get alerts summary."""
        all_alerts = self.alerts.get_all()
        active = self.alerts.get_active()
        return {
            "total_alerts": len(all_alerts),
            "active_alerts": len(active),
            "critical_alerts": len([a for a in active if a.level in [AlertLevel.CRITICAL, AlertLevel.RED]]),
            "by_level": {
                level.value: len([a for a in all_alerts if a.level == level])
                for level in AlertLevel
            },
        }


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
