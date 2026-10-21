"""Structured logging for the Quant Research Lab.

All logs include environment labels, timestamps (UTC), and structured fields.
Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

No secrets, private keys, or wallet passwords are logged.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pythonjsonlogger import jsonlogger


class EnvironmentLabel(str, Enum):
    HISTORICAL_REAL = "HISTORICAL_REAL"
    SIMULATED = "SIMULATED"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    MT5_DEMO = "MT5_DEMO"
    LIVE = "LIVE"


class ColoredFormatter(logging.Formatter):
    """Colored console output for development."""
    COLORS = {
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[41m", # Red background
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        message = super().format(record)
        return f"{color}{message}{self.COLORS['RESET']}"


def setup_logger(
    name: str = "quant_lab",
    level: str = "INFO",
    log_file: str | None = None,
    environment: str = "SIMULATED",
) -> logging.Logger:
    """Set up structured logging for the lab.

    Args:
        name: Logger name
        level: Log level
        log_file: Optional file path for log output
        environment: Environment label for all log records
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Prevent duplicate handlers
    if logger.handlers:
        logger.handlers.clear()

    formatter = ColoredFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        json_fmt = jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(environment)s",
        )
        file_handler.setFormatter(json_fmt)
        logger.addHandler(file_handler)

    # Log environment label
    logger.info(
        f"Logger initialized | environment={environment} | level={level}",
        extra={"environment": environment, "component": "logger"},
    )

    return logger


class AuditLogger:
    """Audit log for all security-critical events.

    Records: config changes, promotions, safety-gate events,
    environment switches, and any action that affects system state.

    NEVER logs secrets, private keys, or wallet passwords.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("quant_lab.audit")
        self._entries: list[dict[str, Any]] = []

    def log(
        self,
        event: str,
        action: str,
        details: dict[str, Any],
        actor: str = "system",
    ) -> None:
        """Log an audit event.

        Args:
            event: Event type (e.g., 'PROMOTION', 'CONFIG_CHANGE', 'KILL_SWITCH')
            action: What happened
            details: Event details (no secrets)
            actor: Who/what triggered it
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "action": action,
            "actor": actor,
            "details": {k: v for k, v in details.items() if k not in {
                "private_key", "seed_phrase", "password", "api_secret"
            }},
        }
        self._entries.append(entry)
        self.logger.info(
            f"AUDIT: {event} | {action} | actor={actor}",
            extra={"audit": entry},
        )

    def get_entries(self) -> list[dict[str, Any]]:
        """Get all audit entries."""
        return list(self._entries)

    def save_to_file(self, path: str) -> None:
        """Save audit log to file."""
        import json
        with open(path, "w") as f:
            json.dump(self._entries, f, indent=2, default=str)
