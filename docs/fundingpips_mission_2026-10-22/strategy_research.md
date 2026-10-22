# Strategy Research — XAUUSD, FundingPips $5,000

Date: 2026-10-22/23 (UTC). Branch: `research/fundingpips-2026`.
Environment labels: `HISTORICAL_REAL` for market data, `BACKTEST` for simulations.

## 1. Scope and decision rule

This mission evaluated the 50 XAUUSD H1 configurations that were frozen in the v5
pre-registration (`docs/PRE_REGISTRATION_SWING_V5.md`) and already searched once.
The parameters were **not** re-tuned. The FundingPips pre-registration
(`docs/PRE_REGISTRATION_FUNDINGPIPS_2026-10-22.md`) added a challenge-path overlay
on the frozen v5 trade log; it is not a new strategy search.

The decision rule was fixed before the challenge run: a configuration can be called
`DEMO-READY` only if it (1) passed every frozen v5 gate, (2) has positive
independent-validation expectancy with a 95% bootstrap lower bound above zero,
(3) shows acceptable firm-breach risk in the challenge simulation, and (4) passes
the risk/demo integration tests. Condition 1 fails for all 50 trials, so the
expected and actual outcome is `NO_VALIDATED_CANDIDATE`.

A FundingPips pass frequency is **not** evidence of edge. It is a path statistic
conditional on a strategy that already failed its expectancy gates.

## 2. Data and validation design

- 64,651 real XAUUSD H1 bars, 2012-01-03..2022-12-30 UTC, SHA-256
  `5dbace90...e267e`, from the MetaQuotes-Demo server; 0% invalid OHLC,
  duplicates, missing values, or missing volume in 2013-2022.
- Development 2013-2018; selection/validation 2019-2022. The 2019-2022 window was
  used to select among trials, so it is selection data, not clean OOS.
- The sealed 2023-2026 holdout was **never opened**. The lock file still reports
  `candidate_frozen: false` and an empty access log.
- Costs: observed spread with a 10-point floor and causal median fallback, 0.5
  point slippage per side, USD 7/lot round-turn commission, MT5 swap
  (-12.6/-4.6 points, triple Wednesday). Mean modelled cost was 0.023-0.031R per
  trade plus 0.012-0.020R swap.
- Execution: signal at bar close, next-bar-open fill, stop-first when both
  barriers are touched, gaps filled at the open, one position at a time,
  trailing stops updated at bar close.
- Not modelled: queue position, partial fills, requotes, rejects, market impact,
  broker-specific slippage distributions, news spreads beyond the observed H1
  spread series. Demo fills would be cleaner than a real account's fills.

## 3. Strategy families and hypotheses

All five families are causal, single-instrument, long/short, one-position-at-a-time
H1 rules with ATR-based stops. The families were chosen to cover trend, pullback,
session breakout, mean reversion, and volatility compression, not because any had
prior XAUUSD evidence.

| Family | IDs | Hypothesis | Pre-registered rule shape |
|---|---|---|---|
| Donchian trailing breakout | `dc01`-`dc10` | XAUUSD trends persist enough for N-bar channel breakouts with ATR trails to beat costs. | Entry on close beyond N-bar high/low with optional buffer; stop 2.0-3.0 ATR; trail 2.0-4.0 ATR. |
| EMA pullback / crossover | `em01`-`em10` | Trend continuation after a pullback to a fast/slow EMA is higher quality than raw breakout. | Fast 20-100, slow 50-200; pullback or crossover entry; stop 1.5-3.0 ATR; trail 2.5-4.0 ATR. |
| Asian-range breakout | `ar01`-`ar10` | The Asian session range predicts London/NY directional expansion. | Break of the Asian range with optional buffer; stop 1.5-2.5 ATR; fixed target 1.5-3.0R or 2.0-2.5 ATR trail; time exit 16:00/20:00. |
| Low-volatility mean reversion | `mr01`-`mr10` | Quiet-regime deviations from a rolling mean revert after costs. | Z-score entry 2.0-2.5 with a volatility cap; stop 1.5-3.0 ATR; max hold 24-72 bars. |
| Squeeze breakout | `sq01`-`sq10` | Volatility compression precedes directional expansion. | Narrow-range percentile over 100-200 bars; breakout after N-bar extreme; stop 1.5-2.5 ATR; fixed target or ATR trail. |

Ten parameter sets per family were frozen before the first test, for exactly 50
trials. Every trial, including failures, is recorded in
`experiments/registry_v5.jsonl` (51 records: 50 trials plus the search summary).

## 4. Results — development and validation

Per-family summary over the ten configs, 2019-2022 validation:

| Family | Configs | OOS trades (sum) | Best mean net R | Median mean net R | Worst mean net R | Configs passing G2/G4 |
|---|---:|---:|---:|---:|---:|---:|
| Donchian trailing | 10 | 2,928 | +0.3131 (`dc05`) | +0.1018 | +0.0112 | 0 / 10 |
| EMA pullback | 10 | 2,890 | +0.2085 (`em08`) | +0.0339 | -0.0869 | 0 / 10 |
| Asian-range breakout | 10 | 7,653 | +0.0328 (`ar08`) | -0.0193 | -0.0479 | 0 / 10 |
| Low-vol mean reversion | 10 | 3,184 | +0.0209 (`mr05`) | -0.0775 | -0.2035 | 0 / 10 |
| Squeeze breakout | 10 | 4,176 | +0.0410 (`sq08`) | -0.0144 | -0.0632 | 0 / 10 |

`G2` is "OOS mean net R > 0 with 95% bootstrap CI lower bound > 0"; `G4` is
"Deflated Sharpe Ratio >= 0.95 with 50 trials". All 50 configs failed both.

Best raw configuration per family:

| ID | Rule | OOS n | Mean net R | 95% CI | DSR | Positive folds | Max DD (R) |
|---|---|---:|---:|---|---:|---:|---:|
| `dc05` | Donchian 200 / 2.0 ATR stop / 4.0 ATR trail | 174 | +0.3131 | [-0.0586, +0.7365] | 0.411 | 3/6 | 21.8 |
| `em08` | EMA 50/100 crossover, 2.5 ATR stop, 3.5 ATR trail | 108 | +0.2085 | [-0.1434, +0.6211] | 0.363 | 4/6 | 12.2 |
| `ar08` | Asian range + 0.1 buffer, 2.0 ATR stop, trail 2.5, exit 20:00 | 740 | +0.0328 | spans zero | 0.002 | 3/6 | 28.4 |
| `mr05` | 100-bar z-score 2.0, 2.5 ATR stop, 48-bar hold | 242 | +0.0209 | spans zero | 0.039 | 3/6 | 22.2 |
| `sq08` | 200-bar squeeze 30%, 2.0 ATR stop, 2.5 ATR trail | 601 | +0.0410 | spans zero | 0.005 | 3/6 | 25.9 |

The apparently strong trend results are not robust:

- `dc05`'s +0.313R is a 2020 gold-trend artifact. Its yearly totals were 2019
  +17.8R, 2020 +51.3R, 2021 -2.1R, 2022 -12.6R. It fails fold and year coverage.
- `em08` is the most coherent config: positive in all four OOS years, 4/6 positive
  folds, 12.2R max drawdown, +0.209R mean. Its CI still spans zero and DSR is 0.36.
  It is **not** evidence of an edge at the pre-registered bar and is not promoted.
- The high-trade-count breakout families (`ar`, `sq`) have means indistinguishable
  from zero after costs. `ar07`/`ar08` have DSR ~0.001; their positive means are
  economically negligible and statistically unresolved.
- All mean-reversion configs are negative or indistinguishable from zero.

Multiple-testing diagnostics: PBO via CSCV = **0.550** (limit 0.30), meaning the
best in-sample config ranked worse than median out of sample in the majority of
splits. Only 7 of 50 configs even reached the +/-20% perturbation gate, and only
after earlier gates had already failed; the gate is reported as not passed, not
cleared. No config reached the random-entry null gate.

## 5. Challenge-path overlay

The two-phase FundingPips simulation (165,600 attempts, 50 configs × 3 risk
fractions × 4 scenarios × 3 cost cases × 92 rolling starts) reuses the frozen
trade log. Full mechanics are in `backtest_methodology.md`; the interpretation is
here.

At the primary decision case (5% daily limit, 3 minimum days, Phase 2 reset to
$5,000, $10 round-turn commission):

| Risk per trade | Both-phase passes / 4,600 | Pass rate | Soft guard | Inactivity | Incomplete | Firm breaches |
|---|---:|---:|---:|---:|---:|---:|
| 0.10% | 0 | 0.00% | 0.00% | 100.00% | 0.00% | 0 |
| 0.25% | 0 | 0.00% | 12.85% | 57.91% | 29.24% | 0 |
| 0.50% | 100 | 2.17% | 72.11% | 13.57% | 12.15% | 0 |

Key observations:

1. **At 0.10% risk the $5,000 account cannot trade.** The minimum 0.01 lot with a
   typical 2 ATR XAUUSD stop risks more than the per-trade budget, so every trade
   is skipped and every attempt dies of inactivity. This is a hard broker/account
   constraint, not a simulator artifact. Across all 50 configs at 0.50% risk,
   3,003 trades were skipped because 0.01 lots exceeded the approved budget.
2. **Passes are concentrated in two configs.** At 0.50% risk, 7 of 50 configs
   produced any pass: `ar07` 33/92 (35.9%), `ar08` 31/92 (33.7%), `dc02` 14/92
   (15.2%), `ar09` 8/92, `ar10` 7/92, `dc08` 4/92, `dc07` 3/92. The median config
   pass rate is 0. Only 7/50 had any pass at all. These are the same configs whose
   OOS means are near zero and whose DSR is ~0. This is selection among 50
   challenge paths, not an edge.
3. **Most attempts end on the internal soft guards, not the firm's limits.** Zero
   firm breaches at 0.50% risk is a property of the 1%/4% internal overlay, which
   stops trading before the 5%/10% firm limits. With the overlay disabled
   (`firm_rules_only`), 74/4,600 attempts breach (1.61%) and 303 pass (6.59%) --
   more passes and real breaches, confirming the overlay trades pass probability
   for survival. Neither number is a reason to deploy.
4. **Pass times are long.** Median days to pass at 0.50% risk is 675.8 calendar
   days (p95 max drawdown 8.29%). This is not a quick challenge pass; it is a
   multi-year path that depends on the 2019-2022 sample.
5. **Costs matter.** At 0.50% risk, the $10 round-turn vs $5 case moves pass rate
   from 2.17% to 2.67%; 1.5x cost stress cuts it to 0.91% (42/4,600). A strategy
   whose pass frequency halves under a 1.5x cost stress is not robust.
6. **The 3% daily-limit variant is not distinguishable** from the 5% primary case
   at 0.50% risk, because the 1% internal guard binds first. The stricter firm
   rule is irrelevant when trading stops at 1%.

The pre-registered conclusion follows: **no configuration is DEMO-READY.** The
challenge pass rates quantify the risk of deploying a non-edge strategy; they do
not create a candidate.

## 6. Regime classifier decision

The mission allowed a causal regime classifier "only if it earns its complexity."
It was **not built**, for three reasons:

1. There is no validated base strategy to condition. A regime filter can only
   scale or veto a signal; with no signal that clears G2/G4, a classifier would
   add parameters and multiple-testing burden without a testable benefit.
2. A regime model would need its own pre-registered trial budget, filtered
   (causal) probabilities only, and a leakage audit. Adding it after seeing the
   challenge results would be post-hoc tuning of the same 2019-2022 data.
3. The v5 results already show the failure is not "wrong regime" but "no
   after-cost edge at this sample size." Conditioning noise does not fix that.

A future program may add a regime layer only with new data, a new pre-registration,
and its own trial count. It is not implemented here and must not be added to the
current 50-trial program.

## 7. What is real, simulated, and unverified

- **Real:** XAUUSD H1 OHLC/volume and observed spreads from a MetaQuotes demo
  server; UTC-normalized timestamps; live broker symbol specification; MT5
  `order_calc_profit` as the sizing ground truth; live demo `order_check`
  plumbing.
- **Simulated:** the v5 fills, costs, swap, and every challenge path. The
  simulator uses H1 bid OHLC with spread marks, not tick-level FundingPips fills.
- **Unverified:** FundingPips swap schedule, commission side basis, winter daily
  reset offset, FundingPips demo/eval credentials, and personal-EA approval. Any
  of these could change the challenge numbers; none can turn a failed expectancy
  gate into evidence of edge.
- **Could invalidate the research conclusion:** a coding error in the frozen v5
  cost/spread series; a different broker's costs; a structural change in XAUUSD;
  or a real, small edge that this sample is too short to resolve. The correct
  statement is "no edge was demonstrated at the required confidence," not "an
  edge is proven absent."

## 8. Recommendation

Do not deploy any of these 50 configurations to a FundingPips evaluation. Do not
purchase an evaluation on the strength of the simulated pass rates. A future
program needs a genuinely distinct, pre-registered hypothesis class, new data,
and its own trial budget; the sealed 2023-2026 holdout remains available for such
a program but must not inform its design.
