# v5 Swing Search — Result

Date: 2026-10-22. Environment: `HISTORICAL_REAL`. Branch: `research/swing-v5`.

## Verdict

**NO_EDGE_FOUND within the pre-registered protocol.** No configuration passed all ten
gates. The sealed 2023-2026 holdout was **not opened**. No MQL5 EA, Strategy Tester run,
demo deployment, or live path was created.

This is a valid result under `docs/PRE_REGISTRATION_SWING_V5.md`, which states that if no
configuration passes, the search ends with a documented no-edge result and the holdout
remains sealed.

## What was run

- 50 pre-registered XAUUSD H1 configurations: 5 families x 10 parameter sets, frozen
  before the first test.
- Data: 64,651 MT5 H1 bars, 2012-01-03 .. 2022-12-30 UTC. Development 2013-2018,
  selection/OOS 2019-2022. Data SHA-256
  `5dbace90244b3b7e422890ef285b29f8e3f20591266ae2db641ce60bb82e267e`.
- Protocol SHA-256 `d02a6a8e...46b56`; Amendment 1 `33538557...0cd7c`.
- Costs: observed spread (10-point floor, causal median fallback), 0.5 point slippage
  per side, USD 7/lot round-turn commission, MT5 swap (-12.6/-4.6 points, triple
  Wednesday). Mean modelled cost was 0.023-0.031R per trade plus 0.012-0.020R swap.
- Execution: signal at bar close, fill at next bar open, stop-first, gaps at the open,
  trailing stop updated at bar close, one position at a time.
- Data-quality gate: 0% invalid OHLC, zero/negative prices, duplicate timestamps,
  missing values, or missing volume in 2013-2022. PASS.
- Gates: 100+ OOS trades, bootstrap 95% CI lower bound > 0, 4/6 positive stability
  folds, DSR >= 0.95 with 50 trials, CSCV PBO <= 0.30, 1.5x cost stress, +/-20%
  perturbation, ATR-tercile/year coverage, random-entry null, max DD <= 25R.

Run code label: `c7526a11721b009012b11f21511f888d8c9b90b4-dirty`. The `-dirty` suffix
reflects untracked output and probe paths, not a code change; the research code is commit
`c7526a1`. The run is deterministic and was reproduced with identical metrics.

## Binding result

| Gate | Failures / 50 |
|---|---:|
| G1 OOS trades >= 100 | 3 |
| G2 OOS mean > 0 with CI lower > 0 | **50** |
| G3 4/6 positive folds | 37 |
| G4 DSR >= 0.95 | **50** |
| G5 PBO <= 0.30 | **50** (PBO = 0.550) |
| G6 1.5x cost stress | 31 |
| G7 +/-20% perturbation | not reached |
| G8 regime/year coverage | 38 |
| G9 random-entry null | not reached |
| G10 max DD <= 25R | 31 |

Every configuration failed G2 and G4. The 95% bootstrap CI of the mean net R included
zero for all 50 trials, and no trial's Deflated Sharpe Ratio exceeded 0.95. G7 and G9 were
not evaluated because no configuration passed the earlier gates; they are reported as
not passed, not as evaluated-and-cleared.

Best raw means, before the multiple-testing correction:

| ID | Family | OOS n | Mean net R | 95% CI | DSR | Folds+ | Max DD R | Yearly total R |
|---|---|---:|---:|---|---:|---:|---:|---|
| dc05 | donchian 200 / 2.0 / 4.0 | 174 | +0.3131 | [-0.0586, +0.7365] | 0.411 | 3/6 | 21.8 | 2019 +17.8, 2020 +51.3, 2021 -2.1, 2022 -12.6 |
| em08 | EMA 50/100 crossover | 108 | +0.2085 | [-0.1434, +0.6211] | 0.363 | 4/6 | 12.2 | 2019 +0.8, 2020 +3.5, 2021 +14.3, 2022 +4.0 |
| dc06 | donchian 200 / 3.0 / 4.0 | 157 | +0.1883 | [-0.0963, +0.5154] | 0.310 | 3/6 | 22.6 | 2019 +14.6, 2020 +33.8, 2021 -7.6, 2022 -11.2 |
| dc04 | donchian 100 / 3.0 / 4.0 | 237 | +0.1484 | [-0.0638, +0.3888] | 0.203 | 3/6 | 14.3 | 2019 +11.7, 2020 +31.6, 2021 -2.9, 2022 -5.3 |
| dc03 | donchian 100 / 2.0 / 3.0 | 309 | +0.0837 | [-0.1028, +0.2830] | 0.067 | 4/6 | 22.7 | 2019 +13.2, 2020 +22.3, 2021 +6.5, 2022 -16.1 |

The highest raw mean, dc05, is a 2020 gold-trend artifact: +51.3R in 2020 and negative in
both 2021 and 2022. It fails the fold and year-coverage gates before the statistical
correction. em08 is the most coherent result: positive in all four OOS years, 4/6 positive
folds, 12.2R max drawdown, and a 0.21R mean. But its 95% CI spans zero and its DSR is 0.36,
so it is **not** evidence of an edge at the pre-registered bar. It is not promoted.

## What is real, simulated, and unverified

- **Real:** MT5 XAUUSD H1 OHLC and volume from the MetaQuotes demo server; observed spreads
  where present; UTC timestamps verified against audited M5 buckets.
- **Simulated:** next-open fills, fixed slippage, stop-first tie-breaking, no queue,
  partial fills, rejects, requotes, or market impact. Swap is the documented MT5 point
  value, not a broker statement.
- **Unverified:** OOS 2019-2022 was used to select among 50 trials, so it is selection
  data. Only the sealed 2023-2026 holdout would be clean, and it was not opened because
  no candidate passed.
- **Could invalidate:** a coding error in the causal spread/swap series; a different
  broker's spread, commission, or swap; a structural change in XAUUSD; or the possibility
  that a real, small edge exists but this sample is too short to resolve it.

## Why this does not prove "no strategy can work"

The per-trade standard deviation of the trend configurations is roughly 1.7-2.7R because
winners can reach 10-20R. At that variance, resolving a 0.2R edge at 95% confidence needs
on the order of 400+ OOS trades, not 100-200. The correct reading is **"no edge was
demonstrated at the required confidence"**, not "an edge is proven absent". A larger
sample, a different instrument universe, or a lower-variance hypothesis class would be a
new pre-registered program, not a re-tune of these 50 trials.

## What was deliberately not done

- The sealed holdout was not read. The lock file still has `candidate_frozen: false` and
  an empty access log.
- No MT5 Strategy Tester reconciliation, because the protocol makes it conditional on a
  gate-passing candidate.
- No demo/paper deployment and no live code path. The project remains incapable of live
  trading by construction.

## Reproduce

```bash
qt/.venv/Scripts/python.exe scripts/export_v5_data.py   # rebuilds data only if MT5 is available
qt/.venv/Scripts/python.exe scripts/run_v5_search.py    # 50 trials, gates, report, registry
qt/.venv/Scripts/python.exe -m pytest tests/test_v5_backtest.py \
  tests/test_v5_strategies.py tests/test_v5_validation.py -q
```

Artifacts: `reports_v5/search_2026-10-22/report.md` (human record),
`report.json` and `trade_log.csv` (local, gitignored), `experiments/registry_v5.jsonl`
(51 records: 50 trials plus the search summary).
