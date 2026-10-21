"""Typed configuration loader for the Quant Research Lab.

Loads configuration from:
1. Environment variables (highest priority)
2. .env file
3. config.yaml (lowest priority)

All secrets must come from environment variables or .env files.
Never from config.yaml.

Environment labels (A2):
HISTORICAL_REAL | SIMULATED | BACKTEST | PAPER | MT5_DEMO | LIVE

The app refuses to start if environment is LIVE or any live-mode setting
is present in the config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class Environment(str, Enum):
    """Valid environment labels per AGENTS.md A2."""
    HISTORICAL_REAL = "HISTORICAL_REAL"
    SIMULATED = "SIMULATED"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    MT5_DEMO = "MT5_DEMO"
    LIVE = "LIVE"

    @classmethod
    def is_live(cls, env: str) -> bool:
        """Return True if the environment represents live trading."""
        return env in {cls.LIVE, "live", "LIVE"}


@dataclass
class GPUConfig:
    max_temperature_c: float = 85.0
    max_power_w: float = 200.0
    mining_priority: bool = False


@dataclass
class WalletConfig:
    address: str = ""


@dataclass
class MiningConfig:
    coin: str = ""
    algorithm: str = ""
    pool: str = ""
    port: int = 0


@dataclass
class MT5Config:
    terminal_path: str = ""
    server: str = ""
    login: int = 0
    # password NEVER stored here


@dataclass
class ExchangeConfig:
    default_data_source: str = "ccxt"
    rate_limit_ms: int = 1000


@dataclass
class DatabaseConfig:
    engine: str = "duckdb"
    path: str = "data/lab.duckdb"
    parquet_cache: str = "data/historical/"


@dataclass
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    db: int = 0


@dataclass
class RiskConfig:
    max_position_size: float = 1.0
    max_portfolio_exposure: float = 0.10
    max_daily_loss: float = 0.05
    max_drawdown: float = 0.20
    max_spread_points: float = 50.0
    max_slippage_bps: float = 10.0


@dataclass
class PromotionGates:
    min_oos_trades: int = 100
    min_oos_windows: int = 4
    oos_expectancy_after_costs: str = "> 0 with 95% bootstrap CI lower bound > 0"
    psr_or_dsr_min: float = 0.95
    pbo_max: float = 0.30
    cost_stress_multiplier: float = 1.5
    param_perturbation: str = "+/-20% keeps >= 70% of net performance"
    regime_coverage: str = "no single regime contributes > 60% of total net PnL"
    shadow_min_duration_days: int = 28
    demo_min_duration_days: int = 56
    demo_min_trades: int = 100
    demo_vs_backtest_slippage_tolerance: float = 0.25
    demo_max_drawdown_vs_backtest_expected: float = 1.5


@dataclass
class LabConfig:
    """Main configuration for the Quant Research Lab."""
    environment: str = "SIMULATED"
    gpu: GPUConfig = field(default_factory=GPUConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    mining: MiningConfig = field(default_factory=MiningConfig)
    mt5: MT5Config = field(default_factory=MT5Config)
    exchanges: ExchangeConfig = field(default_factory=ExchangeConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    promotion_gates: PromotionGates = field(default_factory=PromotionGates)

    def __post_init__(self) -> None:
        """Validate environment on initialization."""
        if Environment.is_live(self.environment):
            raise RuntimeError(
                f"LIVE environment detected: '{self.environment}'. "
                "This lab is configured for demo/paper execution only. "
                "No live-trading code path exists. "
                "Set environment='MT5_DEMO' or 'SIMULATED' to proceed."
            )
        # Validate environment label
        valid_envs = [e.value for e in Environment]
        if self.environment not in valid_envs:
            raise ValueError(
                f"Invalid environment '{self.environment}'. "
                f"Must be one of: {valid_envs}"
            )


def load_env_file(path: Path) -> dict[str, str]:
    """Load variables from a .env file."""
    env_vars: dict[str, str] = {}
    if not path.exists():
        return env_vars
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


def load_config(config_path: Path | None = None) -> LabConfig:
    """Load configuration from YAML and environment variables.

    Priority: env vars > .env file > config.yaml
    """
    # Find config path
    if config_path is None:
        config_path = Path("configs/config.yaml")
        if not config_path.exists():
            config_path = Path("config.yaml")
            if not config_path.exists():
                return LabConfig()  # Default config

    # Load YAML
    config_dict: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            config_dict = yaml.safe_load(f) or {}

    # Load .env file
    env_file = Path(".env")
    if not env_file.exists():
        env_file = Path("configs/.env")
    env_vars = load_env_file(env_file) if env_file.exists() else {}

    # Merge: env vars override YAML
    merged: dict[str, Any] = {**config_dict, **env_vars}

    # Build config with type coercion
    def _get(path_parts: list[str], default: Any) -> Any:
        current = merged
        for part in path_parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    # Also check actual environment variables (highest priority)
    env_override = os.environ.get("LAB_ENVIRONMENT", "")
    if env_override:
        merged["environment"] = env_override

    return LabConfig(
        environment=merged.get("environment", "SIMULATED"),
        gpu=GPUConfig(
            max_temperature_c=float(_get(["gpu", "max_temperature_c"], 85.0)),
            max_power_w=float(_get(["gpu", "max_power_w"], 200.0)),
            mining_priority=bool(_get(["gpu", "mining_priority"], False)),
        ),
        wallet=WalletConfig(address=str(_get(["wallet", "address"], ""))),
        mining=MiningConfig(
            coin=str(_get(["mining", "coin"], "")),
            algorithm=str(_get(["mining", "algorithm"], "")),
            pool=str(_get(["mining", "pool"], "")),
            port=int(_get(["mining", "port"], 0)),
        ),
        mt5=MT5Config(
            terminal_path=str(_get(["mt5", "terminal_path"], "")),
            server=str(_get(["mt5", "server"], "")),
            login=int(_get(["mt5", "login"], 0)),
        ),
        exchanges=ExchangeConfig(
            default_data_source=str(_get(["exchanges", "default_data_source"], "ccxt")),
            rate_limit_ms=int(_get(["exchanges", "rate_limit_ms"], 1000)),
        ),
        database=DatabaseConfig(
            engine=str(_get(["database", "engine"], "duckdb")),
            path=str(_get(["database", "path"], "data/lab.duckdb")),
            parquet_cache=str(_get(["database", "parquet_cache"], "data/historical/")),
        ),
        redis=RedisConfig(
            host=str(_get(["redis", "host"], "localhost")),
            port=int(_get(["redis", "port"], 6379)),
            db=int(_get(["redis", "db"], 0)),
        ),
        risk=RiskConfig(
            max_position_size=float(_get(["risk", "max_position_size"], 1.0)),
            max_portfolio_exposure=float(_get(["risk", "max_portfolio_exposure"], 0.10)),
            max_daily_loss=float(_get(["risk", "max_daily_loss"], 0.05)),
            max_drawdown=float(_get(["risk", "max_drawdown"], 0.20)),
            max_spread_points=float(_get(["risk", "max_spread_points"], 50.0)),
            max_slippage_bps=float(_get(["risk", "max_slippage_bps"], 10.0)),
        ),
        promotion_gates=PromotionGates(
            min_oos_trades=int(_get(["promotion_gates", "min_oos_trades"], 100)),
            min_oos_windows=int(_get(["promotion_gates", "min_oos_windows"], 4)),
            psr_or_dsr_min=float(_get(["promotion_gates", "psr_or_dsr_min"], 0.95)),
            pbo_max=float(_get(["promotion_gates", "pbo_max"], 0.30)),
            cost_stress_multiplier=float(_get(["promotion_gates", "cost_stress_multiplier"], 1.5)),
            demo_min_trades=int(_get(["promotion_gates", "demo_min_trades"], 100)),
            demo_min_duration_days=int(_get(["promotion_gates", "demo_min_duration_days"], 56)),
        ),
    )


def validate_config(config: LabConfig) -> list[str]:
    """Validate configuration and return list of warnings."""
    warnings: list[str] = []

    if not config.wallet.address:
        warnings.append("Wallet address is empty. Required for mining operations.")
    if config.mt5.login == 0:
        warnings.append("MT5 login is 0. Set via MT5 terminal or .env file.")
    if config.environment == "SIMULATED" and not config.mt5.server:
        warnings.append("Running in SIMULATED mode. No real data sources configured.")
    if config.risk.max_daily_loss <= 0:
        warnings.append("max_daily_loss must be positive.")
    if config.risk.max_drawdown <= 0:
        warnings.append("max_drawdown must be positive.")

    return warnings
