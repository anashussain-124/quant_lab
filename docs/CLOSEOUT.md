# Project Closeout — 2026-10-22

- Status: **CLOSED** (research only; no live or demo trading system was ever run from v4).
- Branch: `redesign/v4`.
- Tag: `closeout-2026-10-22`.
- Environment label for every result below: `BACKTEST` over `HISTORICAL_REAL` data.
- Final holdout accessed: **No**.

## Final verified result

Full test suite: **407 passed, 5 warnings** with `qt/.venv/Scripts/python.exe -m pytest -q` (Python 3.13.15) on 2026-10-22.

Cost decomposition on the full pre-holdout sample (227,737 M5 bars), default
parameters. Source: `reports_v4/costs/cost_decomposition_a052314f19605be8.csv`.

| Template | Trades | Gross R/trade | Cost R/trade | Net R/trade |
|---|---:|---:|---:|---:|
| opening_range_breakout | 40,565 | -0.1052 | 0.2483 | -0.3535 |
| prior_day_sweep | 5,905 | -0.1265 | 0.2483 | -0.3748 |
| volatility_trend | 110,286 | -0.0812 | 0.2732 | -0.3544 |
| zscore_mean_reversion | 34,975 | -0.1568 | 0.2495 | -0.4062 |
| session_drift | 31,120 | -0.1280 | 0.2623 | -0.3903 |

All five default templates have **negative gross expectancy**, so transaction
costs only deepen the loss. The `median_searched` variants are equally negative.
No template reached `PASS-CANDIDATE`; none is `ACTIVE`.

The FundingPips/XAUUSD runner reports in `reports_v4/fundingpips/` are `smoke`
runs over the first 5,000 bars only. They are plumbing checks, not full-sample
evidence, and their risk certification is `INCOMPLETE`.

## What is real, simulated, unverified

- **Real:** MT5 M5 XAUUSD snapshot, SHA-256
  `a052314f19605be8497be9fd4df2168e7845c6c917bdc0be0aa5254164dd3952`,
  267,927 rows from 2023-01-03 to 2026-10-22 UTC. Audit: 0 duplicates,
  0 out-of-order rows, 0 invalid OHLC, 0 missing values, 1,027 gaps, 144 outlier bars.
  Binance BTCUSDT/ETHUSDT 1m data for 2023-2024 is also stored and audited.
- **Simulated:** fills at next-bar open, observed-spread cost model, no contract
  sizing, no commission/swap model, no floating-equity reconstruction. These are
  backtest assumptions, not execution evidence.
- **Unverified:** the full 500-trial multi-timeframe search, three-year Dukascopy
  collection and cross-source comparison (download failed with HTTP 429), full
  dependence-adjusted validation, any demo/paper survival, and any live viability.
- **Not attempted:** MT5 connection, demo orders, live orders, capital allocation.

## What could invalidate this conclusion

The negative result applies to these five templates and this cost model. A
different cost/spread model could change magnitudes but not the sign of the
gross expectancy. Data gaps and broker-specific spread behaviour remain caveats.
This closeout does **not** prove that no XAUUSD strategy can work; it records
that these templates, as configured, did not.

## Reproduce

```bash
qt/.venv/Scripts/python.exe -m pytest -q
qt/.venv/Scripts/python.exe -m scripts.cost_decomposition --help
qt/.venv/Scripts/python.exe -m scripts.fundingpips_xauusd_runner preflight
qt/.venv/Scripts/python.exe -m scripts.fundingpips_xauusd_runner smoke --profile compare
```

## Archive

`quant_lab_closeout_2026-10-22.zip` (sibling of this project folder) contains
the full working tree, git history, `data/store`, reports (including the 32MB
trade logs) and this document. It excludes `qt/.venv` and Python/test caches.
The SHA-256 checksum is in `quant_lab_closeout_2026-10-22.zip.sha256`.

There was **no git remote**: the archive is the only backup. Copy it to external
storage or cloud before deleting anything.

## If the project is ever resumed

Do not tune these five failing templates further. Pre-register a new hypothesis,
run the full frozen-parameter search only on the pre-holdout partition, and keep
the final holdout sealed until a candidate passes the documented gates in
`AGENTS.md` and `docs/adr/0003-v4-gate-and-no-m5-substitution.md`.
