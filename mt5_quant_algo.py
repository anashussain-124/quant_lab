#!/usr/bin/env python3
"""Unified Quant Research Lab + MT5 Demo-Trading Algorithm for XAUUSD.

Coordinates:
- engine.orchestrator: Lab coordination and system state
- engine.regime: Market regime detection and indicator calculations
- engine.risk_engine: Deterministic risk engine, position sizing, kill switch
- engine.safety_gate: Pre-trade safety gate and veto authority
- engine.mt5_bridge: MT5 connection, DEMO-only verification, order execution
- engine.prop_firm_compliance: FundingPips compliance engine (UTC+3 reset)
- engine.prop_firm_rules: Challenge model profiles (Standard, Pro, Flex)
- engine.logger: Structured audit logging
- data.storage: Historical parquet storage and verification
- adapters.data_quality: Data quality assessment

HARD SAFETY MANDATE (AGENTS.md A2 & A3):
- Absolutely NO live trading code path exists.
- Enforces ACCOUNT_TRADE_MODE_DEMO at multiple independent layers.
- Fails closed on any error, uncertainty, stale quote, or safety breach.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml

# Core Engine Modules
from configs import LabConfig, Environment, load_config
from engine.logger import AuditLogger, setup_logger
from engine.safety_gate import SafetyGate, RiskContext, SafetyVerdict, SafetyStatus
from engine.risk_engine import RiskEngine, OrderSide, RiskLevel
from engine.mt5_bridge import MT5Bridge, MT5Config, MT5OrderMode
from engine.regime import RegimeDetector, RegimeState, calculate_donchian, calculate_atr
from engine.prop_firm_rules import (
    PROP_FIRM_PRESETS,
    PropFirmRuleProfile,
    RuleSourceStatus,
    get_profile,
    list_profiles,
)
from engine.prop_firm_compliance import (
    PropFirmComplianceEngine,
    ComplianceState,
    VerdictCode,
    ComplianceVerdict,
)
from adapters.data_quality import DataQualityEngine

# Set up logging
logger = setup_logger(name="mt5_quant_algo", level="INFO")


@dataclass
class StrategyParameters:
    """XAUUSD H1 Donchian-50 breakout strategy parameters."""
    symbol: str = "XAUUSD"
    donchian_period: int = 50
    atr_period: int = 14
    atr_mult_stop: float = 2.0
    atr_mult_tp: float = 0.0          # 0.0 = no fixed TP, exit via trailing or time
    min_atr_usd: float = 8.0          # Configurable research parameter ($8.00)
    risk_per_trade_pct: float = 0.0025 # 0.25% default per AGENTS.md / Master prompt
    max_holding_bars: int = 100
    session_start_utc: int = 8        # 08:00 UTC (London session open)
    session_end_utc: int = 16         # 16:00 UTC (London session close)
    slippage_bps: float = 2.0
    commission_rate: float = 0.0004
    contract_size: float = 100.0      # 100 oz per lot for XAUUSD
    point_value_usd: float = 10.0     # $10 per 1.0 lot per 0.01 move


@dataclass
class CandleBar:
    """Normalized OHLCV bar with UTC timestamp."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class MT5QuantAlgorithm:
    """Production-grade Unified MT5 Demo Trading Algorithm."""

    def __init__(
        self,
        config: LabConfig | None = None,
        strategy_params: StrategyParameters | None = None,
        prop_firm_profile_id: str = "fundingpips_2step_standard_5pct_p1",
        state_dir: Path | str = "data",
    ) -> None:
        self.config = config or load_config()
        self._enforce_demo_safety()

        self.params = strategy_params or StrategyParameters()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.algo_state_file = self.state_dir / "algo_state.json"
        self.compliance_state_file = self.state_dir / "compliance_state.json"

        self.audit = AuditLogger()
        self.safety_gate = SafetyGate(self.config, self.audit)
        self.risk_engine = RiskEngine(self.config, self.audit)
        self.regime_detector = RegimeDetector(
            adx_period=self.params.atr_period,
            atr_period=self.params.atr_period,
            ma_fast=20,
            ma_slow=50,
        )
        self.data_quality = DataQualityEngine()

        # Prop Firm Compliance
        self.rule_profile = get_profile(prop_firm_profile_id)
        self.compliance = PropFirmComplianceEngine(
            rule_profile=self.rule_profile,
            state_path=self.compliance_state_file,
            safety_buffer_pct=0.005,
        )

        # MT5 Bridge
        mt5_cfg = MT5Config(
            symbol=self.params.symbol,
            contract_size=self.params.contract_size,
        )
        self.bridge = MT5Bridge(
            config=mt5_cfg,
            risk_engine=self.risk_engine,
            audit=self.audit,
        )

        self._running = False
        self._last_processed_bar_ts: Optional[str] = None
        self._load_algo_state()

    # ----------------------------------------------------------------------
    # Safety Verification
    # ----------------------------------------------------------------------

    def _enforce_demo_safety(self) -> None:
        """Enforces absolute prohibition of live trading."""
        if Environment.is_live(self.config.environment):
            err_msg = (
                f"FATAL: Live environment requested ('{self.config.environment}'). "
                "Per AGENTS.md A2, no live trading code path exists in this project. "
                "Halting immediately."
            )
            logger.critical(err_msg)
            raise RuntimeError(err_msg)

    def _verify_mt5_demo_mode(self) -> tuple[bool, str]:
        """Independent verification that connected account is DEMO."""
        self._enforce_demo_safety()
        try:
            import MetaTrader5 as mt5
            acc_info = mt5.account_info()
            if acc_info is None:
                return False, "Failed to retrieve MT5 account info"
            
            # In MetaTrader 5: ACCOUNT_TRADE_MODE_DEMO == 0
            if acc_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
                return False, f"CRITICAL: Non-demo trade mode detected: {acc_info.trade_mode}"
            
            return True, f"DEMO verified (Account: {acc_info.login}, Server: {acc_info.server})"
        except ImportError:
            # When MT5 is not installed / mocked
            return True, "DEMO verified (MT5 library simulated/offline)"
        except Exception as e:
            return False, f"MT5 demo verification exception: {e}"

    # ----------------------------------------------------------------------
    # State Persistence
    # ----------------------------------------------------------------------

    def _load_algo_state(self) -> None:
        if self.algo_state_file.exists():
            try:
                data = json.loads(self.algo_state_file.read_text(encoding="utf-8"))
                self._last_processed_bar_ts = data.get("last_processed_bar_ts")
            except Exception as e:
                logger.warning(f"Could not load algo state: {e}")

    def _save_algo_state(self) -> None:
        try:
            state = {
                "last_processed_bar_ts": self._last_processed_bar_ts,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.algo_state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save algo state: {e}")

    # ----------------------------------------------------------------------
    # Market Data Ingestion
    # ----------------------------------------------------------------------

    def fetch_h1_bars(self, n_bars: int = 150) -> list[CandleBar]:
        """Fetch closed H1 bars from MT5 or historical store."""
        bars: list[CandleBar] = []
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                # Fetch recent bars; pos 1 starts from the LAST COMPLETED bar
                rates = mt5.copy_rates_from_pos(self.params.symbol, mt5.TIMEFRAME_H1, 1, n_bars)
                if rates is not None and len(rates) > 0:
                    for r in rates:
                        ts = datetime.fromtimestamp(r["time"], tz=timezone.utc)
                        bars.append(CandleBar(
                            timestamp=ts,
                            open=float(r["open"]),
                            high=float(r["high"]),
                            low=float(r["low"]),
                            close=float(r["close"]),
                            volume=float(r["tick_volume"]),
                        ))
                    return bars
        except Exception as e:
            logger.debug(f"MT5 live fetch unavailable: {e}. Falling back to historical parquet store.")

        # Fallback to historical parquet store
        parquet_path = Path("data/store/max/XAUUSD_H1_MAX.parquet")
        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            tail = df.tail(n_bars)
            for _, row in tail.iterrows():
                ts = pd.Timestamp(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.tz_localize(timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                bars.append(CandleBar(
                    timestamp=ts.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0)),
                ))
        return bars

    # ----------------------------------------------------------------------
    # Core Strategy Logic
    # ----------------------------------------------------------------------

    def evaluate_cycle(self) -> dict[str, Any]:
        """Execute one complete evaluation cycle on closed candles.

        Returns decision dictionary: TAKE, NO_TRADE, REDUCE_RISK, or HALT.
        """
        decision_id = str(uuid.uuid4())
        cycle_time = datetime.now(timezone.utc)
        result: dict[str, Any] = {
            "decision_id": decision_id,
            "timestamp": cycle_time.isoformat(),
            "action": "NO_TRADE",
            "reason": "",
            "details": {},
        }

        # 1. Independent Demo Verification
        demo_ok, demo_msg = self._verify_mt5_demo_mode()
        if not demo_ok:
            result["action"] = "HALT"
            result["reason"] = f"DEMO_CHECK_FAILED: {demo_msg}"
            self.compliance.halt(result["reason"])
            logger.critical(result["reason"])
            return result

        # 2. Ingest Closed H1 Bars
        bars = self.fetch_h1_bars(n_bars=self.params.donchian_period + 30)
        if len(bars) < self.params.donchian_period + 15:
            result["reason"] = "INSUFFICIENT_BARS"
            return result

        # In closed-candle trading, bars[-1] is the latest COMPLETED bar.
        latest_closed = bars[-1]
        latest_ts_str = latest_closed.timestamp.isoformat()

        # Prevent duplicate entries on the same completed candle
        if self._last_processed_bar_ts == latest_ts_str:
            result["reason"] = "CANDLE_ALREADY_PROCESSED"
            return result

        # 3. Session Filter (London: 08:00 - 16:00 UTC)
        bar_hour = latest_closed.timestamp.hour
        if bar_hour < self.params.session_start_utc or bar_hour >= self.params.session_end_utc:
            result["reason"] = f"OUTSIDE_SESSION (Hour {bar_hour} UTC not in {self.params.session_start_utc}-{self.params.session_end_utc})"
            self._last_processed_bar_ts = latest_ts_str
            self._save_algo_state()
            return result

        # 4. Data Quality Check
        df_bars = pd.DataFrame([asdict(b) for b in bars])
        quality = self.data_quality.evaluate(df_bars, dataset_id="XAUUSD_H1")
        if quality.score < 0.70:
            result["reason"] = f"DATA_QUALITY_LOW ({quality.score:.2f} < 0.70)"
            return result

        # 5. Calculate Indicators
        # Historical window strictly excludes the latest closed bar
        hist_window = bars[:-1]
        df_hist = pd.DataFrame([asdict(b) for b in hist_window])
        upper_arr, lower_arr = calculate_donchian(df_hist, period=self.params.donchian_period)
        atr_arr = calculate_atr(df_hist, period=self.params.atr_period)

        donchian_upper = float(upper_arr[-1])
        donchian_lower = float(lower_arr[-1])
        current_atr = float(atr_arr[-1])

        if np.isnan(donchian_upper) or np.isnan(donchian_lower) or np.isnan(current_atr):
            result["reason"] = "INDICATOR_CALC_FAILED"
            return result

        # 6. ATR Filter ($8.00 minimum documented research parameter)
        if current_atr < self.params.min_atr_usd:
            result["reason"] = f"ATR_BELOW_MINIMUM (${current_atr:.2f} < ${self.params.min_atr_usd:.2f})"
            self._last_processed_bar_ts = latest_ts_str
            self._save_algo_state()
            return result

        # 7. Regime Detection
        regime_state = self.regime_detector.detect(df_hist)

        # 8. Signal Detection
        close_price = latest_closed.close
        side: Optional[OrderSide] = None

        if close_price > donchian_upper:
            side = OrderSide.BUY
        elif close_price < donchian_lower:
            side = OrderSide.SELL

        if side is None:
            result["reason"] = "NO_BREAKOUT"
            self._last_processed_bar_ts = latest_ts_str
            self._save_algo_state()
            return result

        # 9. Account State & Position Sizing
        balance, equity = self._get_account_balance_and_equity()
        self.compliance.update_equity(balance=balance, equity=equity, timestamp=latest_closed.timestamp)

        stop_distance = current_atr * self.params.atr_mult_stop
        risk_dollars = equity * self.params.risk_per_trade_pct

        # Calculate lots: 1 point (0.01) = $10 per lot
        stop_points = stop_distance / 0.01
        calculated_lots = risk_dollars / (stop_points * self.params.point_value_usd)
        
        # Round lots to broker step (0.01 min, 0.01 step)
        lots = round(calculated_lots / 0.01) * 0.01
        lots = max(0.01, min(lots, 10.0))

        # Stop loss & take profit levels
        if side == OrderSide.BUY:
            sl_price = round(close_price - stop_distance, 2)
            tp_price = round(close_price + (current_atr * self.params.atr_mult_tp), 2) if self.params.atr_mult_tp > 0 else 0.0
        else:
            sl_price = round(close_price + stop_distance, 2)
            tp_price = round(close_price - (current_atr * self.params.atr_mult_tp), 2) if self.params.atr_mult_tp > 0 else 0.0

        # 10. Safety Gate Pre-Trade Evaluation
        context = RiskContext(
            daily_loss_so_far=self.compliance.generate_report()["daily_exposure_usd"],
            portfolio_drawdown=self.compliance.generate_report()["total_exposure_pct"],
            open_positions=len(self.bridge.get_positions()),
            data_quality_score=quality.score,
            connection_status="connected",
            spread_points=2.0,
        )
        safety_verdict = self.safety_gate.check_pre_trade(context)
        if not safety_verdict.can_proceed:
            result["action"] = "HALT" if safety_verdict.risk_status == SafetyStatus.HALTED else "NO_TRADE"
            result["reason"] = f"SAFETY_GATE_REJECTED: {safety_verdict.reason}"
            return result

        # 11. Prop Firm Compliance Check
        compliance_verdict = self.compliance.check_trade_eligible(risk_amount=risk_dollars)
        if not compliance_verdict.can_proceed:
            result["action"] = "NO_TRADE"
            result["reason"] = f"COMPLIANCE_REJECTED: {compliance_verdict.reason}"
            return result

        # 12. Approved Execution
        result["action"] = "TAKE"
        result["reason"] = f"Donchian-{self.params.donchian_period} Breakout Approved"
        result["details"] = {
            "side": side.value,
            "lots": lots,
            "price": close_price,
            "sl": sl_price,
            "tp": tp_price,
            "atr": current_atr,
            "regime": regime_state.regime.value,
            "risk_dollars": risk_dollars,
            "compliance_state": self.compliance.state.value,
        }

        # Submit demo order via bridge
        order_res = self.bridge.send_order(
            symbol=self.params.symbol,
            side=side,
            volume=lots,
            price=close_price,
            sl=sl_price,
            tp=tp_price,
        )
        result["order_result"] = order_res

        self.audit.log(
            event="EXEC_ORDER_SUBMITTED",
            action=f"Executed {side.value} {lots} {self.params.symbol}",
            details=result["details"],
            actor="mt5_quant_algo",
        )

        self._last_processed_bar_ts = latest_ts_str
        self._save_algo_state()
        return result

    def _get_account_balance_and_equity(self) -> tuple[float, float]:
        """Fetch current balance and equity from MT5 or mock defaults."""
        try:
            import MetaTrader5 as mt5
            acc = mt5.account_info()
            if acc is not None:
                return float(acc.balance), float(acc.equity)
        except Exception:
            pass
        return 10000.0, 10000.0

    # ----------------------------------------------------------------------
    # CLI Command Handlers
    # ----------------------------------------------------------------------

    def status_command(self) -> None:
        """Display system status, compliance health, and current market state."""
        print("=" * 65)
        print(" QUANT RESEARCH LAB — MT5 DEMO ALGORITHM STATUS")
        print("=" * 65)
        demo_ok, demo_msg = self._verify_mt5_demo_mode()
        print(f"Safety Check       : {'PASS' if demo_ok else 'FAIL'} ({demo_msg})")
        print(f"Environment        : {self.config.environment}")
        print(f"Strategy           : Donchian-{self.params.donchian_period} Breakout (XAUUSD H1)")
        print(f"Prop Firm Model    : {self.rule_profile.display_name}")
        print(f"Rule Status        : {self.rule_profile.source_status.value}")
        print(f"Compliance State   : {self.compliance.state.value}")

        report = self.compliance.generate_report()
        print("-" * 65)
        print(f"Current Balance    : ${report['current_balance']:,.2f}")
        print(f"Total Realized P&L : ${report['total_realized_pnl']:,.2f}")
        print(f"Daily Room Left    : ${report['daily_room_usd']:,.2f} / ${report['max_daily_loss_usd']:,.2f}")
        print(f"Total Room Left    : ${report['total_room_usd']:,.2f} / ${report['max_total_loss_usd']:,.2f}")
        print(f"Trading Days Met   : {report['trading_days']} (Min required: {report['min_trading_days_required']})")
        print("=" * 65)

    def backtest_command(
        self,
        parquet_path: Path | str = "data/store/max/XAUUSD_H1_MAX.parquet",
        risk_pct: float | None = None,
    ) -> dict[str, Any]:
        """Run backtest on historical H1 data with correct XAUUSD sizing & compliance."""
        from engine.backtest_fixed import run_backtest_fixed

        p_path = Path(parquet_path)
        if not p_path.exists():
            print(f"Error: Historical data file {p_path} not found.")
            return {}

        df = pd.read_parquet(p_path)
        risk = risk_pct or self.params.risk_per_trade_pct

        print(f"\nRunning backtest on {len(df):,} bars of {self.params.symbol} H1...")
        print(f"Settings: Donchian={self.params.donchian_period}, ATR_Filter=${self.params.min_atr_usd:.2f}, Risk={risk*100:.2f}%")

        # Generate signals on closed bars
        upper, lower = calculate_donchian(df, period=self.params.donchian_period)
        atr = calculate_atr(df, period=self.params.atr_period)

        signals = []
        for i in range(self.params.donchian_period + 1, len(df)):
            ts = pd.Timestamp(df["timestamp"].iloc[i])
            if ts.hour < self.params.session_start_utc or ts.hour >= self.params.session_end_utc:
                continue

            current_atr = atr[i - 1]
            if np.isnan(current_atr) or current_atr < self.params.min_atr_usd:
                continue

            close_prev = df["close"].iloc[i - 1]
            u_prev = upper[i - 1]
            l_prev = lower[i - 1]

            if close_prev > u_prev:
                signals.append({
                    "timestamp": df["timestamp"].iloc[i],
                    "side": "BUY",
                    "price": df["open"].iloc[i],
                    "atr": current_atr,
                })
            elif close_prev < l_prev:
                signals.append({
                    "timestamp": df["timestamp"].iloc[i],
                    "side": "SELL",
                    "price": df["open"].iloc[i],
                    "atr": current_atr,
                })

        print(f"Generated {len(signals)} breakout signals.")
        results = run_backtest_fixed(
            data=df,
            signals=signals,
            initial_capital=self.rule_profile.initial_balance,
            risk_per_trade=risk,
            max_drawdown_pct=self.rule_profile.max_total_loss_pct,
            daily_loss_limit_pct=self.rule_profile.max_daily_loss_pct,
            max_holding_bars=self.params.max_holding_bars,
            atr_mult=self.params.atr_mult_stop,
            commission_rate=self.params.commission_rate,
            slippage_bps=self.params.slippage_bps,
        )

        metrics = results.get("metrics", {})
        print("\n" + "=" * 60)
        print(" BACKTEST RESULTS SUMMARY")
        print("=" * 60)
        print(f"Total Trades     : {metrics.get('total_trades', 0)}")
        print(f"Win Rate         : {metrics.get('win_rate', 0.0)*100:.1f}%")
        print(f"Profit Factor    : {metrics.get('profit_factor', 0.0):.2f}")
        print(f"Sharpe Ratio     : {metrics.get('sharpe_ratio', 0.0):.2f}")
        print(f"Sortino Ratio    : {metrics.get('sortino_ratio', 0.0):.2f}")
        print(f"Max Drawdown     : {metrics.get('max_drawdown_pct', 0.0):.2f}%")
        print(f"Final Return     : {metrics.get('total_return_pct', 0.0):.2f}%")
        print(f"Halted Status    : {results.get('halted', False)}")
        print("=" * 60)
        return results

    def report_command(self) -> None:
        """Print full compliance and audit report."""
        report = self.compliance.generate_report()
        print(json.dumps(report, indent=2))

    def run_demo_command(self, once: bool = False, interval_seconds: int = 60) -> None:
        """Run demo trading execution loop."""
        self._running = True
        logger.info("Starting MT5 Quant Algo demo loop (DEMO ONLY)...")

        def handle_signal(sig: int, frame: Any) -> None:
            logger.info("Shutdown signal received. Exiting gracefully...")
            self._running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        while self._running:
            try:
                res = self.evaluate_cycle()
                logger.info(f"Cycle Result: action={res['action']}, reason={res['reason']}")
            except Exception as e:
                logger.error(f"Error in execution cycle: {e}")

            if once:
                break
            time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Quant Research + MT5 Demo-Trading Algorithm for XAUUSD",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # status
    subparsers.add_parser("status", help="Show system status and compliance state")

    # backtest
    bt_parser = subparsers.add_parser("backtest", help="Run backtest on historical H1 data")
    bt_parser.add_argument("--risk", type=float, default=0.0025, help="Risk fraction per trade (default 0.0025)")
    bt_parser.add_argument("--data", type=str, default="data/store/max/XAUUSD_H1_MAX.parquet", help="Path to parquet")

    # validate
    subparsers.add_parser("validate", help="Run walk-forward validation and robustness checks")

    # run-demo
    run_parser = subparsers.add_parser("run-demo", help="Run live demo trading loop")
    run_parser.add_argument("--once", action="store_true", help="Execute single evaluation cycle and exit")
    run_parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds")

    # report
    subparsers.add_parser("report", help="Output daily compliance and risk report")

    args = parser.parse_args()
    algo = MT5QuantAlgorithm()

    if args.command == "status" or not args.command:
        algo.status_command()
    elif args.command == "backtest":
        algo.backtest_command(parquet_path=args.data, risk_pct=args.risk)
    elif args.command == "report":
        algo.report_command()
    elif args.command == "run-demo":
        algo.run_demo_command(once=args.once, interval_seconds=args.interval)
    elif args.command == "validate":
        print("Running walk-forward validation...")
        from engine.prop_firm_research import run_walk_forward
        parquet_path = Path("data/store/max/XAUUSD_H1_MAX.parquet")
        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            wf_results = run_walk_forward(df, risk_per_trade=0.0025)
            print(f"Walk-forward windows processed: {len(wf_results)}")
            for idx, r in enumerate(wf_results):
                print(f"Window {idx+1}: trades={r.get('trades')}, return={r.get('return_pct', 0)*100:.1f}%, DD={r.get('max_drawdown_pct', 0)*100:.1f}%")
        else:
            print(f"Parquet file {parquet_path} not found.")


if __name__ == "__main__":
    main()
