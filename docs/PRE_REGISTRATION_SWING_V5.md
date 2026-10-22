# Pre-Registration — Swing Strategy Search v5

Status: **FROZEN before any v5 strategy test.**
Date: 2026-10-22. Owner approval: user selected "Swing H1/H4, focused ~50 trials, personal account, gates + demo".

This document defines the entire search. No parameter set outside this document may be
tested on development or validation data, and no change may be made after seeing results
without recording it as a new, separately counted experiment.

## 1. Question

Is there a simple, cost-surviving swing strategy on XAUUSD H1 with positive
out-of-sample expectancy that passes multiple-testing and robustness gates?

If the answer is no, that is the result. No strategy will be promoted to demo or
described as profitable.

## 2. Data

- Instrument: XAUUSD, MetaQuotes-Demo feed, H1 bars.
- Source: MT5 terminal `copy_rates_range`, exported to `data/store/v5/`.
- Timestamps: UTC, verified against the audited v4 M5 parquet (100% OHLC match on
  all overlapping H1 buckets).
- Development: 2008-01-01 .. 2018-12-31.
- Validation (OOS): 2019-01-01 .. 2022-12-31.
- Sealed holdout: 2023-01-01 .. 2026-10-22. Never loaded by the search harness.
- Warmup: data from 2007-01-01 used only to initialize indicators.
- Bars are used as-is; missing bars are not forward-filled. Indicators use completed
  bars only. Signals at bar close `i`, fills at open `i+1`.

Data-quality gates: no duplicate timestamps, no invalid OHLC rows, no negative/zero
prices. Gaps and outlier bars are reported, not silently repaired.

## 3. Execution and cost model

- One position at a time, no pyramiding, no partial fills.
- Entry at next bar open after a signal.
- Initial stop at `entry ± stop_atr * ATR14(signal bar)` (Wilder ATR).
- Target in R multiples of the initial stop, or ATR trailing stop (family-specific).
- Stops/targets checked on bar high/low. If both are touched in one bar, **stop first**.
- Gap through a stop: fill at the bar open, not at the stop level.
- Trailing stop is updated at bar close and becomes active on the next bar.
- Time exits fill at the next bar open.
- Costs: half-spread at entry and exit from the bar `spread` column, 0.5 point
  slippage per side, USD 7 per lot round-turn commission (USD 3.5 per side),
  MT5 swap (mode=points, long -12.6, short -4.6, triple on Wednesday) applied at
  the 21:00 UTC rollover for each night held.
- Research P&L is in R (initial risk units). Account simulation uses 0.25% risk per
  trade on a USD 100,000 demo account; lot size is rounded down to 0.01.
- Cost stress multiplies spread, slippage, commission and swap by 1.5.

These are assumptions, not broker-certified costs. The MT5 Strategy Tester run
will use the terminal's own swap/commission model and must reconcile within the
tolerance in section 7.

## 4. The 50 pre-registered trials

Five economically motivated families, ten configurations each. Every trial uses
the same cost and execution model. No additional parameter sets may be added.

### F1 `donchian_trail` — breakout trend following

Long when `close[i] > max(high[i-N:i]) + buffer*ATR[i]`; short symmetric.
Initial stop `stop_atr*ATR`; optional trailing stop `highest_close - trail_atr*ATR`
(lowest close for shorts). Max hold 240 bars. ATR period 14.

| ID | N | stop_atr | trail_atr | buffer |
|---|---:|---:|---:|---:|
| dc01 | 50 | 2.0 | 3.0 | 0.0 |
| dc02 | 50 | 3.0 | 3.0 | 0.1 |
| dc03 | 100 | 2.0 | 3.0 | 0.0 |
| dc04 | 100 | 3.0 | 4.0 | 0.1 |
| dc05 | 200 | 2.0 | 4.0 | 0.0 |
| dc06 | 200 | 3.0 | 4.0 | 0.1 |
| dc07 | 100 | 2.0 | 2.0 | 0.0 |
| dc08 | 100 | 3.0 | 3.0 | 0.0 |
| dc09 | 50 | 2.5 | 4.0 | 0.1 |
| dc10 | 200 | 2.5 | 3.0 | 0.0 |

### F2 `ema_pullback` — trend continuation

`fast` and `slow` EMAs (Wilder EMA on close). Trend long when `EMA_fast > EMA_slow`.
`pullback` mode: enter long when close crosses from below to above `EMA_fast` while
the trend is long; short symmetric. `crossover` mode: enter on `EMA_fast` crossing
`EMA_slow`. Initial stop `stop_atr*ATR`; trailing `trail_atr*ATR`. Exit on the
opposite trend crossover, stop, trail, or 240-bar max hold.

| ID | fast | slow | stop_atr | trail_atr | entry |
|---|---:|---:|---:|---:|---|
| em01 | 20 | 100 | 2.0 | 3.0 | pullback |
| em02 | 50 | 200 | 2.5 | 4.0 | pullback |
| em03 | 50 | 100 | 2.0 | 3.0 | pullback |
| em04 | 20 | 50 | 1.5 | 2.5 | pullback |
| em05 | 100 | 200 | 3.0 | 4.0 | pullback |
| em06 | 20 | 100 | 3.0 | 3.0 | pullback |
| em07 | 50 | 200 | 2.0 | 3.0 | crossover |
| em08 | 50 | 100 | 2.5 | 3.5 | crossover |
| em09 | 20 | 100 | 2.0 | 4.0 | crossover |
| em10 | 100 | 200 | 2.0 | 3.0 | crossover |

### F3 `asian_range_breakout` — London session expansion

Asian range = high/low of bars 01:00-06:00 UTC. Entry window 07:00-12:00 UTC,
one trade per day. Long when close breaks `range_high + buffer*ATR`; short below
`range_low - buffer*ATR`. Initial stop `stop_atr*ATR`; either fixed R target or
ATR trailing stop. All positions are closed at the configured UTC hour (16 or 20).

| ID | buffer | stop_atr | exit | target/trail |
|---|---:|---:|---:|---|
| ar01 | 0.0 | 1.5 | 16 | 2.0R target |
| ar02 | 0.0 | 2.0 | 20 | 2.0R target |
| ar03 | 0.1 | 1.5 | 16 | 2.0R target |
| ar04 | 0.1 | 2.0 | 20 | 2.0R target |
| ar05 | 0.0 | 2.0 | 16 | 1.5R target |
| ar06 | 0.1 | 2.5 | 20 | 1.5R target |
| ar07 | 0.0 | 2.0 | 20 | 2.5 ATR trail |
| ar08 | 0.1 | 2.0 | 20 | 2.5 ATR trail |
| ar09 | 0.0 | 2.5 | 20 | 3.0R target |
| ar10 | 0.1 | 1.5 | 16 | 2.0 ATR trail |

### F4 `lowvol_meanrev` — range mean reversion

`z = (close - SMA_N) / STD_N`. Long when `z < -z_entry`; short when `z > z_entry`,
only when ATR14/close percentile rank over the prior 100 bars is below `vol_cap`.
Exit when the z-score crosses zero, at stop, or at max hold. No fixed target.

| ID | N | z_entry | stop_atr | max_hold | vol_cap |
|---|---:|---:|---:|---:|---:|
| mr01 | 20 | 2.0 | 2.0 | 24 | 0.5 |
| mr02 | 20 | 2.5 | 2.0 | 24 | 0.5 |
| mr03 | 50 | 2.0 | 2.0 | 48 | 0.6 |
| mr04 | 50 | 2.5 | 2.5 | 48 | 0.6 |
| mr05 | 100 | 2.0 | 2.5 | 48 | 0.6 |
| mr06 | 100 | 2.5 | 3.0 | 72 | 0.6 |
| mr07 | 20 | 2.0 | 1.5 | 24 | 0.4 |
| mr08 | 50 | 2.5 | 2.0 | 48 | 0.4 |
| mr09 | 50 | 2.0 | 3.0 | 72 | 0.7 |
| mr10 | 100 | 2.0 | 2.0 | 48 | 0.5 |

### F5 `squeeze_breakout` — volatility expansion

Bollinger Bands `SMA_N ± 2*STD_N`. `bandwidth = (upper-lower)/SMA`. Squeeze when
bandwidth percentile rank over the prior `sq_lookback` bars is at or below
`sq_pct`. Enter long when close crosses above the upper band while a squeeze was
present in the previous bar; short symmetric. Initial stop `stop_atr*ATR`; exit at
target R or ATR trailing stop; max hold 72 bars.

| ID | N | sq_lookback | sq_pct | stop_atr | exit |
|---|---:|---:|---:|---:|---|
| sq01 | 20 | 100 | 0.20 | 1.5 | 2.0R target |
| sq02 | 20 | 100 | 0.20 | 2.0 | 2.5 ATR trail |
| sq03 | 20 | 200 | 0.20 | 1.5 | 2.0R target |
| sq04 | 50 | 100 | 0.20 | 2.0 | 2.0R target |
| sq05 | 50 | 200 | 0.20 | 2.0 | 3.0 ATR trail |
| sq06 | 20 | 100 | 0.30 | 1.5 | 2.0R target |
| sq07 | 50 | 100 | 0.30 | 2.0 | 2.0R target |
| sq08 | 20 | 200 | 0.30 | 2.0 | 2.5 ATR trail |
| sq09 | 50 | 200 | 0.30 | 2.5 | 3.0R target |
| sq10 | 20 | 100 | 0.20 | 2.5 | 3.0R target |

## 5. Evaluation

- Stability folds: calendar years 2017-2022 (6 folds), trades assigned by entry year,
  entries skipped in the first 24 bars of each fold (embargo). Fixed parameters mean
  there is no train/test fitting; folds test stability, not optimization.
- Primary OOS window: 2019-2022.
- Metrics: trade count, mean net R, total net R, win rate, profit factor, Sharpe
  (per trade, annualized by trade frequency), max drawdown in R, average hold,
  exposure, yearly results, volatility-tercile contribution.

## 6. Pass gates (all required)

1. OOS trades >= 100.
2. OOS mean net R > 0 and 95% bootstrap CI lower bound > 0 (10,000 resamples).
3. At least 4 of 6 stability folds have positive net R.
4. Deflated Sharpe Ratio >= 0.95 using 50 trials.
5. Probability of Backtest Overfitting (CSCV, 6 folds, C(6,3)=20 splits) <= 0.30.
6. Cost-stress 1.5x: OOS mean net R still > 0.
7. Parameter perturbation +/-20%: every variant has OOS mean net R > 0, and the
   median variant retains >= 70% of base OOS mean net R.
8. No single ATR-tercile contributes > 60% of total positive net R, and at least
   3 of 4 OOS years have positive net R.
9. Random-entry null: OOS mean net R exceeds the 95th percentile of 500 random-entry
   simulations matched on trade count, direction mix, stop-distance distribution and
   holding period.
10. OOS max drawdown <= 25R.

Among passers, rank by OOS mean net R. If none pass, the search ends with a documented
no-edge result and the holdout remains sealed.

## 7. Sealed holdout and MT5

- The holdout is run once, only after a candidate is frozen and the gates above pass.
- Holdout pass: >= 100 trades, mean net R > 0 with bootstrap CI lower > 0, max DD <= 25R.
- Then implement the exact frozen rules in MQL5 and run the MT5 Strategy Tester on
  the same OOS and holdout periods, demo account only.
- Cross-check tolerance: trade count +/-10%, net P&L +/-15%, max DD +/-20%.
  Larger divergence requires reconciliation before any demo use.
- If the holdout fails, the candidate is rejected; no re-tuning against the holdout.

## 8. Demo phase

A passing candidate goes to MT5 demo for at least 56 calendar days and 100 trades
at 0.25% risk. Demo is not live trading. No live code path will be created.

## 9. What would stop the project

- No candidate passes the gates: publish the negative result.
- Holdout failure: publish it; do not search the holdout for a better variant.
- MT5 reconciliation failure: no demo deployment until resolved.
- Data-quality failure: no testing until the dataset is fixed.

## 10. Honest prior

Most of these 50 configurations will lose after costs. A positive result that only
appears after many unregistered variations is not a result. The registry records every
trial, including failures.
