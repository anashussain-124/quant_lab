# Backtest and Challenge-Simulation Methodology

Environment: `HISTORICAL_REAL` for price data; `BACKTEST` for all fills, sizing,
challenge accounts, and rule checks. No result here is a live or demo fill.

## 1. What was tested, and what was not

This mission did **not** run a new strategy search. It reuses the 50
pre-registered v5 XAUUSD H1 configurations exactly as frozen on 2026-10-22.
The only new quantitative work is a challenge-path simulation that sizes those
frozen trades under FundingPips rules.

The v5 search itself is documented in `docs/PRE_REGISTRATION_SWING_V5.md`,
`docs/PRE_REGISTRATION_SWING_V5_AMENDMENT_1.md`, and `docs/V5_RESULT.md`.

## 2. Data

| Item | Value |
|---|---|
| Instrument | XAUUSD |
| Timeframe | H1 |
| Rows | 64,651 |
| Span | 2012-01-03 01:00 .. 2022-12-30 23:00 UTC |
| Source | MT5 demo server (`HISTORICAL_REAL`) |
| SHA-256 | `5dbace90244b3b7e422890ef285b29f8e3f20591266ae2db641ce60bb82e267e` |
| Development | 2013-2018 |
| Selection / OOS | 2019-2022 |
| Sealed holdout | 2023-01-01 .. 2026-10-22, **not accessed** |

Data-quality checks: invalid OHLC 0%, zero/negative prices 0%, duplicate
timestamps 0%, missing values 0%, missing volume 0%. Gate threshold <1% each.
PASS.

The v5 trade log is the frozen bridge between the search and the challenge
simulator:

| Item | Value |
|---|---|
| Rows | 51,791 |
| Configs | 50 |
| SHA-256 | `5384578e490df6f4ce4947e13fd07a722d4e71c825e489c1891495b138e2a270` |
| Identity check | `net_r = gross_r - cost_r + swap_r` exact |

The challenge simulator refuses to run if either hash changes.

## 3. v5 execution model

- Signal is generated on a **closed** H1 bar.
- Entry is filled at the **next bar open**, never at the signal bar close.
- Stops are checked before targets inside each bar (stop-first tie-break).
- A gap through the stop fills at the open, not at the stop price.
- One position at a time; trailing stops update at bar close.
- No pyramiding, no overlapping positions for one config.
- Times are UTC-aware; the daily boundary is handled separately by the
  challenge simulator.

Costs:

- Observed spread where present, with a 10-point floor and a causal median
  fallback;
- 0.5 point slippage per side;
- USD 7/lot round-turn commission in v5;
- MT5 swap points, with triple Wednesday;
- 1.5x stress applied to spread, slippage, commission, and swap.

Limitations that remain: no queue position, partial fills, requotes, rejects,
exchange throttles, or market impact. H1 bars cannot resolve intrabar path;
stop-first is an assumption, not observed sequencing.

## 4. Parameter search and multiple testing

The 50 configurations were frozen before any test: five families
(`donchian_trail`, `ema_pullback`, `asian_range_breakout`, `lowvol_meanrev`,
`squeeze_breakout`) × ten parameter sets. They are recorded in
`experiments/registry_v5.jsonl`; every trial counts toward multiple-testing
correction, including losing ones.

Gates, pre-registered and unchanged after results:

| Gate | Requirement |
|---|---|
| G1 | OOS trades >= 100 |
| G2 | OOS mean net R > 0 with 95% bootstrap CI lower bound > 0 |
| G3 | At least 4 of 6 test folds positive |
| G4 | Deflated Sharpe Ratio >= 0.95 with 50 trials |
| G5 | CSCV PBO <= 0.30 |
| G6 | Positive under 1.5x all-in cost stress |
| G7 | +/-20% parameter perturbation retains >=70% of net performance |
| G8 | No single ATR-tercile/year regime contributes >60% of PnL |
| G9 | Random-entry null with matched turnover produces ~zero edge |
| G10 | Max drawdown <= 25R |

Result: every trial failed G2 and G4; PBO was 0.550; no candidate reached G7 or
G9, so those gates are reported as **not passed**, not as evaluated-and-cleared.
The sealed holdout was not opened.

## 5. Challenge simulation

### 5.1 Purpose

The simulator answers a narrower question: if the frozen v5 trades had been
sized on a $5,000 FundingPips 2 Step Standard account, what would the pass,
breach, and near-breach frequencies have been? A pass is **not** evidence of
edge and cannot override a failed v5 gate.

### 5.2 Frozen mechanics

- Account: $5,000. Phase 1 target 8%, Phase 2 target 5%.
- Overall floor: $4,500 static; equity or balance touching it is a breach.
- Daily floor: 5% of that day's reference, where reference is
  `max(opening balance, opening equity)`.
- Reset: 21:00 UTC (the unresolved winter DST offset is a caveat, not a
  sensitivity that changes the verdict).
- Floating P&L is marked on every H1 bar using the worst intrabar price for the
  position direction; shorts pay the observed spread.
- A phase passes only when realized balance reaches the target and the minimum
  distinct trading days with a closed trade is met.
- If the target is met early, trading continues under the same rules until the
  minimum days are satisfied or the attempt fails.
- A 30-calendar-day gap without a closed trade is `FAIL_INACTIVITY`.
- Data ending before an outcome is `INCOMPLETE`, never a pass.
- Phase 2 starts at $5,000; Phase 1 profit is not carried in the primary case.
  A carry-Phase-2 sensitivity is reported separately and is not used to pick a
  winner.

### 5.3 Sizing

Risk per trade is a fraction of current equity at entry, capped by daily and
overall room:

```
budget = min(risk_fraction * equity_at_entry, daily_room, overall_room)
raw_lots = budget / (stop_distance * 100)
lots = floor(raw_lots / 0.01) * 0.01
```

If the minimum 0.01 lot would risk more than the budget, the trade is skipped
and counted. The simulator never rounds up. Risk cases: 0.10%, 0.25%, 0.50%.
Internal soft guards: 1% daily and 4% overall of starting balance. Hitting one
ends the attempt as `SOFT_GUARD`; it is not reported as a firm breach.

### 5.4 Scenarios and cost cases

Primary decision case: 5% daily, 3 minimum days, internal guards on, $10
round-turn FundingPips commission, Phase 2 reset to $5,000.

Sensitivities (reported, never used to select a candidate):

- 3% daily, 0 minimum days;
- carry Phase 1 ending balance into Phase 2;
- firm-rules-only (internal 1%/4% guards disabled);
- $5 round-turn commission;
- 1.5x all-in cost stress.

Rolling starts: every 14 calendar days from 2019-01-01 to 2022-07-01, requiring
at least 180 future days. There are 92 starts per cell. Attempts overlap and are
therefore **not independent**; no confidence interval is claimed as if they
were independent samples.

Matrix size: 50 configs × 3 risk fractions × 4 scenarios × 3 cost cases × 92
starts = **165,600 attempts** (1,800 cells).

## 6. What the metrics mean

- `both_phase_pass_rate`: share of attempts that reach the Phase 2 target
  without a firm breach, soft guard, inactivity failure, or incomplete data.
- `firm_breach_rate`: daily or overall hard-limit breach. A breach is a fail.
- `soft_guard_rate`: internal risk stop triggered before the firm limit.
- `near_*`: came within 1% of the relevant hard limit without breaching.
- `incomplete_rate`: history ended before an outcome. Never counted as a pass.
- `skipped_min_lot`: trades where 0.01 lots exceeded the risk budget. This is a
  material constraint on a $5,000 account with wide XAUUSD stops.
- `median_days_to_pass`: only over passed attempts; it is conditional and not a
  survival estimate.

## 7. Reproducibility

The challenge report stores:

- data and trade-log hashes;
- config file SHA-256;
- Python, NumPy and pandas versions;
- simulator source SHA-256 and git commit;
- all 165,600 attempt rows and per-cell aggregates;
- explicit caveats, including that a simulated pass is not a FundingPips pass.

Run:

```bash
python research/challenge_v5.py \
  --config configs/fundingpips.yaml \
  --out reports_fundingpips/challenge_2026-10-22 \
  --workers 8
```

## 8. What would invalidate the conclusions

- A hash mismatch or a change to the frozen trade log.
- A coding error in daily reset, daily reference, or floating-equity marking.
- A FundingPips rule change or a different commission/swap schedule.
- A different broker's XAUUSD spread, swap, or execution quality.
- The daily reset being UTC+2 rather than UTC+3 in the tested winter dates.
- Any use of the 2023-2026 holdout to choose among these 50 configs.

## 9. Honest interpretation

The simulation measures the **risk of deploying a non-edge strategy**, not a
route to a validated edge. It cannot turn a failed expectancy gate into a
promotion. The correct reading of a high pass rate would be "this path was
lucky under these assumptions", not "the strategy works".
