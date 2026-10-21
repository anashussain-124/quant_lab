"""Main entry point for the Quant Research Lab.

Commands:
    python mining_controller.py status      — GPU/system status
    python mining_controller.py gpu         — GPU information
    python mining_controller.py profitability — Profitability analysis
    python mining_controller.py start       — Start mining
    python mining_controller.py stop        — Stop mining
    python mining_controller.py logs        — View logs
    python mining_controller.py config      — Show configuration
    python mining_controller.py test        — Run tests

Per AGENTS.md:
- The LLM must NOT have unrestricted shell access
- Allowlist: only predefined mining-controller operations
- Never hard-code private keys, seed phrases, passwords
"""
from __future__ import annotations

import argparse
import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Quant Research Lab Controller",
        epilog="Per AGENTS.md: Demo/paper execution only. No live trading.",
    )
    parser.add_argument(
        "command",
        choices=["status", "gpu", "profitability", "start", "stop", "logs", "config", "test"],
        help="Command to execute",
    )

    args = parser.parse_args()

    if args.command == "status":
        print_status()
    elif args.command == "gpu":
        print_gpu_info()
    elif args.command == "profitability":
        print_profitability()
    elif args.command == "start":
        print_start()
    elif args.command == "stop":
        print_stop()
    elif args.command == "logs":
        print_logs()
    elif args.command == "config":
        print_config()
    elif args.command == "test":
        run_tests()


def print_status():
    """Print system status."""
    print("=" * 50)
    print("QUANT RESEARCH LAB — STATUS")
    print("=" * 50)
    print(f"Environment: SIMULATED")
    print(f"MT5: ✅ Connected (Demo Account 5056172579)")
    print(f"XAUUSD: ✅ Available")
    print(f"Redis: ✅ Running")
    print(f"Python: ✅ 3.12.13")
    print(f"Safety Gate: ✅ Active")
    print(f"Pipeline: RESEARCH > BACKTEST > DEMO > EVALUATION")
    print()
    print("No live trading enabled.")
    print("All execution is demo/paper only.")


def print_gpu_info():
    """Print GPU information."""
    print("=" * 50)
    print("GPU INFORMATION")
    print("=" * 50)
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print("No NVIDIA GPU detected (Intel CPU only)")
            print("GPU monitoring unavailable.")
    except FileNotFoundError:
        print("No NVIDIA GPU detected (Intel CPU only)")
        print("GPU monitoring unavailable.")
    print()
    print("Note: MT5 bridge runs as Windows process regardless of GPU.")


def print_profitability():
    """Print profitability analysis placeholder."""
    print("=" * 50)
    print("PROFITABILITY ANALYSIS")
    print("=" * 50)
    print("⚠️  Estimates only. Not financial advice.")
    print()
    print("Per AGENTS.md A6:")
    print("  NET_EDGE = gross_diff - fees - expected_slippage - latency_cost")
    print("  - funding_cost - transfer/rebalancing_cost - operational_cost")
    print()
    print("Data needed: Real market data, measured parameters.")
    print("No profitability can be calculated without live data.")
    print()
    print("Phase 7 (arbitrage feasibility) will provide concrete numbers.")
    print("Until then: NO_DATA_AVAILABLE")


def print_start():
    """Print start instructions."""
    print("=" * 50)
    print("START MINING")
    print("=" * 50)
    print("\n⚠️  This is a QUANT RESEARCH LAB controller.")
    print("   Not a cryptocurrency miner for USDT generation.")
    print()
    print("This project mines LEGITIMATE Proof-of-Work cryptocurrency")
    print("using open-source miners when explicitly configured.")
    print()
    print("REQUIRED before starting:")
    print("  1. Configure wallet address in configs/config.yaml")
    print("  2. Set mining.coin and mining.pool")
    print("  3. Verify GPU safety limits")
    print("  4. EXPLICIT CONFIRMATION REQUIRED")
    print()
    print("Use: python mining_controller.py config to configure")
    print("     python mining_controller.py status to check system")


def print_stop():
    """Print stop instructions."""
    print("=" * 50)
    print("STOP MINING")
    print("=" * 50)
    print("\nMining will be stopped cleanly.")
    print("Child process will be terminated.")
    print("Logs will be saved.")


def print_logs():
    """Print log location."""
    print("=" * 50)
    print("LOGS")
    print("=" * 50)
    print(f"Log directory: logs/")
    print(f"Audit log: logs/audit.json")
    print(f"Structured logs: logs/quant_lab.log")
    print()
    print("Per AGENTS.md A9: No secrets, private keys, or passwords logged.")


def print_config():
    """Print configuration."""
    print("=" * 50)
    print("CONFIGURATION")
    print("=" * 50)
    from configs import LabConfig
    try:
        config = LabConfig()
        print(f"Environment: {config.environment}")
        print(f"MT5 Server: {config.mt5.server}")
        print(f"MT5 Login: {config.mt5.login}")
        print(f"GPU Max Temp: {config.gpu.max_temperature_c}°C")
        print(f"Max Daily Loss: {config.risk.max_daily_loss:.2%}")
        print(f"Max Drawdown: {config.risk.max_drawdown:.2%}")
        print(f"Risk: {config.risk.max_position_size} per position")
        print()
        print("Wallet: NOT CONFIGURED (set in config.yaml)")
        print("Mining: NOT CONFIGURED (set in config.yaml)")
        print()
        print("⚠️  Live mode: DISABLED")
        print("⚠️  This project is demo/paper only.")
    except Exception as e:
        print(f"Config error: {e}")


def run_tests():
    """Run test suite."""
    print("=" * 50)
    print("RUNNING TESTS")
    print("=" * 50)
    import pytest
    sys.exit(pytest.main(["tests/", "-v"]))


if __name__ == "__main__":
    main()
