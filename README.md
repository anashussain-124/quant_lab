# Donchian-50 XAUUSD — Quant Research System

> **PROJECT CLOSED — 2026-10-22 (research only).**
>
> The final v4 result is negative: all five tested XAUUSD templates had negative
> gross expectancy, and worse net expectancy after modelled costs, on the
> pre-holdout sample. No strategy reached `PASS-CANDIDATE`, nothing is `ACTIVE`,
> and the sealed final holdout was never accessed. See
> `docs/PROJECT_STATUS.md` and `docs/CLOSEOUT.md`.
>
> **Everything below this notice is legacy, pre-v4 Donchian documentation.** Its
> performance claims (PF 2.0, Sharpe 3.0, "edge candidate"), research tables and
> live/demo instructions are **not** confirmed by the v4 audit and must not be
> read as a current edge or as authorization to trade. No live-trading code path
> exists in this repository.

## Overview

A complete quantitative research and live trading system for the XAUUSD Donchian breakout strategy.

**Strategy:** Donchian(50) breakout during London session (08:00-16:00 UTC) with ATR-based stops.

**Performance (2025-2026 backtest):**
- Profit Factor: 2.0
- Sharpe: 3.0
- Max Drawdown: 4-6%
- Win Rate: 29%

## Quick Start

```bash
# Install dependencies
pip install numpy pandas MetaTrader5 pytest

# Run backtest
python engine/research_standalone.py

# Run live strategy (MT5 demo)
python mt5_donchian_exec.py

# Generate daily report
python daily_report.py
```

## Project Structure

```
donchian_model/
├── engine/                     # Core engine modules
│   ├── research_standalone.py  # Full research suite (backtest, walk-forward, MC)
│   ├── backtest_fixed.py       # Corrected backtest engine
│   ├── regime.py               # Regime detection, ADX, Donchian, ATR
│   ├── strategy_analyzer.py    # Strategy matrix testing
│   ├── validation.py           # DSR/PSR/PBO/AUUC validation
│   └── validation_gates.py     # Gate framework
├── adapters/
│   ├── crypto/                 # CCXT crypto exchange adapters
│   └── mt5/                    # MT5 XAUUSD adapter
├── data/
│   ├── fetch_extended.py       # Data fetching from MT5
│   ├── scrape_mt5.py           # MT5 data scraper
│   └── store/                  # Historical data (parquet files)
│       └── max/
│           ├── XAUUSD_H1_MAX.parquet   # 50K H1 bars (2018-2026)
│           ├── XAUUSD_H4_MAX.parquet   # 34K H4 bars (2004-2026)
│           └── XAUUSD_D1_MAX.parquet   # 5.7K D1 bars (2004-2026)
├── tests/                      # Unit tests (279 tests, all passing)
├── configs/                    # Configuration files
├── logs/                       # Live trading logs
│   ├── mt5_donchian.log        # Execution log
│   ├── trades.json             # Trade history
│   └── strategy_state.json     # Persistent state
├── docs/
│   └── TECHNICAL_REFERENCE.md  # Complete technical reference
├── mt5_donchian_exec.py        # Live MT5 execution script
├── daily_report.py             # Daily P&L report generator
├── README_STRATEGY.md          # Strategy documentation
└── requirements.txt            # Python dependencies
```

## Strategy Details

### Entry Logic
```
LONG:  Close > Donchian_Upper(50) AND 08:00 <= UTC < 16:00
SHORT: Close < Donchian_Lower(50) AND 08:00 <= UTC < 16:00
```

### Exit Logic
```
Stop Loss:  Entry ± 2.5 × ATR(14)
Max Holding: 100 bars
Reverse: Opposite signal
```

### Position Sizing
```
lots = (equity × risk_pct) / (stop_distance × 100)
```

### Risk Limits
```
Risk per trade: 0.25% ($12 at $5K)
Daily loss limit: 5%
Max drawdown: 10%
Max consecutive losses: 10
```

## Research Results

### Risk Level Comparison

| Risk | $ Risk | Trades | PF | Sharpe | Max DD | Return |
|------|--------|--------|-----|--------|--------|--------|
| 0.25% | $12 | 103 | 1.99 | 2.98 | -2.9% | +18.8% |
| **0.50%** | **$25** | **103** | **1.96** | **2.98** | **-5.9%** | **+40.3%** |
| 1.00% | $50 | 39 | 1.24 | 1.25 | -10.6% | +7.6% |

### Parameter Robustness (25/25 combinations profitable)

| Config | PF | Sharpe | Max DD |
|--------|-----|--------|--------|
| D=40 M=1.5 | 2.11 | 3.11 | -7.7% |
| D=50 M=2.0 | 1.96 | 2.98 | -5.9% |
| D=55 M=2.5 | 2.09 | 3.34 | -3.9% |

### Regime Decomposition

| Period | ATR | ADX | London Range | Efficiency |
|--------|-----|-----|--------------|------------|
| 2021-2024 | $4.89 | 37.3 | $5.30 | 0.116 |
| 2025-2026 | $16.85 | 37.9 | $16.11 | 0.122 |

### Monte Carlo (10K simulations)
```
Median DD: -6.4%
P95 DD: -4.4%
Breach probability (10% limit): 5.7%
```

## Live Trading

### MT5 Demo Setup
1. Install MetaTrader 5
2. Create demo account (server: MetaQuotes-Demo)
3. Update `Config.ACCOUNT` in `mt5_donchian_exec.py`

### Cron Job Setup
```
Schedule: 0 8-15 * * 1-5 (hourly, London session, weekdays)
Script: mt5_donchian_exec.py
```

### Telegram Alerts
- Signal detection
- Trade execution
- Errors and warnings

## Key Findings

1. **Edge is regime-conditional** — works only in high-volatility regimes (2025+)
2. **Volatility expansion drives edge** — ATR tripled from $4.89 to $16.85
3. **Parameter robust** — 25/25 combos profitable
4. **London session essential** — Asian session has no edge
5. **0.5% risk optimal** — 1% breaches DD limits

## Classification

**REGIME-CONDITIONAL EDGE CANDIDATE**

The strategy has a statistically defensible edge, but ONLY in high-volatility regimes. It is NOT a "set and forget" system — volatility must be monitored.

## Next Steps

1. Run MT5 demo for 30-50 trading days
2. Compare demo performance vs backtest
3. Monitor ATR — if it drops below $8, consider pausing
4. Only then consider prop firm funded account

## Emergency Triggers (STOP if any hit)

- Daily loss > 4%
- Max drawdown > 8%
- 5+ consecutive losses
- ATR drops below $8

## License

Research purposes only. No live trading without explicit authorization.

## Synchronize research results with Obsidian

The local-only importer reads the configured JSON/JSONL/CSV research artifacts
and writes generated notes to the existing vault path in
`configs/obsidian_sync.json`. It does not run backtests, access the sealed
holdout, or submit orders. Configure the vault path if the vault moves, then
run from the repository root:

```powershell
qt\.venv\Scripts\python.exe -m scripts.obsidian_sync inspect
qt\.venv\Scripts\python.exe -m scripts.obsidian_sync validate
qt\.venv\Scripts\python.exe -m scripts.obsidian_sync sync
qt\.venv\Scripts\python.exe -m scripts.obsidian_sync list
```

Generated experiment notes and reports are kept under
`07 Results & Reports/Generated by Quant Lab`. Existing authored notes are
preserved; see [the daily workflow](./docs/OBSIDIAN_QUANT_WORKFLOW.md) for
configuration, review, and validation details.
