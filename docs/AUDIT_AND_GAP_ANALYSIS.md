# Quant Lab — Audit & Gap Analysis

## Current State Audit

### What EXISTS and WORKS (tested, 279 tests passing)

| Component | File | Status |
|-----------|------|--------|
| Safety gate | `engine/safety_gate.py` | ✅ Deterministic, absolute veto |
| Config | `configs/__init__.py` | ✅ LabConfig, Environment |
| Logger | `engine/logger.py` | ✅ AuditLogger, structured |
| Backtester | `engine/backtester_v2.py` | ✅ Event-driven, cost model |
| Quant math | `libs/quant_math/__init__.py` | ✅ E1-E11 functions |
| Validation | `engine/validation.py` | ✅ DSR/PSR/PBO/AUUC |
| Risk engine | `engine/risk_engine.py` | ✅ Paper broker, kill switch |
| MT5 bridge | `engine/mt5_bridge.py` | ✅ Shadow/demo modes |
| Crypto arb | `engine/crypto_arb.py` | ✅ Funding/price arb |
| Regimes | `engine/phases.py` | ✅ Hurst, strategy factory |
| Gate | `engine/gate.py` | ✅ Go/No-Go promotion |
| Dashboard | `engine/dashboard.py` | ✅ Metrics, alerts, health |
| Research agent | `engine/research_agent.py` | ✅ L0-L2 |
| Orchestrator | `engine/orchestrator.py` | ✅ Wires all |
| Pipeline | `engine/pipeline.py` | ✅ N-day demo, verdicts |
| Adapters base | `adapters/__init__.py` | ✅ Abstract + quality |
| Data quality | `adapters/data_quality.py` | ✅ Quality engine |
| Storage | `data/storage.py` | ✅ Parquet + hashing |

### What is PARTIALLY IMPLEMENTED (placeholder/simulated)

| Component | What's Missing |
|-----------|----------------|
| `adapters/crypto/__init__.py` | CCXT calls are simulated — no real exchange connection |
| `adapters/mt5/__init__.py` | MetaTrader5Adapter class exists but methods are stubs |
| `engine/mt5_bridge.py` | Bridge logic complete but no real MT5 connection |
| `engine/pipeline.py` | Signals are simulated, not from real data |
| `engine/phases.py` | StrategyFactory + RegimeDetector but no real strategies |
| `data/storage.py` | ParquetStorage works but no real data flowing through |

### What is SIMULATED (needs real data)

| Component | Simulation |
|-----------|------------|
| `CCXTAdapter.fetch_ticks()` | Returns hardcoded bid/ask |
| `CCXTAdapter.fetch_historical()` | Returns random OHLCV |
| `CCXTAdapter.fetch_order_book()` | Returns hardcoded book |
| `CCXTAdapter.get_funding_rate()` | Returns 0.0001 |
| `CCXTAdapter.get_open_interest()` | Returns 1000000 |
| `Pipeline._generate_signals()` | Returns random signals |
| `MetaTrader5Adapter` | All methods are stubs |

### What is MISSING entirely

| Gap | Priority |
|-----|----------|
| Real CCXT connection to Binance/Bybit | P1 |
| Real historical data fetch (OHLCV, trades, book) | P1 |
| Data validation pipeline (real checks) | P2 |
| Redis/Docker infrastructure | P3 |
| Real MT5 connection and validation | P4 |
| First real historical backtest | P5 |
| Strategy research framework (multiple hypotheses) | P6 |
| Train/validation/test chronological separation | P7 |
| Walk-forward testing | P8 |
| Robustness/overfitting tests | P9 |
| Paper/demo deployment of survivors | P10 |
| 30-50+ demo trading days | P11 |
| Live vs backtest comparison | P12 |
| Continuous hypothesis generation | P13 |
| Experiment database (persistent registry) | P6 |
| Strategy versioning (immutable versions) | P6 |
| Per-strategy performance metrics (full set) | P6 |
| Daily report with all sections (master prompt §21) | P10 |

---

## Implementation Plan (per master prompt §24)

### PRIORITY 1: Connect Real Market Data
**Goal**: Replace simulated CCXT calls with real exchange data.

Tasks:
1. Install CCXT: `pip install ccxt`
2. Rewrite `adapters/crypto/__init__.py` to use real CCXT:
   - `fetch_ticks()` → `exchange.fetch_trades()`
   - `fetch_historical()` → `exchange.fetch_ohlcv()`
   - `fetch_order_book()` → `exchange.fetch_order_book()`
   - `get_funding_rate()` → `exchange.fetch_funding_rate()`
   - `get_open_interest()` → `exchange.fetch_open_interest()`
3. Add rate limiting per exchange
4. Add error handling for exchange outages
5. Test with Binance public API (no keys needed for public data)

### PRIORITY 2: Data Validation and Persistence
**Goal**: Real data → validation → normalization → storage.

Tasks:
1. Enhance `DataQualityEngine` with real checks
2. Add `receive_time` tracking separate from `event_time`
3. Store raw data separately from processed
4. Record all transformations
5. Persist to Parquet with content hash versioning

### PRIORITY 3: Redis/Docker Infrastructure
**Goal**: Start Redis for caching and message queuing.

Tasks:
1. Start Docker Desktop
2. Run Redis container
3. Add Redis client to data pipeline
4. Cache order books and recent ticks

### PRIORITY 4: Connect and Validate MT5 Demo
**Goal**: Real MT5 connection, verify broker specs.

Tasks:
1. Install MT5 terminal
2. Implement `adapters/mt5/__init__.py` with real MetaTrader5 package
3. Validate: broker, account, symbol, bid/ask, spread, contract specs
4. Test historical candle fetch
5. Test order execution in DEMO mode only

### PRIORITY 5: First Real Historical Backtest
**Goal**: Run backtest on real historical data.

Tasks:
1. Fetch 1+ year of BTC/USDT daily data
2. Run momentum_breakout strategy
3. Compute full metrics (Sharpe, Sortino, drawdown, etc.)
4. Save results with experiment metadata

### PRIORITY 6: Strategy Research Framework
**Goal**: Replace placeholder with multi-hypothesis framework.

Tasks:
1. Define strategy interface (entry, exit, risk, sizing)
2. Implement 3+ strategy families:
   - Trend/momentum
   - Mean reversion
   - Market structure
3. Add experiment registry (persistent)
4. Add strategy versioning (immutable)
5. Add full performance metrics (master prompt §13)

### PRIORITY 7-13: Advanced Research
**Goal**: Walk-forward, overfitting defense, demo deployment.

(Details in master prompt §7-13)

---

## Key Architectural Decisions

1. **CCXT for crypto** — unified API, supports Binance/Bybit/OKX
2. **MT5 for XAUUSD** — broker-provided, demo-only
3. **Parquet for storage** — columnar, versioned by content hash
4. **Redis for caching** — order books, recent ticks
5. **UTC everywhere** — normalize at adapter boundary
6. **Decimal for prices** — no floating-point in financial calculations
7. **Separate raw/processed** — never alter raw data
8. **Immutable strategy versions** — never overwrite experiments
9. **Risk engine independent** — strategies cannot override limits
10. **Demo results are upper bound** — never claim live viability from demo

---

## Honest Assessment

**What we have**: A structurally complete, well-tested (279 tests) quant research framework with all the right abstractions.

**What we don't have**: Any evidence of a trading edge. The system is strategically empty — it can run, report, and issue verdicts, but it hasn't been tested on real data with real strategies.

**The hard part**: Finding a strategy that survives walk-forward testing, robustness checks, and 30-50+ demo days. Most ideas will fail. That's the point.

**Next action**: PRIORITY 1 — connect real CCXT data. This unblocks everything else.
