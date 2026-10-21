"""Phase 10 tests — Monitoring Dashboard.

Per AGENTS.md A4:
- Every record, log line, metric, and dashboard panel carries an environment label
- Data quality score (0-1) per dataset/stream
- Below threshold means NO_TRADE
"""
from __future__ import annotations

import pytest
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from engine.dashboard import (
    MetricsCollector, AlertManager, HealthChecker, Dashboard,
    MetricSample, Alert, AlertLevel, DashboardSnapshot,
)


class TestMetricsCollector:
    """Test metrics collection."""

    def test_record_metric(self):
        """Should record a metric."""
        collector = MetricsCollector()
        sample = collector.record("test_metric", 42.0)
        assert sample.name == "test_metric"
        assert sample.value == 42.0

    def test_get_latest(self):
        """Should get latest sample."""
        collector = MetricsCollector()
        collector.record("test", 1.0)
        collector.record("test", 2.0)
        latest = collector.get_latest("test")
        assert latest is not None
        assert latest.value == 2.0

    def test_get_latest_missing(self):
        """Should return None for missing metric."""
        collector = MetricsCollector()
        assert collector.get_latest("nonexistent") is None

    def test_get_history(self):
        """Should get history."""
        collector = MetricsCollector()
        for i in range(5):
            collector.record("test", float(i))
        history = collector.get_history("test")
        assert len(history) == 5
        assert history[-1].value == 4.0

    def test_get_latest_values(self):
        """Should get latest values for all metrics."""
        collector = MetricsCollector()
        collector.record("a", 1.0)
        collector.record("b", 2.0)
        values = collector.get_latest_values()
        assert values == {"a": 1.0, "b": 2.0}

    def test_max_history(self):
        """Should trim history."""
        collector = MetricsCollector(max_history=5)
        for i in range(10):
            collector.record("test", float(i))
        history = collector.get_history("test")
        assert len(history) == 5
        assert history[0].value == 5.0

    def test_labels(self):
        """Should store labels."""
        collector = MetricsCollector()
        sample = collector.record("test", 1.0, {"env": "SIMULATED"})
        assert sample.labels["env"] == "SIMULATED"


class TestAlertManager:
    """Test alert management."""

    def test_trigger_alert(self):
        """Should trigger an alert."""
        manager = AlertManager()
        alert = manager.trigger(AlertLevel.RED, "test", "Test alert")
        assert alert is not None
        assert alert.level == AlertLevel.RED
        assert alert.component == "test"

    def test_deduplication(self):
        """Should deduplicate alerts."""
        manager = AlertManager()
        a1 = manager.trigger(AlertLevel.RED, "test", "Alert", dedup_key="dup1")
        a2 = manager.trigger(AlertLevel.RED, "test", "Alert", dedup_key="dup1")
        assert a1 is not None
        assert a2 is None

    def test_acknowledge(self):
        """Should acknowledge alert."""
        manager = AlertManager()
        manager.trigger(AlertLevel.RED, "test", "Alert")
        assert len(manager.get_active()) == 1
        manager.acknowledge(0)
        assert len(manager.get_active()) == 0

    def test_acknowledge_all(self):
        """Should acknowledge all alerts."""
        manager = AlertManager()
        manager.trigger(AlertLevel.RED, "test", "Alert1")
        manager.trigger(AlertLevel.YELLOW, "test", "Alert2")
        manager.acknowledge_all()
        assert len(manager.get_active()) == 0

    def test_get_by_level(self):
        """Should filter by level."""
        manager = AlertManager()
        manager.trigger(AlertLevel.RED, "test", "Alert1")
        manager.trigger(AlertLevel.YELLOW, "test", "Alert2")
        manager.trigger(AlertLevel.RED, "test", "Alert3")
        red_alerts = manager.get_by_level(AlertLevel.RED)
        assert len(red_alerts) == 2

    def test_has_critical(self):
        """Should detect critical alerts."""
        manager = AlertManager()
        assert manager.has_critical() is False
        manager.trigger(AlertLevel.CRITICAL, "test", "Critical")
        assert manager.has_critical() is True

    def test_clear(self):
        """Should clear all alerts."""
        manager = AlertManager()
        manager.trigger(AlertLevel.RED, "test", "Alert")
        manager.clear()
        assert len(manager.get_all()) == 0

    def test_max_alerts(self):
        """Should trim alerts."""
        manager = AlertManager(max_alerts=5)
        for i in range(10):
            manager.trigger(AlertLevel.RED, "test", f"Alert {i}")
        assert len(manager.get_all()) == 5


class TestHealthChecker:
    """Test health checking."""

    def test_register_check(self):
        """Should register a check."""
        checker = HealthChecker()
        checker.register_check("test", lambda: (True, "OK"))
        result = checker.check("test")
        assert result == (True, "OK")

    def test_check_unknown(self):
        """Should handle unknown check."""
        checker = HealthChecker()
        result = checker.check("nonexistent")
        assert result[0] is False

    def test_check_exception(self):
        """Should handle check exception."""
        checker = HealthChecker()
        checker.register_check("bad", lambda: 1/0)
        result = checker.check("bad")
        assert result[0] is False

    def test_check_all(self):
        """Should run all checks."""
        checker = HealthChecker()
        checker.register_check("a", lambda: (True, "OK"))
        checker.register_check("b", lambda: (False, "FAIL"))
        results = checker.check_all()
        assert len(results) == 2
        assert results["a"] == (True, "OK")
        assert results["b"][0] is False

    def test_overall_health_green(self):
        """Should be GREEN when all pass."""
        checker = HealthChecker()
        checker.register_check("a", lambda: (True, "OK"))
        assert checker.get_overall_health() == AlertLevel.GREEN

    def test_overall_health_yellow(self):
        """Should be YELLOW when minority fail (25%)."""
        checker = HealthChecker()
        checker.register_check("a", lambda: (True, "OK"))
        checker.register_check("b", lambda: (True, "OK"))
        checker.register_check("c", lambda: (True, "OK"))
        checker.register_check("d", lambda: (False, "FAIL"))
        assert checker.get_overall_health() == AlertLevel.YELLOW

    def test_overall_health_red(self):
        """Should be RED when majority fail (75%)."""
        checker = HealthChecker()
        checker.register_check("a", lambda: (False, "FAIL"))
        checker.register_check("b", lambda: (False, "FAIL"))
        checker.register_check("c", lambda: (False, "FAIL"))
        checker.register_check("d", lambda: (True, "OK"))
        assert checker.get_overall_health() == AlertLevel.RED

    def test_overall_health_critical(self):
        """Should be CRITICAL when critical failure."""
        checker = HealthChecker()
        checker.register_check("a", lambda: (False, "CRITICAL failure"))
        assert checker.get_overall_health() == AlertLevel.CRITICAL


class TestDashboard:
    """Test dashboard."""

    def test_record_metric(self):
        """Should record metric."""
        dashboard = Dashboard()
        dashboard.record_metric("test", 42.0)
        latest = dashboard.metrics.get_latest("test")
        assert latest.value == 42.0

    def test_trigger_alert(self):
        """Should trigger alert."""
        dashboard = Dashboard()
        alert = dashboard.trigger_alert(AlertLevel.RED, "test", "Test")
        assert alert is not None
        assert alert.level == AlertLevel.RED

    def test_register_health_check(self):
        """Should register health check."""
        dashboard = Dashboard()
        dashboard.register_health_check("test", lambda: (True, "OK"))
        result = dashboard.health.check("test")
        assert result == (True, "OK")

    def test_get_snapshot(self):
        """Should get snapshot."""
        dashboard = Dashboard()
        dashboard.record_metric("test", 1.0)
        snapshot = dashboard.get_snapshot()
        assert isinstance(snapshot, DashboardSnapshot)
        assert snapshot.latest_metrics["test"] == 1.0

    def test_snapshot_environment(self):
        """Should have environment label."""
        dashboard = Dashboard(environment="MT5_DEMO")
        snapshot = dashboard.get_snapshot()
        assert snapshot.environment == "MT5_DEMO"

    def test_get_metrics_summary(self):
        """Should get metrics summary."""
        dashboard = Dashboard()
        dashboard.record_metric("a", 1.0)
        dashboard.record_metric("b", 2.0)
        summary = dashboard.get_metrics_summary()
        assert summary["total_metrics"] == 2

    def test_get_alerts_summary(self):
        """Should get alerts summary."""
        dashboard = Dashboard()
        dashboard.trigger_alert(AlertLevel.RED, "test", "Alert1")
        dashboard.trigger_alert(AlertLevel.YELLOW, "test", "Alert2")
        summary = dashboard.get_alerts_summary()
        assert summary["total_alerts"] == 2
        assert summary["active_alerts"] == 2

    def test_uptime(self):
        """Should track uptime."""
        dashboard = Dashboard()
        snapshot = dashboard.get_snapshot()
        assert snapshot.uptime_seconds >= 0


class TestIntegration:
    """Test integration."""

    def test_full_dashboard_flow(self):
        """Full dashboard flow."""
        dashboard = Dashboard(environment="SIMULATED")

        # Record metrics
        dashboard.record_metric("equity", 5000.0)
        dashboard.record_metric("daily_pnl", 100.0)
        dashboard.record_metric("data_quality", 0.95)

        # Register health checks
        dashboard.register_health_check("data_feed", lambda: (True, "OK"))
        dashboard.register_health_check("risk_engine", lambda: (True, "OK"))

        # Trigger alert
        dashboard.trigger_alert(AlertLevel.YELLOW, "test", "Test alert")

        # Get snapshot
        snapshot = dashboard.get_snapshot()
        # System health is GREEN (all health checks pass) even with an alert
        assert snapshot.system_health == AlertLevel.GREEN
        assert snapshot.active_alerts == 1
        assert snapshot.latest_metrics["equity"] == 5000.0

    def test_alert_dedup_in_dashboard(self):
        """Should deduplicate alerts."""
        dashboard = Dashboard()
        dashboard.trigger_alert(AlertLevel.RED, "test", "Alert", dedup_key="dup")
        dashboard.trigger_alert(AlertLevel.RED, "test", "Alert", dedup_key="dup")
        assert len(dashboard.alerts.get_all()) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
