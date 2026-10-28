"""Frozen identifiers shared by the non-executing v6 research infrastructure."""

from enum import StrEnum
from pathlib import Path


class V6Environment(StrEnum):
    HISTORICAL_REAL = "HISTORICAL_REAL"
    SIMULATED = "SIMULATED"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    MT5_DEMO = "MT5_DEMO"

V6_DATASET_ID = "v6_h4_dev_validation"
V6_DATASET_RELATIVE_PATH = (
    Path("data") / "store" / "v6" / "xauusd_h4_dev_validation.parquet"
)
V6_DATASET_SHA256 = "3cdfee22323aed688424460c0f5b63c323c68033a973328f29aa4e3a7b51b38c"
V6_DATASET_START_UTC = "2012-01-03T00:00:00+00:00"
V6_DATASET_END_UTC = "2022-12-30T20:00:00+00:00"
V6_DATASET_BAR_COUNT = 16_944
V6_PROTOCOL_SHA256 = "10ee67e96473c81e9f6aba00003598237954111f08c1b79687c95f8d9b1798af"
V6_COST_MODEL_VERSION = "v6-cost-assumption-interface-1"
V6_REQUIRED_DSR_TRIALS = 74
V6_BOOTSTRAP_RESAMPLES = 10_000
V6_RANDOM_SEED = 0
V6_NULL_SIMULATIONS = 500
V6_GATE_IDS = (
    "g1_trades",
    "g2_mean_ci",
    "g3_folds",
    "g4_dsr",
    "g5_pbo",
    "g6_cost_stress",
    "g7_perturbation",
    "g8_regime",
    "g9_null",
    "g10_drawdown",
)
