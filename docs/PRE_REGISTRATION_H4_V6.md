# Pre-Registration — H4 Gold Trend Program v6

Status: **FROZEN before any v6 strategy backtest.**
Date: 2026-10-23.
Owner: the user asked to "optimize it for funding pips or make another strategy
for this to reach and complete this target." This document is the second path:
a new, separately counted research program. It does not revise v5.

Environment label: `HISTORICAL_REAL` data, `BACKTEST` simulation. No demo or
live order is part of this program.

## 1. Question

Does a slower H4 Donchian breakout on XAUUSD, optionally gated by a causal
volatility-regime filter, have positive out-of-sample expectancy after
FundingPips-level costs — and if it does, can the frozen FundingPips 2-Step
simulation pass the challenge at a risk fraction that respects the firm's
daily and overall loss limits?

If the answer is no, that is the result. No configuration will be promoted,
described as profitable, or sent to a broker.

**Honest prior.** Most trend configurations fail after costs. Gold's 2019–2020
trend is unusually favourable and can dominate a 2019–2022 sample. H4 produces
few trades, so statistical power is lower than in v5. The v5 program already
tested 50 H1 configurations and found no edge; this program is a long shot, not
a fix.

## 2. Relationship to v5 and multiple-testing disclosure

- No v5 parameter is changed, re-run, or re-interpreted. The v5 verdict
  `NO_EDGE_FOUND` stands.
- v6 uses a different input timeframe, a new 24-configuration grid, and a new
  registry (`experiments/registry_v6.jsonl`). It is a separate program.
- The H4 grid was motivated in part by the v5 observation that the best raw
  H1 result came from the longest Donchian lookback (`dc05`, N=200 H1 ≈ 8
  trading days), which failed the v5 gates. The H4 N=50 configuration covers a
  similar economic horizon with fewer, cleaner observations. This lineage is
  disclosed; it does not remove any configuration from the grid, and every
  result is reported.
- **DSR uses all 74 trials**: the 50 frozen v5 trial Sharpes imported from
  `reports_v5/search_2026-10-22/report.json` plus the 24 v6 trial Sharpes.
  Searching twice is part of the trial history.

## 3. Data

- Instrument: XAUUSD, MetaQuotes-Demo feed.
- H4 frame derived deterministically by
  `research.data_v5.resample_h4` from the frozen v5 H1 dev/validation parquet:
  - H1 parent: `data/store/v5/xauusd_h1_dev_validation.parquet`,
    SHA-256 `5dbace90244b3b7e422890ef285b29f8e3f20591266ae2db641ce60bb82e267e`.
  - H4 output: `data/store/v6/xauusd_h4_dev_validation.parquet`,
    SHA-256 `3cdfee22323aed688424460c0f5b63c323c68033a973328f29aa4e3a7b51b38c`.
  - 16,944 bars, 2012-01-03 00:00 to 2022-12-30 20:00 (label timezone UTC,
    inherited from the H1 export). Zero duplicate timestamps, zero invalid
    OHLC rows, exact H1-to-H4 OHLC reconstruction (16,944/16,944 bars).
  - H4 spread is the maximum of the four H1 spread samples, a conservative
    choice. Zero-spread bars use the causal median fallback in the cost model.
  - **Timezone caveat:** the frozen H1 labels are broker-server time recorded
    as UTC (observed UTC+3 on MetaQuotes-Demo). v6 uses no session-dependent
    rule, so only H4 bucket boundaries are affected. This must be reconciled
    in the MT5 Strategy Tester before any demo use.
- Development: 2012 used only for indicator warm-up; 2013-01-01 .. 2018-12-31
  descriptive only. **No configuration is selected or dropped using dev
  results.**
- Validation (OOS): 2019-01-01 .. 2022-12-31. This is the gate window.
- Sealed holdout: 2023-01-01 .. 2026-10-22. It is not loaded by the v6 harness.
  It will be derived from the sealed v5 H1 holdout only after a candidate is
  frozen, and every access is logged.
- Missing bars are not forward-filled. Indicators use completed bars only;
  signals at H4 bar close `i`, fills at H4 open `i+1`.

## 4. Execution, costs, and sizing

Identical mechanics to the frozen v5 backtester (`research/backtest_v5.py`):

- One position at a time, no pyramiding, no partial fills.
- Entry at next H4 bar open after a signal.
- Initial stop `entry ± stop_atr * ATR14` (Wilder ATR on the signal bar).
- Trailing stop `highest_close - trail_atr * ATR` for longs, symmetric for
  shorts; updated at bar close and active from the next bar.
- Stops checked on bar high/low; **stop first** if both stop and target are
  touched. Gap through a stop fills at the bar open.
- Max hold 120 H4 bars (about 20 calendar days), then exit at the next open.
- Costs: half-spread at entry and exit from the H4 `spread` column, 0.5 point
  slippage per side, **USD 10 per lot round-turn commission** (FundingPips
  primary assumption: $5 per lot per side), swap from the v5 model applied at
  21:00 UTC with triple on Wednesday.
- Cost stress multiplies spread, slippage, commission and swap by 1.5.
- Research P&L is in R (initial risk units). Account simulation, if reached,
  uses 0.5% risk per trade on USD 5,000 with volume floored to 0.01 lots.

These costs are assumptions, not broker-certified. FundingPips' commission
side basis and swap schedule remain unverified.

## 5. The 24 frozen trials

All configurations use ATR14, a symmetric Donchian channel computed from
**prior** bars only, max hold 120 H4 bars, and a 2.0-ATR trail unless stated.
No other parameter set may be tested.

### Group A — plain H4 Donchian (`h4d`)

| ID | N (H4 bars) | stop_atr | trail_atr | vol filter |
|---|---:|---:|---:|---|
| h4d01 | 20 | 1.5 | 2.0 | none |
| h4d02 | 20 | 1.5 | 3.5 | none |
| h4d03 | 20 | 2.5 | 2.0 | none |
| h4d04 | 20 | 2.5 | 3.5 | none |
| h4d05 | 50 | 1.5 | 2.0 | none |
| h4d06 | 50 | 1.5 | 3.5 | none |
| h4d07 | 50 | 2.5 | 2.0 | none |
| h4d08 | 50 | 2.5 | 3.5 | none |
| h4d09 | 100 | 1.5 | 2.0 | none |
| h4d10 | 100 | 1.5 | 3.5 | none |
| h4d11 | 100 | 2.5 | 2.0 | none |
| h4d12 | 100 | 2.5 | 3.5 | none |

### Group B — H4 Donchian with volatility-regime filter (`h4v`)

Same grid, plus: trade only when the causal percentile rank of `ATR14/close`
over the prior 100 H4 bars is **≥ 0.50**. The rank uses the current value
against prior values only.

| ID | N (H4 bars) | stop_atr | trail_atr | vol filter |
|---|---:|---:|---:|---:|
| h4v01 | 20 | 1.5 | 2.0 | rank >= 0.50 |
| h4v02 | 20 | 1.5 | 3.5 | rank >= 0.50 |
| h4v03 | 20 | 2.5 | 2.0 | rank >= 0.50 |
| h4v04 | 20 | 2.5 | 3.5 | rank >= 0.50 |
| h4v05 | 50 | 1.5 | 2.0 | rank >= 0.50 |
| h4v06 | 50 | 1.5 | 3.5 | rank >= 0.50 |
| h4v07 | 50 | 2.5 | 2.0 | rank >= 0.50 |
| h4v08 | 50 | 2.5 | 3.5 | rank >= 0.50 |
| h4v09 | 100 | 1.5 | 2.0 | rank >= 0.50 |
| h4v10 | 100 | 1.5 | 3.5 | rank >= 0.50 |
| h4v11 | 100 | 2.5 | 2.0 | rank >= 0.50 |
| h4v12 | 100 | 2.5 | 3.5 | rank >= 0.50 |

## 6. Evaluation

- Primary window: OOS 2019-2022, trades assigned by entry time.
- Stability folds: calendar years 2017-2022, six folds, entries skipped in the
  first 24 bars of each fold (embargo). Parameters are fixed, so folds test
  stability, not fitting.
- Metrics: trade count, mean net R, total net R, win rate, profit factor,
  per-trade Sharpe, max drawdown in R, average hold, exposure, yearly results,
  ATR-tercile contribution, cost and swap drag.
- Bootstrap: 10,000 resamples, seed 0. Null model: 500 simulations, seed 0.
- PBO: CSCV with six folds, all C(6,3)=20 splits.
- Perturbation: ±20% on every numeric parameter except the zero buffer and
  categorical variables.

## 7. Pass gates (all required)

1. OOS trades >= 100. This is retained from v5 for comparability; it is not a
   universal threshold. With a 4-year window it requires roughly one trade per
   ten trading days, which the shorter lookbacks can meet.
2. OOS mean net R > 0 and 95% bootstrap CI lower bound > 0.
3. At least 4 of 6 stability folds have positive total net R.
4. Deflated Sharpe Ratio >= 0.95 using **74 trials** (50 v5 + 24 v6).
5. Probability of Backtest Overfitting (CSCV) <= 0.30.
6. Cost stress 1.5x: OOS mean net R still > 0.
7. Parameter perturbation ±20%: every variant has OOS mean net R > 0 and the
   median variant retains >= 70% of the base OOS mean net R.
8. No single ATR tercile contributes > 60% of total positive net R, and at
   least 3 of 4 OOS years have positive net R.
9. OOS mean net R exceeds the 95th percentile of 500 random-entry simulations
   matched on trade count, direction mix, stop-distance distribution and
   holding period.
10. OOS max drawdown <= 25R.

Gate 9 is evaluated only for configurations that pass gates 1–8 and 10; a
configuration that already fails an earlier gate is reported as rejected and
is not re-tested.

### 7b. Challenge-deployment gate (only for a core-gate passer)

Run the frozen FundingPips 2-Step simulator on the candidate's validation
trades at **0.5% risk per trade**, primary scenario (5% daily loss, 10% static
overall loss, 3 minimum trading days, Phase 2 reset to USD 5,000, internal 1%
daily / 4% overall soft guards), USD 10 round-turn commission, 92 rolling
starts.

Required:
- both-phase pass rate >= 20% across the 92 starts;
- firm-rule breach rate <= 2%.

Rationale: below a 20% simulated pass rate, the expected number of challenge
attempts exceeds five and the outcome is closer to a lottery than a deployment
plan. This is a deployment-economics threshold for this program, not a
statistical universal. The 2% breach cap protects the firm's hard limits.
This overlay is computed on the same validation period used for selection, so
it is deployment evidence only — not independent proof of edge. The sealed
holdout remains the independent test.

A configuration that passes gates 1–10 but fails 7b is labelled
`EDGE_NOT_DEPLOYABLE`, not selected, and not promoted.

## 8. Selection and stopping rule

- All 24 configurations are run exactly once on the frozen v6 harness.
- Among configurations passing all gates, select the highest OOS mean net R;
  ties break on lower OOS max drawdown.
- If none pass: publish `NO_EDGE_FOUND`, keep the holdout sealed, and do not
  tune, extend, or re-run the grid.
- A code bug that invalidates results is fixed and the **entire** grid is
  re-run; the bug, fix, and re-run are recorded as an amendment. No parameter
  may change in response to results.
- Any new idea after this document is a new, separately counted program.

## 9. Sealed holdout

Only after a candidate is frozen:

- Derive H4 from the sealed v5 H1 holdout via the same resampler, log the
  access, and run the frozen candidate once.
- Holdout pass: >= 50 trades, mean net R > 0 with 95% bootstrap CI lower > 0,
  max drawdown <= 25R, and at least 2 of the 3 full holdout years positive.
  The 50-trade floor reflects ~3.8 holdout years at the minimum viable H4
  trade rate; it is not a universal threshold.
- If the holdout fails, reject the candidate. No re-tuning against the holdout.
- If it passes, implement the exact rules in MQL5 and reconcile against the
  MT5 Strategy Tester on validation and holdout: trade count ±10%, net P&L
  ±15%, max drawdown ±20%. Larger divergence blocks demo use.

## 10. Demo phase

A holdout-passing candidate goes to MT5 demo (never live) for at least 120
calendar days and at least 15 closed trades at 0.5% risk. The duration is set
to collect enough H4 trades for a fill comparison; if 15 trades are not reached
by day 180, the candidate is rejected as too sparse to validate. Demo results
are an upper bound on live execution, not proof of live viability.

## 11. What would invalidate the conclusion

- The H4 bucket labels being server time rather than UTC, if later MT5
  reconciliation shows a material difference.
- FundingPips swap or commission differing materially from the v5 assumptions.
- 2019–2022 being an unrepresentative regime for gold trend following.
- Trade counts too low for the confidence interval to be meaningful.
- Any look-ahead in the resampled bars or indicator windows.

## 12. Registry and reproducibility

Every trial stores: data hash, pre-registration hash, code commit, cost-model
version, configuration, seed, and all gate outcomes. The registry includes
failures. The sealed holdout access log is append-only.
