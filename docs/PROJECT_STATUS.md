# Project Status — 2026-10-22

**Lifecycle: CLOSED for research on 2026-10-22 (tag `closeout-2026-10-22`).** See `docs/CLOSEOUT.md` for the final record and archive details.

## Verified

- Branch: `redesign/v4`.
- Full pytest: 406 passed, 5 warnings.
- Five v4 generators execute on real saved MT5 M5 data.
- Parameter-wiring defects in ORB and prior-day sweep were fixed and regression-tested.
- Fast/reference simulator equality is tested on three real windows.
- Full pre-holdout row count is 227,737.
- Cost decomposition found negative gross expectancy for all five templates in the tested configuration.
- No strategy is a PASS-CANDIDATE or ACTIVE.

## Not verified

- Full 500-trial multi-timeframe search.
- Three-year Dukascopy collection and cross-source comparison.
- Complete dependence-adjusted statistical validation.
- Demo/paper survival of any strategy.
- Any live viability.

## Single best next task

Implement the full frozen-parameter multi-timeframe search with persisted signal streams, cost-aware constraints, and registry-backed permutation/null tests, then run it only on the pre-holdout partition.
