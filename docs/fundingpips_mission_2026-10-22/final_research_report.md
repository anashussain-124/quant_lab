# Final Research Report — FundingPips $5,000 XAUUSD Mission

Date: 2026-10-22 (UTC; 2026-10-23 local). Branch: `research/fundingpips-2026`.
Starting commit: `09ca9ab`. Environment labels: `HISTORICAL_REAL`, `BACKTEST`,
`MT5_DEMO`. No `LIVE` path exists.

## 1. Executive verdict

**Outcome B: NO VALIDATED EDGE. NO CANDIDATE IS DEMO-READY. DEPLOYMENT IS BLOCKED.**

- None of the 50 frozen v5 XAUUSD configurations passed the pre-registered
  statistical gates. All 50 failed the bootstrap-CI gate and the Deflated Sharpe
  gate; PBO was 0.550 against a 0.30 limit.
- The FundingPips challenge overlay produced simulated pass frequencies up to
  35.9% for individual configs, but only at 0.50% risk per trade and only for
  configs whose validation mean is indistinguishable from zero. A simulated pass
  is **not** a FundingPips pass and is **not** evidence of edge.
- The risk and demo-execution software passed 481 tests and a live read-only MT5
  check. The runner correctly refused to send an order: the config is disarmed,
  there is no validated candidate, the account is not a $5,000 FundingPips
  account, and terminal AutoTrading is off.
- FundingPips rules are versioned, but four critical operational details remain
  unverified and are fail-closed blockers: swap schedule, commission side basis,
  winter DST reset offset, and demo/evaluation credentials. The personal-EA
  review is also outstanding.
- **No real-money trading occurred, and no live-trading code path was created.**

## 2. Honest priors, restated

- Most ideas fail after costs. "No edge found" is a valid, valuable result.
- A backtest is a hypothesis, not evidence. Demo fills are cleaner than live
  fills and are an upper bound on viability.
- Retail-latency arbitrage is out of scope for this program; XAUUSD H1 trend and
  breakout rules are the tested class.
- A challenge pass probability is not a profitability forecast. It is a path
  statistic under a fixed rule set, conditional on a strategy that already failed
  its expectancy gates.

## 3. What was done

### 3.1 Rules

Current official FundingPips 2-Step Standard rules were retrieved and versioned
in `configs/fundingpips.yaml` (version 2026-10-22) and documented with sources in
`fundingpips_rules.md`. Verified values include the 8%/5% phase targets, static
10% overall loss, 3%/5% daily loss on the higher of opening balance/equity, 3
minimum trading days with 5% daily, unlimited time, 30-day inactivity, 21:00 UTC
reset (FAQ), XAUUSD 100 oz contract, 1:30 metals leverage, $5/lot commission
statement, 0.01 minimum volume, and the personal-EA policy.

Unresolved and treated as blockers: swap schedule, commission side basis, DST
reset conflict, FundingPips demo credentials, and EA proof-of-ownership review.

### 3.2 Research

The 50 v5 configurations (five families: Donchian trailing, EMA pullback,
Asian-range breakout, low-volatility mean reversion, squeeze breakout) were
reused with frozen parameters. No re-tuning was performed. The 2023-2026 holdout
was not opened. Full detail: `strategy_research.md`.

Key results, 2019-2022 validation:

- Every family's best config had a 95% bootstrap CI for mean net R spanning zero.
- Best raw mean: `dc05` +0.313R (n=174), but 2020-driven (+51.3R) and negative in
  2021-2022. `em08` +0.209R (n=108) was positive in all four years but DSR 0.36.
- The breakout families that later look good in the challenge overlay (`ar07`,
  `ar08`) had validation means of +0.029R and +0.033R, DSR ~0.001, and drawdowns
  of 28R. They are noise, not an edge.
- PBO = 0.550; the best in-sample config underperformed the median out-of-sample
  in the majority of CSCV splits.

A causal regime classifier was deliberately **not** built: with no base edge to
condition and no new pre-registered trial budget, it would add complexity and
multiple-testing risk without a testable benefit.

### 3.3 Challenge simulation

`research/challenge_v5.py` simulated the full two-phase challenge over 165,600
attempts (50 configs x 3 risk fractions x 4 scenarios x 3 cost cases x 92 rolling
starts), with floating equity marks, daily resets, phase targets, min days,
inactivity, and internal soft guards. Accounting invariants all passed.

Primary decision case (5% daily, 3 min days, Phase 2 resets to $5,000, $10
round-turn commission):

| Risk | Passes / 4,600 | Rate | Firm breaches | Soft guard | Inactivity |
|---|---:|---:|---:|---:|---:|
| 0.10% | 0 | 0.00% | 0 | 0.00% | 100.00% |
| 0.25% | 0 | 0.00% | 0 | 12.85% | 57.91% |
| 0.50% | 100 | 2.17% | 0 | 72.11% | 13.57% |

At 0.10% the $5,000 account cannot meet the 0.01-lot minimum on typical stops, so
it never trades. At 0.50%, 3,003 trades across all configs were skipped for the
same reason. Passes are concentrated in 7 of 50 configs; the median config pass
rate is 0. Median time to pass is 675.8 calendar days; p95 max drawdown is 8.29%.
Under 1.5x cost stress the pass rate falls to 0.91%.

The `firm_rules_only` diagnostic (soft guards disabled) produced 303 passes
(6.59%) and 74 firm breaches (1.61%), confirming that the internal guards trade
pass probability for survival. Neither result justifies deployment.

### 3.4 Risk engine and MT5 demo runner

- `engine/prop_firm_compliance.py` and `engine/prop_firm_rules.py` implement a
  deterministic, persisted, fail-closed compliance engine: daily/overall limits,
  floating equity, higher-of daily reference, static overall floor, phase
  targets, minimum days, inactivity, open-risk room, and UTC+3 reset.
- `execution/mt5_demo_runner.py` is the sanctioned demo path. It verifies DEMO
  mode at connect and before every send, validates the live symbol spec against
  `order_calc_profit`, normalizes broker server time, floors size to the lot step,
  rejects minimum-lot over-budget trades, checks margin/stops/freeze, persists an
  idempotent intent, never retries a reject, treats a timeout as UNKNOWN and
  halts, verifies fills and SL/TP, reconciles on restart, and enforces a kill
  switch. A read-only `monitor()` halts on floating-equity soft limits but cannot
  close positions.
- Live read-only check on MetaQuotes-Demo account 114370849: account is genuinely
  DEMO, XAUUSD is 100 oz with $1 per 0.01 move per lot, tick fresh (0.205 s after
  offset), spread 33 points, `order_check` retcode 0/DONE with $40.33 margin,
  **no order transmitted**. Gate `BLOCKED` for seven explicit reasons.
- The runner has no live environment; `LIVE` is rejected by test.

## 4. Acceptance summary

| Area | Status |
|---|---|
| Official rules retrieved and versioned | PASS |
| Unresolved critical rules fail closed | PASS |
| FundingPips swap / commission basis / DST / credentials | BLOCKED |
| Data audit and UTC normalization | PASS |
| Full microstructure realism (queue, partial fills, rejects, impact) | FAIL (not modelled; documented) |
| Multiple families, pre-registration, multiple-testing control | PASS |
| Holdout sealed and never tuned | PASS |
| Validated after-cost edge | FAIL (none) |
| Prop-firm risk engine | PASS |
| Automatic close at limit | BLOCKED (monitor is read-only) |
| Challenge simulation and accounting | PASS |
| MT5 demo safety, sizing, reconciliation, kill switch | PASS (tests + live read-only) |
| Actual FundingPips demo order / end-to-end test | BLOCKED (not executed) |
| Real-money trading | NOT PERFORMED (prohibited) |

Full criterion-by-criterion evidence: `validation_report.md`.

## 5. What is real, simulated, and unverified

**Real**

- 64,651 XAUUSD H1 bars and observed spreads from a MetaQuotes demo server.
- The live broker symbol specification and `order_calc_profit` values.
- DEMO account verification, tick freshness, spread, and `order_check` plumbing.
- All 481 passing tests and the read-only report artifacts.

**Simulated**

- Every v5 fill, cost, swap, and challenge path. The challenge report is a
  `BACKTEST`; it is not a FundingPips statement or fill record.
- Sizing previews for a hypothetical $5,000 account on a $100,000 demo account.

**Unverified**

- FundingPips swap, commission side basis, winter reset offset, actual fills,
  and rule enforcement in their terminal.
- Any FundingPips demo/evaluation account behavior; none is available locally.
- Live slippage, rejects, requotes, liquidity, and news spread behavior.
- Whether a real, small edge exists that this 2013-2022 sample is too short to
  resolve. The correct claim is "no edge was demonstrated at the required
  confidence," not "an edge is proven absent."

## 6. What could invalidate these conclusions

- A coding error in the frozen v5 spread/swap/cost series or in the challenge
  simulator's floating marks. The cost identity and accounting invariants were
  tested, but not against an independent FundingPips fill record.
- A different broker's spread, commission, swap, or execution quality.
- A structural change in XAUUSD after 2022.
- A real edge smaller than this sample can resolve; the per-trade variance of
  these trend rules is high (roughly 1.7-2.7R), so 100-200 trades cannot resolve
  a 0.2R edge at 95% confidence.
- Any change to FundingPips rules after 2026-10-22.

## 7. What would be required next

This mission stops under its own rule: no post-result tuning of the 50 trials,
no holdout access, and no deployment without a gate-passing candidate.

A future program would need, at minimum:

1. A genuinely distinct, pre-registered hypothesis class with new data and its
   own trial budget, including a single sealed-holdout access for the finalist.
2. A sample large enough for the hypothesis's variance, or a lower-variance
   design, with the same purged/embargoed methodology and multiple-testing
   corrections.
3. Verified FundingPips swap, commission basis, and DST behavior, plus actual
   FundingPips demo credentials and a completed personal-EA review.
4. A $5,000 demo account with AutoTrading enabled, an armed config with a
   validated candidate, and human sign-off recorded in the audit log.
5. A 56-day demo record with at least 100 trades, compared against backtest
   slippage and drawdown expectations, before any human decision about capital.

**No strategy is guaranteed to pass a challenge or produce profits. The evidence
here says none of the tested strategies has a credible, validated edge after
costs.**

## 8. Evidence index

- `initial_audit.md` — repository, data, backtester, and legacy defects.
- `fundingpips_rules.md` — verified rules, sources, and unresolved items.
- `strategy_research.md` — families, hypotheses, results, regime decision.
- `backtest_methodology.md` — v5 protocol, challenge mechanics, reproducibility.
- `risk_management.md` — risk authority, limits, sizing, fail-closed behavior.
- `mt5_demo_guide.md` — operator guide and live read-only evidence.
- `validation_report.md` — PASS/FAIL/BLOCKED matrix and test results.
- `execution_checklist.md` — pre-deployment checklist for a future attempt.
- `configs/fundingpips.yaml` — versioned rules, scenarios, risk, runner, blockers.
- `reports_fundingpips/challenge_2026-10-22/` — 165,600-attempt evidence.
- `reports_fundingpips/mt5_demo/check_2026-10-22T18-43Z.json` — live check.
- `experiments/registry_v5.jsonl` — all 50 trials plus search summary.
- `docs/PRE_REGISTRATION_FUNDINGPIPS_2026-10-22.md` — frozen protocol.
