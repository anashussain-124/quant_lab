# Execution Checklist — FundingPips $5,000 (Demo Only)

Date: 2026-10-22 (UTC). Environment: `MT5_DEMO`. **Real-money trading is prohibited in
this mission and no live path exists.**

Current disposition: **DO NOT DEPLOY.** Every research gate fails, four critical
FundingPips rules are unresolved, no FundingPips account exists, and the config is
disarmed. This checklist is the gate list a future attempt must satisfy; it is not
a record of a completed deployment.

## 1. Research and promotion gates

| Check | Status now | Evidence / requirement |
|---|---|---|
| Strategy passed all frozen v5 statistical gates | **FAIL** | 50/50 fail G2 and G4; PBO 0.550 |
| Independent validation expectancy > 0 with 95% CI lower bound > 0 | **FAIL** | All CIs span zero |
| Single sealed-holdout access performed for the finalist | **BLOCKED** | Holdout deliberately unopened; no finalist |
| Human promotion sign-off recorded in audit log | **BLOCKED** | No candidate to sign off; agent cannot promote |
| Challenge simulation completed and invariants pass | **PASS** | 165,600 attempts; accounting checks pass |
| No post-result tuning of the 50 trials | **PASS** | Frozen protocol; no retune performed |
| No universal trade-count/confidence threshold invented | **PASS** | v5 gates are program-specific and pre-registered |

Stop here if any of the first four is not satisfied. They are not.

## 2. Rules, broker, and account gates

| Check | Status now | Requirement |
|---|---|---|
| Official FundingPips rules current and versioned | **PASS** | `fundingpips_rules.md`, config version 2026-10-22 |
| Profile `source_status == EMPIRICAL` | **PASS** | Runner refuses other statuses |
| XAUUSD swap schedule verified | **BLOCKED** | Obtain FundingPips contract specification / terminal swap values |
| Commission side basis verified | **BLOCKED** | Confirm whether $5/lot is per side or round turn |
| Daily reset DST rule verified | **BLOCKED** | Resolve FAQ UTC+3 vs winter UTC+2 conflict |
| FundingPips demo/evaluation credentials and server available | **BLOCKED** | Not present locally; must not be invented |
| Personal-EA proof-of-ownership review completed | **BLOCKED** | Human operational step with FundingPips |
| Account is DEMO (`trade_mode == 0`) | **PASS in current check** | MetaQuotes-Demo 114370849; must be re-verified live |
| Account starting balance is exactly $5,000 | **FAIL in current check** | Current demo is $100,000; baseline gate blocks |
| `expected_login` and `expected_server` set in config | **BLOCKED** | Currently null; must be set for a real demo |
| Terminal connected and AutoTrading enabled | **FAIL in current check** | AutoTrading disabled; no order can be sent |

## 3. Configuration gates (`configs/fundingpips.yaml`)

A future demo deployment requires all of the following. They are deliberately
closed now.

- [ ] `environment: MT5_DEMO` (never `LIVE`; the runner rejects it).
- [ ] `deployment.armed: true`.
- [ ] `deployment.order_sending_enabled: true`.
- [ ] `deployment.candidate_id` non-empty and matches the promoted candidate.
- [ ] `deployment.candidate_validated: true`.
- [ ] `deployment.blockers: []` after verifying swap, commission, DST, credentials,
      EA review, and candidate validation.
- [ ] `risk_fraction` set within the pre-registered range; do not scale risk up
      after wins.
- [ ] `max_spread_points`, `max_tick_age_seconds`, `max_slippage_points`,
      `server_utc_offset_hours`, `max_margin_fraction`, `max_open_positions`,
      `magic`, `risk_buffer_points` reviewed against the FundingPips terminal.
- [ ] Initial balance and daily/overall guards match the purchased account size.
- [ ] Account baseline created **once** for the correct account, after the account
      is funded at $5,000 and before the first trade.

Never edit the frozen research parameters to make a candidate pass. A parameter
change is a new pre-registered program with its own trial count.

## 4. Preflight (read-only) — run before every session

```bash
qt/.venv/Scripts/python.exe scripts/mt5_demo_runner.py --check --order-check \
  --out reports_fundingpips/mt5_demo/check_<UTC timestamp>.json
```

Required observations:

- [ ] `account.ok = true`, `trade_mode = 0`, login/server match config.
- [ ] `terminal.connected = true`, `terminal.trade_allowed = true`.
- [ ] balance/equity are finite, positive, and match the expected account.
- [ ] XAUUSD `contract_size = 100`, tick size/point 0.01, money per point per lot
      measured from `order_calc_profit`, volume min/step correct.
- [ ] reported tick value is either consistent or explicitly warned and not used.
- [ ] tick age is below the configured maximum after server-offset normalization.
- [ ] spread is below the configured maximum and the book is not crossed.
- [ ] `order_check` is DONE and margin is within the cap; `transmitted = false`.
- [ ] execution gate shows only expected reasons, if any.
- [ ] kill switch is absent; no unresolved intent; no foreign/orphan position.
- [ ] baseline exists and matches the account; no open position before a new entry
      unless the strategy allows it.

If any item fails: **NO TRADE**. Do not "fix" a failed preflight by loosening a
limit during the session.

## 5. Per-order lifecycle

The runner implements this sequence; verify it in the decision log:

```text
SIGNAL -> RISK/COMPLIANCE -> PREFLIGHT -> BASELINE -> RECONCILE
-> ORDER_CHECK -> PERSIST PENDING INTENT -> RE-VERIFY DEMO
-> ORDER_SEND (once) -> RE-READ POSITION -> VERIFY -> FILLED
```

- [ ] Signal is generated from a closed bar, not an intrabar price.
- [ ] Risk approval uses current equity, remaining daily room, remaining overall
      room, and the internal soft guards.
- [ ] Size is floored to the lot step; 0.01-lot over-budget is rejected, never
      rounded up.
- [ ] Stop distance, tick value, money-per-point, margin, stops level, and freeze
      level are all valid; unknown values reject.
- [ ] DEMO mode is re-verified **after** `order_check` and immediately before send.
- [ ] The pending intent is persisted before send; a restart cannot duplicate it.
- [ ] Order is sent once. A rejection is not retried. A timeout is `UNKNOWN` and
      activates the kill switch.
- [ ] Fill verification checks magic, comment prefix, side, volume, price within
      slippage, SL, and TP. A mismatch halts.
- [ ] Decision log contains `decision_id`, environment, account, symbol, reason
      codes, and the order request/response (no secrets).

## 6. Monitoring, restart, and kill switch

- [ ] Run `--monitor` on a schedule (1-5 minutes) while a position is open. It
      updates floating equity, reconciles, and kills the switch on a soft/hard
      limit. It **cannot close** the position.
- [ ] A human is assigned to close positions and investigate on any `HALT`.
- [ ] On restart, run `--reconcile` before any new entry. Unknown intents and
      orphan/foreign positions block trading.
- [ ] Manual kill: `scripts/mt5_demo_runner.py --kill "reason"` (or set
      `QUANT_LAB_KILL_SWITCH`). Clearing requires `--clear-kill --confirm` and a
      documented reason.
- [ ] Never clear a kill switch to "see if the next trade works." Resolve the
      cause first.
- [ ] Daily and overall limits are checked from **floating** equity, not only
      realized balance. A limit breach ends the session.

## 7. Post-run evidence

- [ ] JSON check/monitor/reconcile reports saved with UTC timestamps.
- [ ] Decision JSONL and state snapshots preserved; no deletion of losing runs.
- [ ] Trade list reconciled against MT5 statements (demo).
- [ ] Slippage, spread at fill, rejects, and latency summarized.
- [ ] Demo results compared with backtest expectations over at least 56 days and
      100 trades before any human capital discussion.
- [ ] Report labels every result `MT5_DEMO`; it is not `LIVE` evidence.
- [ ] A simulated or demo pass is never described as an actual FundingPips pass.

## 8. Abort conditions (any one means stop)

- Non-DEMO or unknown `trade_mode`.
- Account login/server/balance mismatch, or baseline mismatch.
- Stale/future tick, crossed quote, spread above limit, or tick-value/contract
  inconsistency that cannot be resolved.
- Missing or invalid stop distance, tick value, margin, stops/freeze level.
- Minimum lot exceeds the risk budget.
- Kill switch, unresolved intent, duplicate/open position, foreign position.
- Order rejection, timeout, unknown retcode, or fill/SL/TP verification failure.
- Daily or overall soft guard reached; firm limit reached.
- Any config change that was not reviewed and recorded.

## 9. Sign-off (future deployment only)

| Role | Requirement | Status |
|---|---|---|
| Research | Candidate passed every gate; holdout accessed once and logged | Not met |
| Risk | Engine tests pass; limits and buffers reviewed | Software PASS; no candidate |
| Operations | FundingPips demo credentials, EA review, $5,000 baseline | Blocked |
| Human owner | Explicit written promotion to demo, recorded in audit log | Not requested; agent cannot self-promote |

Until every row is satisfied, the only correct action is **NO TRADE**.
