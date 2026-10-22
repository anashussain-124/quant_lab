# Risk Management — Design and Limits

Environment: `MT5_DEMO` only. No live order path exists. This document describes
the deterministic limits that sit between a strategy signal and any demo order.

## 1. Authority order

1. **FundingPips rules** (hard limits) — `engine/prop_firm_compliance.py`.
2. **Internal soft guards** — enforced by `execution/mt5_demo_runner.py`, always
   tighter than the firm limits.
3. **Execution checks** — broker spec, tick freshness, spread, margin, stops,
   volume, fill verification.
4. **Strategy** — proposes a side and a stop distance only.

The strategy cannot override, resize, or bypass any of the first three layers.
The runner has no code path that increases risk after wins, and no ML/LLM
component in any risk decision.

## 2. Challenge limits modeled

| Limit | Value on $5,000 | Basis |
|---|---:|---|
| Phase 1 target | +$400 (8%) | starting balance |
| Phase 2 target | +$250 (5%) | starting balance |
| Overall loss floor | -$500 (10%) | static starting balance |
| Daily loss limit (primary) | 5% of daily reference | max(opening balance, opening equity) |
| Daily loss limit (sensitivity) | 3% of daily reference | 3% add-on variant |
| Minimum trading days | 3 | 0 with the 3% variant |
| Inactivity | 30 calendar days | no completed trade |
| Daily reset | 21:00 UTC | FAQ UTC+3 |

Floating equity is included. The overall floor is checked against equity **and**
balance; a floating loss can breach it before the position is closed.

## 3. Internal guards (tighter than the firm)

| Guard | Default | Purpose |
|---|---:|---|
| Risk per trade | 0.25% of equity, max 0.50% | keeps one stop well inside the daily limit |
| Safety buffer | 0.5% of starting balance ($25) | stops before the firm's hard floor |
| Daily soft stop | 1.0% of starting balance ($50) | blocks new entries and alerts |
| Overall soft stop | 4.0% of starting balance ($200) | blocks new entries and alerts |
| Max open positions | 1 | prevents stacking |
| Max spread | 50 points ($0.50) | rejects abnormal spreads |
| Max tick age | 5 seconds | rejects stale quotes |
| Max slippage (request) | 20 points | bounded deviation |
| Risk buffer | 5 points | stop-gap/slippage allowance inside the risk budget |
| Max margin | 25% of equity | prevents over-leverage at broker level |
| Max volume | 100 lots | broker maximum, additionally capped by risk |

Cash risk per trade includes commission and the risk buffer, not just the stop
distance. The runner budgets:

```
per_lot_risk = stop_distance * 100 USD + commission_per_lot + buffer
budget = min(equity * risk_fraction, daily_room, overall_room)
lots = floor(budget / per_lot_risk / 0.01) * 0.01
```

If `lots < 0.01`, the trade is rejected (`MIN_LOT_EXCEEDS_BUDGET` or
`BELOW_MIN_VOLUME`). It is never rounded up.

On a $5,000 account this is material: a 20.00 stop at 0.25% risk gives a
$12.50 budget, while 0.01 lots risks about $20 plus commission. The correct
outcome is `NO_TRADE`, not a larger position.

## 4. Broker-specific validation

Before an order:

- Re-read `account_info()` and require `trade_mode == 0` (DEMO) and the expected
  login/server.
- Build the symbol spec from live `symbol_info` **and** `order_calc_profit`.
- Reject non-finite/zero point, tick size, contract size, volumes, digits, or
  negative stops/freeze levels.
- Require buy/sell profit calculations to agree within 5%, and contract size
  and measured profit to agree within 5% when the profit currency is the account
  currency.
- Warn if the reported `trade_tick_value` disagrees with the measured value;
  size from measured profit, never the reported tick value.
- Require a filling mode supported by the symbol (IOC or FOK).
- Round volume **down** to the broker step.
- Reject stops inside the broker minimum stop distance or freeze distance.
- Re-run `order_check`; require retcode 0/10009, finite margin, margin <= 25%
  equity, and free margin >= 0.
- Re-verify DEMO mode immediately before `order_send`.

## 5. Daily and overall protection in operation

There are two enforcement points:

1. **Before each order**: `update_equity` with current broker balance/equity,
   then block if the compliance engine is near/at a limit or the internal soft
   guard is consumed. The order is sized against remaining daily and overall
   room, not just the per-trade risk.
2. **Independent monitoring**: `python scripts/mt5_demo_runner.py --monitor`
   re-reads balance/equity, updates compliance state, reconciles open intents,
   and activates the local kill switch if a firm limit or internal soft guard is
   reached. It **cannot send an order**. Run it from a scheduler while a demo
   position is open.

Monitoring does not auto-close a position. The defined policy for an open
position on halt is: the kill switch blocks new entries; a human closes the
position in the MT5 terminal and records the reason. Automatic liquidation is
deliberately not implemented in this mission because it would need its own
verified close-order path, spread/reject handling, and manual override design.

## 6. Exposure and correlation

The current mission is single-symbol XAUUSD with `max_open_positions = 1`.
Correlated exposure is therefore bounded by "one XAUUSD position", not by a
cross-asset correlation matrix. No claim is made that a multi-symbol portfolio
would be safe under these limits. If a second symbol is added later, a
correlation-aware portfolio cap must be designed and tested first.

## 7. Restart, duplicate, and disconnect behavior

- **Persistent state**: baseline, intent files, compliance snapshot, decision
  log, and kill switch live under `data/mt5_demo_state/` (or configured path).
- **Atomic writes**: JSON state is written to a temp file and replaced; decision
  lines are appended and fsynced.
- **One runner**: an exclusive lock prevents concurrent execution.
- **Duplicate prevention**: every send persists a `PENDING` intent first. A
  restart reconciles it against positions/orders by magic + comment. Unresolved
  outcomes block new entries.
- **Unknown result**: a timeout, exception, or uncertain retcode marks the intent
  `UNKNOWN`, activates the kill switch, and never retries the send.
- **Disconnect**: preflight fails closed; after a send, an uncertain state is
  treated as unknown, not success.
- **Foreign positions**: any position that does not match our magic/comment is
  treated as foreign and blocks new entries.
- **Kill switch**: a file and/or `QUANT_LAB_KILL_SWITCH` environment variable;
  clearing requires `--clear-kill --confirm`.

## 8. Fail-closed conditions

Any of these means `NO_TRADE`:

- Account not DEMO, unknown trade mode, wrong login/server, or disconnected
  terminal.
- AutoTrading disabled, EA trading disabled, or symbol not fully tradable.
- Stale, future-dated, crossed, or wide-spread tick.
- Invalid/unavailable symbol spec or profit calculation.
- Missing/ambiguous stop distance, minimum lot above budget, zero/unknown room.
- Margin above cap or margin unavailable.
- Stop/target inside broker minimum/freeze distance.
- Unresolved intent, orphan position, foreign position, or reconciliation error.
- Compliance state `BREACHED`, `HALTED`, phase target met, inactivity deadline,
  or unknown/unverified rules.
- Missing deployment gate, missing candidate, or any deployment blocker.

## 9. What is tested

- 46 runner tests: spec/profit mismatch, tick age/spread/crossing, server-time
  normalization, demo/non-demo, account mismatch, kill switch, floor sizing,
  min-lot rejection, room caps, margin cap, `order_check` rejects, no retry,
  timeout -> UNKNOWN + kill, fill verification, restart reconciliation,
  duplicate/open-position blocking, monitor alerts, config cross-checks.
- 36 compliance tests: daily reference, floating losses, static overall floor,
  phase target, minimum days, inactivity, halt, persistence/restart.
- 20 challenge-simulator tests: reset boundaries, sizing, phase transitions,
  breach accounting, soft guards, incomplete paths, cost cases.

Tests use fakes; they do not prove broker fill behavior. The live read-only
check proves the terminal and non-transmitting `order_check` plumbing only.

## 10. Residual gaps

- No continuous intra-trade liquidation; monitoring + human close only.
- No multi-symbol correlation model.
- No FundingPips demo credentials, so no FundingPips-specific margin, swap, or
  commission statement exists.
- Demo fills will still be cleaner than live fills.
- The internal 1%/4% guards are risk policy, not a claim that the challenge will
  pass; they intentionally end attempts early.
