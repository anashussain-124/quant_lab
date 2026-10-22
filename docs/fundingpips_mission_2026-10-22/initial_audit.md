# Initial Audit — FundingPips $5,000 XAUUSD Mission

Date: 2026-10-22 (UTC). Branch: `research/fundingpips-2026`.
Starting commit: `09ca9ab` ("docs: clarify sealed holdout policy for a future
program"). Environment labels used throughout: `HISTORICAL_REAL`, `BACKTEST`,
`MT5_DEMO`. No `LIVE` path exists.

## 1. Scope of this audit

The mission asked for an autonomous research and demo-execution program:
verify FundingPips rules, audit data and backtester, research XAUUSD strategy
families without holdout leakage, build an independent prop-firm risk engine,
simulate the two-phase challenge, build a safe MT5 demo runner, and report
PASS/FAIL/BLOCKED against every acceptance criterion.

This audit records what existed before new code was written, what was found
broken, and what the mission deliberately did not touch.

## 2. Repository state at audit

| Item | Finding |
|---|---|
| Prior program | v4 closed with a verified negative result (tag `closeout-2026-10-22`). |
| v5 program | 50 pre-registered XAUUSD H1 trials, verdict `NO_EDGE_FOUND`; 2023-2026 holdout never opened. |
| Holdout controls | `data/holdout.py` + `scripts/run_holdout_once.py`; lock file reports `candidate_frozen: false` and an empty access log. |
| Data | 64,651 XAUUSD H1 bars, 2012-01-03..2022-12-30 UTC, SHA-256 `5dbace90...e267e`. |
| Trade log | 51,791 rows from 50 configs, SHA-256 `5384578e...e2a270`; exact identity `net_r = gross_r - cost_r + swap_r`. |
| Tests at start | 41 test modules; the configured suite previously recorded 392 passing tests. |
| Python | Windows, Python 3.11.9 in this shell; project declares 3.12+. |
| MT5 terminal | MetaQuotes-Demo, account 114370849, `trade_mode=0` (DEMO), USD 100,000, leverage 100, build 6276. |
| AutoTrading | Terminal `trade_allowed=False`; account `trade_expert=True`. |

The live MT5 account is **not** a FundingPips evaluation account and is not
$5,000. It can prove symbol/order plumbing only.

## 3. Data and leakage audit

- Timestamps are UTC-aware. MT5 tick timestamps were observed to be **broker
  server time (UTC+3 on MetaQuotes-Demo)**, not UTC. This was initially
  misread as future-dated ticks; the new runner now normalizes with an explicit
  `server_utc_offset_hours` and records both raw and normalized times.
- The v5 data quality gate reports 0% invalid OHLC, zero/negative prices,
  duplicate timestamps, missing values, and missing volume for 2013-2022.
- v5 leakage canaries and shuffled-label checks passed before the search.
- The v5 selection window (2019-2022) is **selection data**, not clean OOS.
  Only 2023-2026 would be clean, and it remains sealed.
- FundingPips-specific work reuses the frozen v5 trade log. It does not
  re-simulate entries, and it must not be used to tune the 50 configs.

## 4. Backtester audit

Existing v5 model:

- Signal at H1 bar close, fill at next bar open.
- Stop-first tie-breaking; gaps filled at the open.
- One position at a time; trailing stops updated at bar close.
- Observed spread with a 10-point floor and causal fallback, 0.5 point
  slippage per side, USD 7/lot round-turn commission, MT5 swap points with
  triple Wednesday.
- 1.5x stress multiplier applied to spread, slippage, commission and swap.
- No queue position, partial fills, requotes, rejects, or market impact.

The challenge simulator adds contract-size sizing and intrabar equity marks but
inherits these limitations. It is a `BACKTEST`, not a FundingPips fill record.

## 5. Risk and execution audit — defects found

Legacy code was not safe enough to be an execution path. Confirmed defects:

1. `mt5_quant_algo.py` used `point_value_usd=10.0`; XAUUSD at 100 oz is
   **$1.00 per 0.01 move per lot**. Fixed to 1.0.
2. Position size used `round()` and a forced 0.01 minimum, which could round
   **up** past the risk budget. Fixed to floor and reject below minimum.
3. `_get_account_balance_and_equity` returned a fabricated `10000.0` fallback.
   Fixed to raise when MT5 data is unavailable.
4. Compliance equity was updated with bar time, which can rewind the daily
   reference. Fixed to current UTC.
5. `engine/mt5_bridge.py::verify_demo_mode` returned `True` without re-querying
   the terminal. Fixed to re-query and compare login/server.
6. The bridge's DEMO send called a nonexistent adapter method. The bridge now
   **refuses all DEMO sends** and points to the sanctioned runner.
7. Shadow fills used random slippage without an environment label. They are now
   explicitly `environment: SIMULATED`, `simulated_fill: true`.
8. The legacy `MetaTrader5Adapter` class was undefined. The bridge now attaches
   directly to the running terminal for read-only use and rejects account
   mismatches.
9. `mt5_quant_algo.py` logged `EXEC_ORDER_SUBMITTED` even when the bridge
   rejected an order. It now returns `NO_TRADE` / `EXECUTION_REFUSED` and logs
   simulated fills separately.

The new sanctioned path is `execution/mt5_demo_runner.py`; see
`mt5_demo_guide.md`.

## 6. MT5 broker-spec audit

Verified live from `order_calc_profit` and `symbol_info`:

| Field | XAUUSD value |
|---|---|
| Digits / point / tick size | 2 / 0.01 / 0.01 |
| Contract size | 100 oz |
| Money per 0.01 move, 1 lot | USD 1.00 |
| Money per 1.00 move, 1 lot | USD 100.00 |
| Volume min / step / max | 0.01 / 0.01 / 100 |
| Stops / freeze level | 0 / 0 points |
| Filling mode | FOK + IOC (3) |
| Symbol trade mode | 4 (full) |
| 1-lot margin at ~$4,000 | ~USD 4,038 (leverage 1:100 on this demo) |
| Observed spread | ~28-34 points (~$0.28-0.34) |

`symbol_info.trade_tick_value` reported `0.1`, which contradicts the measured
profit calculation and contract size. The runner treats this as a warning and
sizes from `order_calc_profit` plus contract size, never from the reported tick
value. Any contract/profit mismatch above 5% is rejected.

FundingPips uses 1:30 metals leverage and its own swap/commission schedule; none
of those values are assumed from MetaQuotes-Demo.

## 7. Compliance audit

`engine/prop_firm_compliance.py` is the deterministic rule engine. It models:

- static 10% overall floor from starting balance;
- daily limit as a percentage of `max(opening balance, opening equity)`;
- floating and realized losses;
- 21:00 UTC reset (with the unresolved winter offset documented);
- phase target, minimum trading days, inactivity, and a manual halt;
- persistence with atomic writes and restart loading.

`engine/prop_firm_rules.py` profiles default to $10,000; the runner replaces that
with the official $5,000 account size and cross-checks every critical YAML value
against the frozen profile. A mismatch is a configuration error.

## 8. Fail-closed conditions at the end of the audit

- No FundingPips demo/evaluation credentials or server are available.
- FundingPips XAUUSD swap is unpublished locally and unverified.
- The commission side basis ($5/lot) is unstated by FundingPips.
- The daily reset offset conflicts between FAQ (UTC+3) and the Master
  auto-close table (UTC+2 in winter).
- Personal-EA proof-of-ownership review has not been performed.
- All 50 v5 trials failed the frozen gates; there is no validated candidate.
- The local MT5 account is Demo but not a $5,000 FundingPips account.

These are deployment blockers, not reasons to fabricate a pass.

## 9. What this audit does not claim

- No live-trading capability was added.
- No FundingPips account was created, purchased, or connected.
- No strategy is described as profitable or proven.
- The live MT5 terminal was used only for read-only checks and a non-transmitting
  `order_check`; no demo order was sent.

## 10. Evidence

- `reports_v5/search_2026-10-22/report.json` and `report.md`
- `reports_v5/search_2026-10-22/trade_log.csv`
- `reports_fundingpips/mt5_demo/check_2026-10-22T18-43Z.json`
- `reports_fundingpips/challenge_2026-10-22/` (matrix output)
- `tests/test_mt5_demo_runner.py`, `tests/test_fundingpips_compliance.py`,
  `tests/test_challenge_v5.py`
