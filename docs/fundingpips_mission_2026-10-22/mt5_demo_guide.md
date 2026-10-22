# MT5 Demo Runner — Operator Guide

Sanctioned path: `execution/mt5_demo_runner.py`, driven by
`scripts/mt5_demo_runner.py`. Environment: `MT5_DEMO`. No live code path exists.

## 1. Safety model

The runner is read-only by default. It sends an order only when **all** of the
following are true:

- `configs/fundingpips.yaml` has `environment: MT5_DEMO`;
- `deployment.armed: true`;
- `deployment.order_sending_enabled: true`;
- a non-empty `candidate_id` with `candidate_validated: true`;
- the deployment blocker list is empty;
- the rules profile is `EMPIRICAL`;
- MT5 is connected to the expected DEMO account and server;
- the symbol spec, tick, spread, size, margin, and stops pass live checks;
- the account baseline matches the configured $5,000 starting account;
- no kill switch, unresolved intent, foreign/orphan position, or open position;
- the compliance engine and internal soft guards allow the trade.

In this mission the first five are **not** satisfied and the blocker list is not
empty. `--execute-demo` therefore refuses before any order call. That is
intentional, not a bug.

## 2. Prerequisites

- Windows with MetaTrader 5 installed and the MetaTrader5 Python package.
- A **demo** account. This mission used MetaQuotes-Demo, account 114370849,
  `trade_mode=0`, USD 100,000, leverage 100.
- For a future FundingPips test: a FundingPips demo/eval account, its server,
  and login. Those credentials are **not present** in this workspace and must
  not be invented.
- AutoTrading enabled in the terminal for actual demo execution. It was
  disabled during this mission; the check correctly reports
  `TERMINAL_AUTOTRADING_DISABLED`.
- Python dependencies from `requirements.txt`. The `python-json-logger`, `ruff`
  and `mypy` packages were missing from the local Python 3.11 environment during
  the audit and were installed from the declared requirements.

## 3. Configuration

Relevant block in `configs/fundingpips.yaml`:

```yaml
mt5_demo_runner:
  symbol: "XAUUSD"
  risk_fraction: 0.0025
  max_spread_points: 50
  max_tick_age_seconds: 5
  server_utc_offset_hours: 3
  max_slippage_points: 20
  magic: 20261022
  risk_buffer_points: 5
  max_margin_fraction: 0.25
  max_open_positions: 1
  state_dir: "data/mt5_demo_state"
  expected_login: null
  expected_server: null
```

`server_utc_offset_hours` is **not** cosmetic. MT5 returns broker server time.
Observed MetaQuotes-Demo offset was +3h; without normalization, a fresh tick
looks ~3 hours in the future and is rejected. FundingPips' winter/summer offset
remains unresolved and is a deployment blocker.

`expected_login` and `expected_server` are null in this mission. For a real
demo deployment they must be set; the runner then rejects any other account.

## 4. Commands

```bash
# Read-only preflight; writes a JSON report, never touches account state.
python scripts/mt5_demo_runner.py --check

# Read-only preflight plus a non-transmitting order_check (margin/price plumbing).
python scripts/mt5_demo_runner.py --check --order-check

# Explicit report path (use a timestamped name to preserve evidence).
python scripts/mt5_demo_runner.py --check --order-check \
  --out reports_fundingpips/mt5_demo/check_2026-10-22T18-43Z.json

# Reconcile persisted intents against live positions/orders (read-only).
python scripts/mt5_demo_runner.py --reconcile

# Monitor floating equity and daily/overall limits; halt on alert; no orders.
python scripts/mt5_demo_runner.py --monitor

# Activate / clear the manual kill switch.
python scripts/mt5_demo_runner.py --kill "operator reason"
python scripts/mt5_demo_runner.py --clear-kill --confirm

# Execution (refused in this mission; requires every gate open).
python scripts/mt5_demo_runner.py --execute-demo --signal-file signal.json
```

Exit codes:

- `0`: report produced (check/reconcile/kill/monitor OK);
- `2`: execution attempted but no verified fill, or monitor alerted;
- `3`: configuration or startup failure.

`--monitor` is intended for Windows Task Scheduler (for example every 1-5
minutes while a position is open). It updates compliance state and activates
the kill switch on an alert. It cannot close a position; that is a human step.

## 5. What the check verifies (live evidence)

Latest read-only report:
`reports_fundingpips/mt5_demo/check_2026-10-22T18-43Z.json`.

Verified on 2026-10-22 18:43 UTC:

- account 114370849 @ MetaQuotes-Demo, `trade_mode=0` (DEMO), balance/equity
  100,000, USD, leverage 100;
- terminal connected, AutoTrading disabled;
- XAUUSD point 0.01, contract 100, tick size 0.01, money per point per lot $1.00;
- volume min 0.01, step 0.01, max 100;
- `trade_tick_value` reported 0.1 vs measured 1.0 -> warning, measured value used;
- fresh tick (age ~0.2 s after server-offset normalization), spread ~33 points;
- `order_check` performed, **not transmitted**, retcode 0 (`DONE`), margin
  $40.33, free margin $99,959.67;
- execution gate **BLOCKED**: config not armed, sending disabled, no candidate,
  candidate not validated, deployment blockers present, account balance not
  $5,000, terminal AutoTrading disabled.

The check does not create a baseline, compliance state, intent, or kill switch.
Confirmed after the run: `data/mt5_demo_state/` does not exist and no $5,000
compliance state file was created.

## 6. What is NOT verified

- No order was sent. `order_check` proves request/margin plumbing only.
- No fill, SL/TP, partial fill, rejection, or requote on a real demo was tested.
- No FundingPips server, account, margin, swap, or commission statement exists.
- AutoTrading was off, so even a fully armed config would have been blocked.
- Demo fills are not live fills and are not proof of FundingPips viability.

Do not describe this as an end-to-end MT5 test. It is a read-only demo preflight
plus a non-transmitting margin check.

## 7. Order lifecycle in the runner

`SIGNAL -> RISK/COMPLIANCE -> PREFLIGHT -> BASELINE -> RECONCILE -> ORDER_CHECK
-> PERSIST PENDING INTENT -> RE-VERIFY DEMO -> ORDER_SEND (once) -> RE-READ
POSITION -> VERIFY SL/TP/VOLUME/PRICE -> FILLED or VERIFICATION_FAILED`

- The order is sent **once**, with no automatic retry.
- A timeout/exception/uncertain retcode is `UNKNOWN`, activates the kill switch,
  and blocks new entries.
- A verified fill must match magic, comment prefix, side, SL, TP, volume (not
  above request, at least broker minimum), and price within slippage.
- Partial fills are recorded. An oversized or mismatched fill is a verification
  failure and halts.
- On restart, `reconcile` matches persisted comments/magic. Unresolved, orphan,
  or foreign positions block new entries.

## 8. Kill switch and open-position policy

Activate:

```bash
python scripts/mt5_demo_runner.py --kill "reason"
# or set QUANT_LAB_KILL_SWITCH=reason
```

The kill switch blocks all new entries. It does **not** close an existing
position. Policy:

1. Activate the switch and stop the scheduler.
2. Inspect the position in MT5.
3. Close it manually if required, or let the verified SL/TP handle it.
4. Record the reason and the manual action in the decision log / incident note.
5. Clear the switch only after reconciliation is clean and a human signs off.

There is no automatic liquidation path in this mission.

## 9. Future FundingPips demo checklist (not authorized now)

Only after a validated candidate exists and a human approves:

1. Obtain FundingPips demo/eval credentials and server; verify they are demo.
2. Resolve the daily-reset DST offset, swap, and commission side basis.
3. Complete FundingPips' personal-EA proof-of-ownership review.
4. Set `expected_login`, `expected_server`, and the correct server time offset.
5. Re-run `--check --order-check`; require READY except for deliberate arming.
6. Arm config, set a validated candidate ID, clear blockers, enable AutoTrading.
7. Run `--monitor` on a schedule and a single `--execute-demo` with a recorded
   signal; verify fill/SL/TP; then reconcile.
8. Keep the account in demo. Live capital is explicitly out of scope.

## 10. Troubleshooting

| Symptom | Meaning |
|---|---|
| `TICK_FUTURE_TIMESTAMP` | server offset is wrong or not configured |
| `TICK_STALE` | market closed or feed disconnected; do not trade |
| `TICK_SPREAD_TOO_WIDE` | abnormal spread; wait |
| `SYMBOL_SPEC_INVALID` | broker contract/profit inconsistency; do not trade |
| `ACCOUNT_BALANCE_NOT_INITIAL` | not the configured $5,000 challenge account |
| `TERMINAL_AUTOTRADING_DISABLED` | enable AutoTrading in the terminal |
| `DEPLOYMENT_GATE` | config/candidate/blockers not open |
| `KILL_SWITCH` | manual or monitor halt; reconcile and investigate |
| `UNRESOLVED_INTENTS` | restart/reconcile required before any new entry |
| `FOREIGN_POSITIONS` | a position not opened by this runner; manual review |
| `MONITOR_ALERT` | daily/overall soft or firm limit reached; close manually |

When in doubt, `NO_TRADE` is the correct output.
