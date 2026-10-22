# Validation Report — FundingPips $5,000 Mission

Date: 2026-10-22 (UTC; 2026-10-23 local). Branch: `research/fundingpips-2026`.
Evidence: tests, live read-only MT5 report, frozen research outputs.

Verdict: the research and risk/execution **software** passed its verification gates.
The **deployment** is `BLOCKED`, and **no strategy** is validated. This is Outcome B
under the mission's own decision rule.

## 1. Verification commands and results

Run with the project interpreter `qt/.venv/Scripts/python.exe` (Python 3.13.15):

| Check | Command | Result |
|---|---|---|
| Full test suite | `python -m pytest tests/ -q` | **481 passed**, 5 warnings (19.9 s) |
| Mission tests | `python -m pytest tests/test_fundingpips_compliance.py tests/test_mt5_demo_runner.py tests/test_challenge_v5.py -q` | **102 passed** (36 + 46 + 20) |
| Lint, mission modules | `python -m ruff check execution/mt5_demo_runner.py scripts/mt5_demo_runner.py tests/test_mt5_demo_runner.py tests/test_challenge_v5.py research/challenge_v5.py engine/prop_firm_compliance.py engine/prop_firm_rules.py` | **All checks passed** |
| Strict types, core | `python -m mypy execution/mt5_demo_runner.py engine/prop_firm_compliance.py engine/prop_firm_rules.py --follow-imports=skip --strict` | **Success, 3 files** |
| Live read-only MT5 check | `python scripts/mt5_demo_runner.py --check --out reports_fundingpips/mt5_demo/check_2026-10-22T18-43Z.json` | `BLOCKED` as designed; no order transmitted |
| Challenge matrix | `python research/challenge_v5.py --config configs/fundingpips.yaml --out reports_fundingpips/challenge_2026-10-22 --workers 8` | 165,600 attempts, 1,800 cells, exit 0 |

Notes:

- `research/challenge_v5.py` is not in the core typed package. With `pandas-stubs`
  installed it reports 25 strict pandas-typing errors (largely `.iloc`/scalar
  unions). This is recorded rather than hidden; it does not affect the run results.
- The legacy `mt5_quant_algo.py` is deprecated and retained for research only. It has
  pre-existing lint debt; the mission modules do not depend on it for execution.
- The 5 warnings are pandas/numpy runtime warnings in existing validation code, not
  test failures.

## 2. Acceptance criteria

Status vocabulary: **PASS** = verified by executable evidence; **FAIL** = verified
not to meet the criterion; **BLOCKED** = cannot be verified with available resources,
so deployment is refused (fail closed).

### A. Rules and compliance

| # | Criterion | Status | Evidence / reason |
|---|---|---|---|
| A1 | Current official FundingPips 2-Step Standard rules retrieved and versioned | **PASS** | `docs/fundingpips_rules.md`, `configs/fundingpips.yaml` version 2026-10-22, source URLs, profile `RuleSourceStatus.EMPIRICAL` |
| A2 | Phase targets, static 10% overall loss, 3/5% daily loss on the higher of opening balance/equity, min days, inactivity, reset time encoded | **PASS** | `engine/prop_firm_rules.py`, 36 compliance tests including floating loss, static total, UTC+3 reset, persistence |
| A3 | Critical unresolved rules fail closed | **PASS** | Deployment blockers present; execution gate `BLOCKED`; runner refuses before broker calls |
| A4 | FundingPips swap schedule verified | **BLOCKED** | Not published outside terminal; no FundingPips account. Simulation uses MetaQuotes swap + 1.5x stress |
| A5 | Commission side basis ($5/lot) verified | **BLOCKED** | Official page does not state per-side vs round-turn. Primary uses $10 round turn, $5 sensitivity |
| A6 | Daily reset DST behavior verified | **BLOCKED** | FAQ says UTC+3; Master auto-close table implies UTC+2 winter. Simulator fixes 21:00 UTC; deployment blocked |
| A7 | FundingPips demo/evaluation account available | **BLOCKED** | No credentials/server. Live testing used MetaQuotes-Demo only |
| A8 | Personal-EA proof-of-ownership review completed | **BLOCKED** | Human operational step; not performed |
| A9 | FundingPips rules loaded only when `EMPIRICAL` | **PASS** | Runner rejects non-empirical profiles (`test_rule_mismatch_rejected`) |

### B. Data, backtester, and research

| # | Criterion | Status | Evidence / reason |
|---|---|---|---|
| B1 | Data audit: gaps, duplicates, ordering, stale/crossed quotes, zero prices, UTC | **PASS** | v5 quality gate 0% invalid in 2013-2022; `initial_audit.md`; tick server-offset bug found and fixed |
| B2 | Event time and receive time separated; broker time normalized at adapter | **PASS** | Runner stores raw and normalized tick time, explicit offset, tests for wrong offset |
| B3 | Backtester models spread, commission, swap, next-bar fills, stop-first, gaps, 1.5x stress | **PASS** | Frozen v5 cost model; 50 trials with cost/swap accounting; report hashes |
| B4 | Backtester models queue, partial fills, rejects, requotes, impact | **FAIL** | Not modelled; documented limitation. This makes results optimistic, not conservative |
| B5 | Multiple strategy families researched with pre-registered hypotheses | **PASS** | 5 families x 10 configs, frozen pre-registration, `strategy_research.md` |
| B6 | Trial registry counts every experiment and applies multiple-testing correction | **PASS** | `experiments/registry_v5.jsonl` (51 records), DSR, PBO = 0.550, bootstrap CIs |
| B7 | Losing experiments preserved | **PASS** | All 50 trial records and `reports_v5/search_2026-10-22/` retained; no deletion or cherry-pick |
| B8 | Sealed holdout never used for tuning; single final access policy | **PASS** | `holdout_accessed: false`; lock file `candidate_frozen: false`, empty access log; challenge report records it |
| B9 | Causal regime classifier built only if it earns complexity | **PASS** | Not built; documented decision in `strategy_research.md` §6. No base edge to condition, would be post-hoc tuning |
| B10 | Leakage canaries / shuffled-label controls | **PASS** | v5 pipeline tests; canaries pass before search (see `V5_RESULT.md`) |
| B11 | A validated, positive after-cost edge exists | **FAIL** | 50/50 fail G2 (CI lower > 0) and G4 (DSR >= 0.95); PBO 0.550 > 0.30. No candidate |

### C. Prop-firm risk engine

| # | Criterion | Status | Evidence / reason |
|---|---|---|---|
| C1 | Independent deterministic risk engine; no ML/LLM authority | **PASS** | `engine/prop_firm_compliance.py`; pure functions and persisted state |
| C2 | Daily loss, overall static loss, floating equity, higher-of reference | **PASS** | Tests: `test_daily_loss_breach_from_floating`, `test_daily_reference_uses_higher_of_balance_and_equity`, `test_total_loss_is_static_not_trailing` |
| C3 | Open/correlated exposure and room consumption | **PASS** (single symbol) | `test_open_risk_consumes_daily_room`; max one XAUUSD position. Cross-symbol correlation is out of scope for one instrument |
| C4 | Broker-specific sizing via contract/tick/money-per-point, floor to lot step | **PASS** | `SymbolSpec.from_broker` uses `order_calc_profit`; floor tests; min-lot rejection; live spec point 0.01, contract 100, $1/point |
| C5 | Margin/volume/stops/freeze validation | **PASS** | Order check, free-margin cap, volume min/max/step, stops/freeze tests; live `order_check` margin $40.33 |
| C6 | Internal daily stop and safety buffer | **PASS** | 1% daily / 4% overall soft guards; soft-guard tests and challenge overlay |
| C7 | Max positions and duplicate prevention | **PASS** | One position, foreign/orphan detection, unknown intent blocks, `test_existing_pending_position_blocks_new_entry` |
| C8 | Abnormal spread, stale tick, future tick, crossed quote protection | **PASS** | Tick validation tests; live tick age 0.205 s after offset, spread 33 points |
| C9 | Restart recovery and persistent state | **PASS** | Atomic `StateStore`, reload tests, unknown intent reconciliation, compliance state persistence |
| C10 | Kill switch (manual and automatic) | **PASS** | File/env kill switch; monitor/compliance/reconciliation failures activate it; tests cover blocks |
| C11 | Automatic position close at a firm limit | **BLOCKED** | Monitor is read-only and cannot transmit orders; closing remains a human action. This is a deliberate safety choice, but it means unattended overnight protection is incomplete |
| C12 | Daily/overall protection with floating P&L while a position is open | **PASS** (monitor) / **PARTIAL** | `monitor()` halts and kills the switch on soft/hard limits, but does not close. Requires an external scheduler and human response |

### D. Challenge simulation

| # | Criterion | Status | Evidence / reason |
|---|---|---|---|
| D1 | Two-phase challenge simulated with phase targets, daily/overall limits, floating marks, min days, inactivity | **PASS** | `research/challenge_v5.py`; 20 tests; 165,600 attempts |
| D2 | Pass/breach probabilities, time to pass, drawdown, near-breach, cost sensitivity | **PASS** | `summary.csv`, `aggregates.csv`, `attempts.csv`, `challenge_report.json` |
| D3 | Accounting invariants (no pass+breach, no phase 2 without phase 1, outcomes sum, no duplicate keys) | **PASS** | Validation script over 165,600 rows; all checks passed |
| D4 | A simulated pass is not represented as an actual FundingPips pass | **PASS** | Report metadata `simulated_pass_not_real_pass: true`; docs repeat it |
| D5 | No strategy is declared challenge-ready without expectancy evidence | **PASS** | Decision rule applied; 0 candidates; Outcome B |

### E. MT5 demo execution

| # | Criterion | Status | Evidence / reason |
|---|---|---|---|
| E1 | DEMO verification at connect and before every send | **PASS** | `verify_demo` re-queries; tests for non-demo, unknown mode, disconnect, re-verify after order check |
| E2 | No live code path; live environment rejected | **PASS** | `test_live_environment_rejected`; no live adapter/config/credentials |
| E3 | Symbol validation against real broker specs | **PASS** | Live XAUUSD spec captured; `order_calc_profit` ground truth; tick-value mismatch flagged rather than trusted |
| E4 | Sizing, risk approval, margin/volume checks before send | **PASS** | `size_position` and preflight tests; live preview 0.01 lot, $10.15 risk vs $12.50 budget |
| E5 | Order/fill/SL/TP verification | **PASS** (unit) | Fake-terminal tests verify fill, SL/TP, no-retry on reject, UNKNOWN on timeout |
| E6 | Reconciliation and restart recovery | **PASS** | `reconcile`, orphan/foreign/unresolved-intent tests |
| E7 | Structured logs and decision traces | **PASS** | JSONL decisions, state store, audit events |
| E8 | Bounded retries and disconnect handling | **PASS** | No retry on reject; timeout becomes UNKNOWN + kill switch; read errors block |
| E9 | Manual kill switch | **PASS** | `--kill`, `--clear-kill --confirm`, file/env switch |
| E10 | Actual end-to-end demo order placed and verified on a FundingPips account | **BLOCKED** | No FundingPips credentials; config disarmed; no validated candidate; terminal AutoTrading off. **No order was sent** |
| E11 | Live MetaQuotes-Demo order plumbing validated without transmitting | **PASS** | `order_check` retcode 0/DONE, margin $40.33, `transmitted: false`; no baseline/intent/kill state created |

## 3. Live MT5 evidence (read-only)

Report: `reports_fundingpips/mt5_demo/check_2026-10-22T18-43Z.json`.

- Account 114370849, `MetaQuotes-Demo`, `trade_mode=0` (DEMO), USD 100,000,
  leverage 100, terminal connected.
- XAUUSD: point 0.01, digits 2, contract 100, volume 0.01/100/0.01, filling
  FOK|IOC; live bid 4032.65 / ask 4032.98, spread 33 points, normalized tick age
  0.205 s.
- Sizing preview on a hypothetical $5,000 account: 0.01 lots, risk $10.15,
  budget $12.50, approved.
- Execution gate: `BLOCKED` for `CONFIG_NOT_ARMED`, `ORDER_SENDING_DISABLED`,
  `NO_CANDIDATE_ID`, `CANDIDATE_NOT_VALIDATED`, `DEPLOYMENT_BLOCKERS_PRESENT`,
  `ACCOUNT_BALANCE_NOT_INITIAL(100000.00!=5000.00)`,
  `TERMINAL_AUTOTRADING_DISABLED`.
- `order_check` was performed and reported margin $40.33; **no order was
  transmitted**. No baseline, compliance state, intent, or kill switch was
  created by the check.
- The account is not a FundingPips account and is not $5,000. It proves symbol
  and order plumbing only.

## 4. What is not verified

1. Any real FundingPips fill, swap, commission charge, daily reset, or breach
   behavior. No FundingPips account exists in this workspace.
2. Actual MT5 execution of a demo order by this runner. AutoTrading was off, the
   config was disarmed, and no candidate existed. The mission rules explicitly
   forbid claiming an end-to-end MT5 test that was not executed.
3. Unattended limit enforcement: the monitor can halt but cannot close positions.
4. Cross-symbol/correlated exposure beyond one XAUUSD position.
5. Real-account slippage, rejects, requotes, and liquidity. Demo fills are an
   upper bound, never proof of live viability.

## 5. What would change the verdict

- A new, genuinely distinct pre-registered strategy program that passes all v5
  gates on clean data, including a single sealed-holdout access.
- Verified FundingPips swap, commission basis, and DST behavior, plus actual
  FundingPips demo credentials.
- A completed personal-EA review and a human promotion decision.
- An armed, $5,000 demo account with AutoTrading enabled, followed by a 56-day
  demo record compared against backtest expectations.

None of those conditions is met. The correct status is **no validated candidate,
deployment blocked, no real-money trading performed**.
