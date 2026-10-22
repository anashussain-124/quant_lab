# FundingPips Rules — Verified Record

Version: `2026-10-22`. Environment: `MT5_DEMO` (all FundingPips accounts are
demo/simulated). This document is the human-readable companion to
`configs/fundingpips.yaml` and `engine/prop_firm_rules.py`.

## 1. Sources accessed 2026-10-22

- 2 Step Standard rules:
  https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard
- Trading conduct and security standards (EA policy):
  https://help.fundingpips.com/hc/en-us/articles/34505029138449-Trading-Conduct-and-Security-Standards
- Phase objectives and minimum trading days:
  https://fundingpips.com/trading-objectives

Rules can change. The version above is the only version this repository claims.
Re-verify before any future deployment; a change in any critical value must
invalidate the frozen configuration.

## 2. Account and phase parameters

| Rule | Verified value | Status |
|---|---|---|
| Account sizes | $5K, $10K, $25K, $50K, $100K | EMPIRICAL |
| Phase 1 profit target | 8% of starting balance ($400 on $5,000) | EMPIRICAL |
| Phase 2 profit target | 5% of starting balance ($250 on $5,000) | EMPIRICAL |
| Maximum overall loss | 10% of starting account size, **static** ($500 on $5,000) | EMPIRICAL |
| Daily loss limit | 5% of that day's reference (3% is an add-on variant) | EMPIRICAL |
| Daily reference | Higher of opening balance or opening equity | EMPIRICAL |
| Floating P&L | Counts toward daily and overall limits | EMPIRICAL |
| Minimum trading days | 3 with 5% daily target; 0 with 3% daily add-on | EMPIRICAL |
| Trading period | Unlimited | EMPIRICAL |
| Inactivity | 30 consecutive calendar days without a completed trade | EMPIRICAL |
| News trading (evaluation) | Allowed | EMPIRICAL |
| Overnight / weekend holding | Allowed during evaluation | EMPIRICAL |
| Daily reset | 00:00 platform time; FAQ states platform is UTC+3 = 21:00 UTC | EMPIRICAL with DST caveat |
| Account type | All FundingPips accounts are demo/simulated | EMPIRICAL |

Important basis distinction: the daily limit is **not** 5% of the initial
balance forever. On a $5,000 account that has grown to $5,300, the next day's
limit is $265 if the day opens at $5,300. The overall limit is different: it is
anchored to the starting account size and does not trail.

## 3. XAUUSD contract, margin and costs

| Item | Value | Status |
|---|---|---|
| Contract size | 1 lot = 100 oz | EMPIRICAL |
| Minimum volume | 0.01 lots | EMPIRICAL |
| Volume step | 0.01 lots | EMPIRICAL |
| Metals leverage, Standard | 1:30 | EMPIRICAL |
| Metals leverage, Swing / Swap-Free | 1:5 | EMPIRICAL |
| Commission, metals Standard | $5 per lot | EMPIRICAL, side basis unstated |
| Commission, metals Swing / Swap-Free | $10 per lot | EMPIRICAL, side basis unstated |
| Swap | Not published outside the terminal | UNVERIFIED |
| Demo feed depth | MT5 CFD feed; no true L2 depth | EMPIRICAL limitation |

The simulation uses a **$10 round-turn** as the primary case (assuming $5 per
side), a **$5 round-turn** sensitivity, and a 1.5x all-in cost stress. The side
basis is a deployment blocker until FundingPips confirms it in writing or in an
account statement.

## 4. Automation policy

- A personal Expert Advisor may run fully automated after FundingPips' proof-of-
  ownership review.
- Third-party EAs are allowed only as trade/risk managers, not as signal
  generators.
- The 1K Instant account has an exception; it is not the account used here.
- The Monthly Competition prohibits all EAs.
- Proof-of-ownership review is a human step and has **not** been performed.

This project's Python runner is not an MQL5 EA. Treat it as automation that
still requires the same disclosure and approval if it is ever pointed at a
FundingPips account. That step is not authorized in this mission.

## 5. Unresolved items that block deployment

1. **Daily reset DST.** The FAQ states platform time UTC+3; the Master
   auto-close table implies UTC+2 in winter. A fixed 21:00 UTC boundary is used
   for the simulation, but real DST transitions are not proven. This can shift
   the daily reference by one hour and change whether a trade is inside a day.
2. **XAUUSD swap.** Not published locally; the simulation uses MetaQuotes-Demo
   swap points and a stress multiplier. FundingPips swap is unverified.
3. **Commission side basis.** "$5 per lot" does not state per side vs round
   turn. Primary assumes $10 round turn.
4. **No FundingPips demo/evaluation credentials or server.** The local terminal
   is MetaQuotes-Demo, account 114370849, USD 100,000. It is not a $5,000
   FundingPips account.
5. **Personal-EA proof-of-ownership review.** Not performed.

The runner's configuration gate fails closed on all five. None of them may be
resolved by assumption in code.

## 6. How the rules are encoded

- `configs/fundingpips.yaml` stores the versioned values, the $5,000 account,
  the safety buffer (0.5% = $25), the 5% primary daily limit, the 3% sensitivity,
  and the deployment blockers.
- `engine/prop_firm_rules.py` holds immutable profiles with source URL, status,
  and verification date. Any profile that is not `EMPIRICAL` is rejected by the
  runner.
- `engine/prop_firm_compliance.py` enforces the static overall floor, the daily
  reference, floating equity, phase target, minimum days, inactivity, and a
  manual halt. It persists state atomically.
- `execution/mt5_demo_runner.py` independently enforces internal soft guards of
  1% daily and 4% overall of starting balance, so the account stops before the
  firm's hard limits. It never substitutes its own "better" rule.
- `research/challenge_v5.py` models the two-phase path. It applies the daily
  loss to the day's reference, marks floating P&L on every H1 bar, resets at
  21:00 UTC, and treats any unresolved/incomplete path as not a pass.

## 7. Claims this document does not make

- It does not claim a FundingPips account was purchased, connected, or passed.
- It does not claim the daily-reset timezone is fully resolved.
- It does not claim the swap or commission side basis is verified.
- It does not claim any strategy is eligible for deployment.

Every rule above is a statement about the published rules; simulated compliance
is not an account result.
