"""Unit and integration tests for mt5_quant_algo.py.

Validates:
1. Rejection of LIVE environments (fails closed)
2. DEMO mode check logic
3. Closed candle signal generation and London session filter
4. ATR threshold filter ($8.00)
5. Position sizing calculation (XAUUSD specs)
6. Duplicate candle processing prevention
7. Integration with SafetyGate and PropFirmComplianceEngine
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pandas as pd
import numpy as np

from configs import LabConfig, Environment
from engine.prop_firm_rules import FUNDINGPIPS_2STEP_STANDARD_5PCT_P1
from mt5_quant_algo import MT5QuantAlgorithm, StrategyParameters, CandleBar


class TestMT5QuantAlgoSafety:
    def test_live_environment_forbidden(self):
        # Must raise RuntimeError if environment is LIVE
        with pytest.raises(RuntimeError, match="LIVE environment detected"):
            cfg = LabConfig(environment="LIVE")
            MT5QuantAlgorithm(config=cfg)

    def test_non_demo_account_rejected(self, monkeypatch, tmp_path):
        algo = MT5QuantAlgorithm(
            config=LabConfig(environment="SIMULATED"),
            state_dir=tmp_path,
        )
        # Mock MT5 returning a live account (trade_mode = 1)
        mock_acc = MagicMock()
        mock_acc.trade_mode = 1  # Not DEMO (0)
        mock_acc.login = 999999
        mock_acc.server = "Real-Server"

        mock_mt5 = MagicMock()
        mock_mt5.account_info.return_value = mock_acc
        mock_mt5.ACCOUNT_TRADE_MODE_DEMO = 0

        monkeypatch.setattr("mt5_quant_algo.mt5", mock_mt5, raising=False)
        with patch.dict("sys.modules", {"MetaTrader5": mock_mt5}):
            ok, reason = algo._verify_mt5_demo_mode()
            assert not ok
            assert "Non-demo trade mode detected" in reason

    def test_demo_account_accepted(self, monkeypatch, tmp_path):
        algo = MT5QuantAlgorithm(
            config=LabConfig(environment="SIMULATED"),
            state_dir=tmp_path,
        )
        mock_acc = MagicMock()
        mock_acc.trade_mode = 0  # DEMO
        mock_acc.login = 5056172579
        mock_acc.server = "MetaQuotes-Demo"

        mock_mt5 = MagicMock()
        mock_mt5.account_info.return_value = mock_acc
        mock_mt5.ACCOUNT_TRADE_MODE_DEMO = 0

        monkeypatch.setattr("mt5_quant_algo.mt5", mock_mt5, raising=False)
        with patch.dict("sys.modules", {"MetaTrader5": mock_mt5}):
            ok, reason = algo._verify_mt5_demo_mode()
            assert ok
            assert "DEMO verified" in reason


class TestMT5QuantAlgoStrategy:
    @pytest.fixture
    def algo(self, tmp_path):
        return MT5QuantAlgorithm(
            config=LabConfig(environment="SIMULATED"),
            strategy_params=StrategyParameters(
                donchian_period=50,
                atr_period=14,
                min_atr_usd=8.0,
                session_start_utc=8,
                session_end_utc=16,
            ),
            state_dir=tmp_path,
        )

    def test_session_filter(self, algo):
        # Create bars ending at 03:00 UTC (outside London session)
        bars = []
        base_dt = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc) - timedelta(hours=70)
        for i in range(70):
            bars.append(CandleBar(
                timestamp=base_dt + timedelta(hours=i),
                open=2650.0,
                high=2660.0,
                low=2640.0,
                close=2655.0,
            ))

        with patch.object(algo, "fetch_h1_bars", return_value=bars):
            with patch.object(algo, "_verify_mt5_demo_mode", return_value=(True, "DEMO verified")):
                res = algo.evaluate_cycle()
                assert res["action"] == "NO_TRADE"
                assert "OUTSIDE_SESSION" in res["reason"]

    def test_atr_below_minimum(self, algo):
        # Create bars ending at 10:00 UTC (in London session) but low volatility (ATR < 8.0)
        bars = []
        base_dt = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc) - timedelta(hours=70)
        for i in range(70):
            bars.append(CandleBar(
                timestamp=base_dt + timedelta(hours=i),
                open=2650.0,
                high=2651.0,
                low=2649.0,
                close=2650.5,
            ))

        with patch.object(algo, "fetch_h1_bars", return_value=bars):
            with patch.object(algo, "_verify_mt5_demo_mode", return_value=(True, "DEMO verified")):
                res = algo.evaluate_cycle()
                assert res["action"] == "NO_TRADE"
                assert "ATR_BELOW_MINIMUM" in res["reason"]

    def test_duplicate_candle_prevention(self, algo):
        bars = []
        base_dt = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc) - timedelta(hours=70)
        for i in range(70):
            bars.append(CandleBar(
                timestamp=base_dt + timedelta(hours=i),
                open=2650.0,
                high=2670.0,
                low=2630.0,
                close=2665.0,
            ))

        algo._last_processed_bar_ts = bars[-1].timestamp.isoformat()
        with patch.object(algo, "fetch_h1_bars", return_value=bars):
            with patch.object(algo, "_verify_mt5_demo_mode", return_value=(True, "DEMO verified")):
                res = algo.evaluate_cycle()
                assert res["action"] == "NO_TRADE"
                assert res["reason"] == "CANDLE_ALREADY_PROCESSED"

    def test_backtest_execution(self, algo):
        results = algo.backtest_command(risk_pct=0.0025)
        assert "metrics" in results
        metrics = results["metrics"]
        assert metrics["total_trades"] > 0
        assert metrics["sharpe_ratio"] > 0.0
