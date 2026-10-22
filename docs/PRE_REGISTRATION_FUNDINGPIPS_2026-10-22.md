# Pre-Registration — FundingPips $5,000 Two-Step Challenge Evaluation

Date frozen: 2026-10-22, before running the challenge simulator.
Authorisation: user mission supplied 2026-10-22. This is a new program; it does not
re-open or tune the sealed v5 50-trial protocol.

## Purpose

Measure whether any of the 50 already-registered v5 XAUUSD H1 configurations, with
their rules and parameters frozen, would have passed a FundingPips 2-Step Standard
evaluation on a $5,000 account during 2019-2022. This is a **challenge-path
simulation**, not a new strategy search. No v5 parameter may be changed as a result.

A challenge pass rate is not evidence of edge. It cannot promote a strategy that
fails the v5 expectancy gates.

## Verified FundingPips rules

Sources accessed 2026-10-22:

- https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard
  (rules, daily-loss accordion, commission & leverage tab)
- https://help.fundingpips.com/hc/en-us/articles/34505029138449-Trading-Conduct-and-Security-Standards
  (EA policy)
- https://fundingpips.com/trading-objectives (phase objectives and minimum days)

Verified values:

| Rule | Value |
|---|---|
| Account sizes | $5K, $10K, $25K, $50K, $100K |
| Phase 1 profit target | 8% of starting balance ($400 on $5,000) |
| Phase 2 profit target | 5% of starting balance ($250 on $5,000) |
| Max overall loss | 10% of starting balance, static ($500 on $5,000) |
| Max daily loss | 3% or 5% of the daily reference, chosen configuration |
| Daily loss reference | higher of opening balance or opening equity |
| Minimum trading days | 3 with 5% daily loss; 0 with 3% daily-loss add-on |
| Trading period | unlimited |
| Inactivity | no completed trade for 30 consecutive calendar days |
| Daily reset | 00:00 platform time; FAQ states UTC+3, i.e. 21:00 UTC |
| News trading (evaluation) | allowed |
| Overnight/weekend holding (evaluation) | allowed |
| XAUUSD leverage (Standard) | metals 1:30 |
| XAUUSD contract | 1 lot = 100 oz |
| Commission | metals $5/lot standard account (side basis not stated) |
| Minimum volume | 0.01 lots |
| Automation | personal EA full automation allowed with proof of ownership |

The daily loss limit is **5% of that day's reference**, not 5% of the initial
balance: on a $5,000 account that has grown to $5,300, the day's limit is $265.
The maximum overall loss is static because the official wording is "10% below the
starting account size", not a trailing peak. Floating equity counts toward both
loss limits; the risk engine is tested that way.

## Unresolved items (fail closed)

These are recorded as assumptions or blockers, not verified facts:

1. The FundingPips XAUUSD swap schedule is not published outside the terminal and
   is not verified. The simulation uses v5's MetaQuotes-Demo swap points and a
   1.5x cost stress; swap is a small part of these short holds but is not proven.
2. Commission is stated as "$5 per lot" for metals on a Standard account but the
   page does not say whether that is per side or round turn. The primary simulation
   uses the v5 round-turn commission of $7 and adds a FundingPips delta for a
   $10 round turn ($5 per side); a $5 round-turn sensitivity is reported.
3. The daily-loss FAQ states platform time UTC+3, while the Master auto-close table
   implies UTC+2 in winter. A fixed 21:00 UTC boundary is used and the ambiguity is
   reported; it is a deployment blocker, not a simulation result.
4. No FundingPips demo server or purchased evaluation account is available locally.
   MT5 testing is against MetaQuotes-Demo only and is labelled `MT5_DEMO`.
5. The personal-EA proof-of-ownership review is a human operational step and has
   not been performed.

## Frozen inputs

- Trials: exactly the 50 v5 configurations (`dc01`-`dc10`, `em01`-`em10`,
  `ar01`-`ar10`, `mr01`-`mr10`, `sq01`-`sq10`).
- Signals and trade paths: `reports_v5/search_2026-10-22/trade_log.csv`, generated
  from the v5 backtest with development 2013-2018 and validation 2019-2022.
- Evaluation window: trades entered 2019-01-01 .. 2022-12-31 UTC.
- Holdout 2023-01-01 .. 2026-10-22 remains sealed and is not used.
- Costs: v5 base cost and a 1.5x all-in cost stress. The frozen v5 `CostModel`
  stress multiplier applies to spread, slippage, commission and swap, so the
  stress case applies 1.5x to all four components. The FundingPips commission
  delta is stressed the same way.

## Risk scenarios

Risk per trade is a fraction of **current equity at entry**, capped by remaining
daily and total room:

- 0.10%
- 0.25%
- 0.50%

Sizing uses the actual demo XAUUSD specification: contract size 100 oz, tick size
0.01, $1.00 P&L per 1.00 price move per lot, volume step 0.01, minimum 0.01.

```
budget = risk_fraction * equity_at_entry
raw_lots = budget / (stop_distance * 100)
lots = floor(raw_lots / 0.01) * 0.01
```

If the minimum 0.01 lot would risk more than the budget, the trade is skipped. This
matters on a $5,000 account: wide stops can make 0.25% risk impossible at the
broker minimum. Skipped trades are reported, not silently resized upward.

Internal guards, materially tighter than the firm limits:

- Daily soft guard: 1.0% of starting balance ($50).
- Overall soft guard: 4.0% of starting balance ($200).

Hitting an internal guard ends the attempt as `SOFT_GUARD`. It is not reported as a
firm breach. This is deliberate: a real account can stop trading, but the simulated
attempt is conservatively counted as failed.

## Challenge mechanics

Primary configuration (pre-registered as the decision case):

- Daily loss 5%, 3 minimum trading days.
- Phase 2 starts from $5,000. Profit from Phase 1 is not carried.
- Daily reference resets at 21:00 UTC to `max(balance, equity)`.
- Overall breach if equity or balance reaches $4,500 at any time.
- Daily breach if equity reaches `daily_reference * 0.95` at any time.
- Floating P&L is marked on every H1 bar using the worst intrabar price for the
  open direction (low for long, high plus observed spread for short). Daily resets
  that fall on a market closure are processed using the last known price, so a
  weekend gap is measured against the prior reference rather than hidden.
- A phase passes only when realized balance >= target and the minimum number of
  distinct reset days with a closed trade has been reached.
- If the target is reached before the minimum days, trading continues with the
  same frozen rules until both conditions hold or the attempt fails.
- A gap of 30 calendar days without a closed trade is `FAIL_INACTIVITY`.
- Data ending before an outcome is `INCOMPLETE`, never a pass.

Sensitivity configurations, reported separately and never used to select a winner:

- Daily loss 3%, 0 minimum trading days.
- Phase 2 balance carried from Phase 1 ending balance; in this hypothetical the
  carried balance becomes the Phase 2 starting size for both the 5% target and
  the 10% floor.
- Firm-rules-only diagnostic: same 5%/10% rules and 3 minimum days, but the
  internal 1%/4% soft guards are disabled. This isolates the firm's rules from
  the tighter internal risk overlay and is never used to select a candidate.

Amendment 2026-10-22, before any simulator result: the daily limit was corrected
from a fixed `$250` to 5% of the daily reference after retrieving the official
daily-loss accordion, which specifies a percentage of that day's baseline. The
firm-rules-only diagnostic was added to separate the internal overlay from the
firm's own limits. No result had been generated when these changes were made.

Amendment 2026-10-22, before any simulator result: the cost-stress description was
corrected. The frozen v5 `CostModel.stress` multiplier applies to spread,
slippage, commission and swap, not only to spread and slippage. The simulator
applies the multiplier to every cost component, including the FundingPips
commission delta. This is more conservative than the earlier wording. No result
had been generated when this change was made.

## Rolling start protocol

Start a new two-phase attempt every 14 calendar days from 2019-01-01 through
2022-07-01, requiring at least 180 calendar days of future data. Attempts overlap
and are therefore not independent. Pass rates are reported with that limitation;
no confidence interval is claimed as if the attempts were independent samples.

## Metrics

Per trial and scenario:

- Phase 1 pass rate.
- Phase 2 pass rate conditional on Phase 1 passing.
- Both-phase pass rate.
- Firm breach rate (daily or overall).
- Soft-guard rate.
- Incomplete rate.
- Median and interquartile days to pass.
- Maximum drawdown distribution.
- Near-breach frequency (within 1% of the overall limit).
- Trades skipped by minimum-lot risk.

## Decision rule

A configuration can be called `DEMO-READY` only if all of the following hold:

1. It passed every frozen v5 gate (none do: g2 and g4 failed 50/50).
2. Independent validation net expectancy is positive with a 95% bootstrap lower
   bound above zero.
3. The challenge simulator shows no firm breaches in the tested paths, or the
   breach rate and its uncertainty are explicitly acceptable for a demo-only test.
4. The risk engine and MT5 demo integration tests pass.

Because condition 1 already fails for all 50 trials, the expected outcome is
`NO_VALIDATED_CANDIDATE`. Challenge pass rates will be reported for completeness
and to quantify the risk of deploying a non-edge strategy.

## Stopping rule

No post-result tuning of the 50 trials. No holdout access. If no trial satisfies
the v5 gates, stop strategy research under this program and report Outcome B.
A further strategy program would require new data or a genuinely distinct,
pre-registered hypothesis class, not another pass over these results.
