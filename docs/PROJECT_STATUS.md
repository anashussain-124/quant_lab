# Project Status — 2026-10-22

**Lifecycle: CLOSED. v4 closeout (tag `closeout-2026-10-22`) and the follow-up frozen
v5 swing search both ended with no candidate.** See `docs/CLOSEOUT.md` for the v4 record
and `docs/V5_RESULT.md` for the v5 protocol, result, and provenance.

## Verified

- Branch: `redesign/v4`.
- Configured test suite (`pytest tests/`): 392 passed, 5 warnings.
- Five v4 generators execute on real saved MT5 M5 data.
- Parameter-wiring defects in ORB and prior-day sweep were fixed and regression-tested.
- Fast/reference simulator equality is tested on three real windows.
- v5: 50 pre-registered trials on 64,651 real XAUUSD H1 bars, 2013-2018 development and
  2019-2022 validation, next-bar fills, causal spread fallback, spread/slippage/commission/swap,
  1.5x cost stress.
- v5 verdict: `NO_EDGE_FOUND`. 50/50 trials fail the bootstrap-CI gate (g2) and 50/50
  fail DSR >= 0.95 (g4); PBO = 0.550 (max 0.30). Best raw config dc05 (+0.3131R,
  n=174) is driven by 2020 and negative in 2021-2022; no config passed all ten gates.
- Leakage canary and shuffled-label tests pass; perturbation and random-entry null gates
  were not reached because no config survived earlier gates.
- Holdout 2023-2026 remains sealed (`candidate_frozen: false`, empty access log). No MQL5
  EA, Strategy Tester run, or demo was built because no candidate qualified.
- No strategy is a PASS-CANDIDATE or ACTIVE.

## Not verified

- Any strategy edge after costs on XAUUSD H1/H4.
- MT5 Strategy Tester reconciliation and demo survival (not applicable without a candidate).
- Any live viability.

## Single best next task

None under the frozen protocol; it forbids post-result tuning. A further attempt requires
a new pre-registration with a different hypothesis class, its own trial budget, and a fresh
holdout policy. The sealed 2023-2026 data must not be reused for that design.
