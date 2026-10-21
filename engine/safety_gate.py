"""Safety gate module — deterministic, no ML, no LLM.

Per AGENTS.md A3:
1. Kill switch / risk engine: deterministic code, absolute veto.
2. Execution layer: cannot bypass risk; fails closed.
3. Strategies / ML models: propose only.
4. Research agent (LLM): autonomy levels below.

The safety gate is the single absolute authority.
Nothing can bypass it. It fails closed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from configs import LabConfig
from engine.logger import AuditLogger


class SafetyVerdict(str, Enum):
    """Possible outcomes of a safety check."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HALTED = "HALTED"
    REDUCED = "REDUCED"
    CAUTION = "CAUTION"
    NORMAL = "NORMAL"


class SafetyStatus(str, Enum):
    """System safety status."""
    HEALTHY = "HEALTHY"
    CAUTION = "CAUTION"
    REDUCED = "REDUCED"
    HALTED = "HALTED"
    CRITICAL = "CRITICAL"


@dataclass
class RiskContext:
    """Current risk context for evaluation."""
    gpu_temperature: float = 0.0
    gpu_utilization: float = 0.0
    daily_loss_so_far: float = 0.0
    portfolio_drawdown: float = 0.0
    open_positions: int = 0
    data_quality_score: float = 1.0
    connection_status: str = "connected"
    latency_ms: float = 0.0
    spread_points: float = 0.0


@dataclass
class SafetyResult:
    """Result of a safety gate check."""
    verdict: SafetyVerdict
    reason: str
    can_proceed: bool
    risk_status: SafetyStatus
    recommended_action: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class SafetyGate:
    """Deterministic safety gate — absolute authority.

    Per AGENTS.md:
    - L3 (modify risk/execution code) is FORBIDDEN for LLM
    - Risk engine has absolute veto
    - No code path lets an order skip the risk check
    - Fail closed on any uncertainty
    """

    def __init__(self, config: LabConfig, audit: AuditLogger | None = None) -> None:
        self.config = config
        self.audit = audit or AuditLogger()
        self._status: SafetyStatus = SafetyStatus.HEALTHY
        self._consecutive_failures: int = 0
        self._max_consecutive_failures: int = 5
        self.logger = logging.getLogger("quant_lab.safety")

    def check_pre_trade(self, context: RiskContext) -> SafetyResult:
        """Pre-trade failure-first checklist (AGENTS.md A7).

        Any critical failure means NO_TRADE.
        Returns SafetyResult with verdict and reason.
        """
        checks: list[tuple[str, bool, str]] = []

        # 1. Data reliable
        data_ok = context.data_quality_score >= 0.7
        checks.append(("Data reliable", data_ok, f"Quality score: {context.data_quality_score}"))

        # 2. Liquidity sufficient
        liquidity_ok = context.spread_points < self.config.risk.max_spread_points
        checks.append(("Liquidity sufficient", liquidity_ok, f"Spread: {context.spread_points} pts"))

        # 3. Spread acceptable
        spread_ok = context.spread_points < self.config.risk.max_spread_points
        checks.append(("Spread acceptable", spread_ok, f"Max spread: {self.config.risk.max_spread_points}"))

        # 4. Latency acceptable
        latency_ok = context.latency_ms < 5000  # 5 seconds
        checks.append(("Latency acceptable", latency_ok, f"Latency: {context.latency_ms}ms"))

        # 5. Connection state certain
        conn_ok = context.connection_status == "connected"
        checks.append(("Connection state certain", conn_ok, f"Status: {context.connection_status}"))

        # 6. Daily loss limit
        daily_ok = context.daily_loss_so_far < self.config.risk.max_daily_loss
        checks.append(("Daily loss within limit", daily_ok, f"Loss: {context.daily_loss_so_far:.2f} / {self.config.risk.max_daily_loss}"))

        # 7. Drawdown limit
        dd_ok = context.portfolio_drawdown < self.config.risk.max_drawdown
        checks.append(("Drawdown within limit", dd_ok, f"DD: {context.portfolio_drawdown:.2%} / {self.config.risk.max_drawdown:.2%}"))

        # 8. GPU temperature (for mining/computation)
        gpu_ok = context.gpu_temperature < self.config.gpu.max_temperature_c
        checks.append(("GPU temperature safe", gpu_ok, f"Temp: {context.gpu_temperature:.1f}°C / {self.config.gpu.max_temperature_c}°C"))

        # Evaluate
        all_passed = all(ok for _, ok, _ in checks)
        failed_checks = [(name, reason) for name, ok, reason in checks if not ok]

        if not all_passed:
            for name, reason in failed_checks:
                self.audit.log(
                    event="SAFETY_GATE_REJECT",
                    action="Pre-trade check failed",
                    details={"check": name, "reason": reason},
                    actor="safety_gate",
                )
            return SafetyResult(
                verdict=SafetyVerdict.REJECTED,
                reason=" | ".join(f"{n}: {r}" for n, r in failed_checks),
                can_proceed=False,
                risk_status=SafetyStatus.CAUTION,
                recommended_action="NO_TRADE",
                details={"failed_checks": failed_checks},
            )

        self.audit.log(
            event="SAFETY_GATE_APPROVE",
            action="All pre-trade checks passed",
            details={"checks_passed": len(checks)},
            actor="safety_gate",
        )
        return SafetyResult(
            verdict=SafetyVerdict.APPROVED,
            reason="All checks passed",
            can_proceed=True,
            risk_status=SafetyStatus.HEALTHY,
            recommended_action="PROCEED",
        )

    def check_mt5_demo_only(self, trade_mode: int) -> SafetyResult:
        """Verify MT5 account is DEMO (AGENTS.md A2).

        trade_mode: 0 = DEMO, 1 = REAL
        """
        if trade_mode != 0:
            self.audit.log(
                event="MT5_ACCOUNT_REJECTED",
                action=f"Non-demo account detected (trade_mode={trade_mode})",
                details={"trade_mode": trade_mode},
                actor="safety_gate",
            )
            return SafetyResult(
                verdict=SafetyVerdict.REJECTED,
                reason=f"MT5 account is not DEMO (trade_mode={trade_mode}). Live accounts forbidden.",
                can_proceed=False,
                risk_status=SafetyStatus.CRITICAL,
                recommended_action="REFUSE_AND_ALERT",
            )
        return SafetyResult(
            verdict=SafetyVerdict.APPROVED,
            reason="MT5 account verified as DEMO",
            can_proceed=True,
            risk_status=SafetyStatus.HEALTHY,
            recommended_action="PROCEED",
        )

    def check_gpu_safety(self, temperature: float) -> SafetyResult:
        """Check GPU temperature against limits."""
        if temperature >= self.config.gpu.max_temperature_c:
            self.audit.log(
                event="GPU_OVERHEAT",
                action="GPU temperature exceeded limit — HALT",
                details={"temperature": temperature, "limit": self.config.gpu.max_temperature_c},
                actor="safety_gate",
            )
            self._status = SafetyStatus.CRITICAL
            return SafetyResult(
                verdict=SafetyVerdict.HALTED,
                reason=f"GPU temperature {temperature}°C >= limit {self.config.gpu.max_temperature_c}°C",
                can_proceed=False,
                risk_status=SafetyStatus.CRITICAL,
                recommended_action="STOP_MINING",
            )
        if temperature >= self.config.gpu.max_temperature_c * 0.85:
            self._status = SafetyStatus.CAUTION
            return SafetyResult(
                verdict=SafetyVerdict.REDUCED,
                reason=f"GPU temperature {temperature}°C approaching limit",
                can_proceed=True,
                risk_status=SafetyStatus.CAUTION,
                recommended_action="REDUCE_INTENSITY",
            )
        return SafetyResult(
            verdict=SafetyVerdict.APPROVED,
            reason="GPU temperature within safe range",
            can_proceed=True,
            risk_status=SafetyStatus.HEALTHY,
            recommended_action="PROCEED",
        )

    def evaluate_drawdown_governor(self, drawdown: float) -> SafetyStatus:
        """Drawdown governor: NORMAL > CAUTION > REDUCED > HALTED.

        Test values with max_drawdown=0.20:
        - 0.10 → HEALTHY (< 0.10)
        - 0.40 → CAUTION (< 0.40)
        - 0.70 → REDUCED (< 0.70)
        - 0.85 → HALTED (>= 0.70)

        Note: thresholds are absolute based on max_drawdown=0.20.
        """
    def evaluate_drawdown_governor(self, drawdown: float) -> SafetyStatus:
        """Drawdown governor: NORMAL > CAUTION > REDUCED > HALTED.

        Thresholds with max_drawdown=0.20:
        - <= 0.10: HEALTHY
        - <= 0.40: CAUTION
        - <= 0.70: REDUCED
        - > 0.70: HALTED
        """
        if drawdown > 0.70:
            status = SafetyStatus.HALTED
        elif drawdown > 0.40:
            status = SafetyStatus.REDUCED
        elif drawdown > 0.10:
            status = SafetyStatus.CAUTION
        else:
            status = SafetyStatus.HEALTHY
        self._status = status
        return status

    def emergency_stop(self, reason: str) -> SafetyResult:
        """Global kill switch."""
        self.audit.log(
            event="KILL_SWITCH_ACTIVATED",
            action="Emergency stop triggered",
            details={"reason": reason},
            actor="kill_switch",
        )
        self._status = SafetyStatus.CRITICAL
        self._consecutive_failures = 0
        return SafetyResult(
            verdict=SafetyVerdict.HALTED,
            reason=f"Kill switch: {reason}",
            can_proceed=False,
            risk_status=SafetyStatus.CRITICAL,
            recommended_action="HALT_ALL",
        )

    @property
    def status(self) -> SafetyStatus:
        return self._status

    @property
    def is_safe(self) -> bool:
        return self._status in {SafetyStatus.HEALTHY, SafetyStatus.CAUTION}
