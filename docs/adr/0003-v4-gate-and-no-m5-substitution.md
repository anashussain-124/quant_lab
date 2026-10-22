# ADR 0003: v4 validation gates and no M5 substitution

Date: 2026-10-22

## Decision

Treat a strategy as `FAIL` or `INCONCLUSIVE` unless the evaluation has at
least 300 trades, t-statistic greater than 3, a permutation result at or above
the 95th percentile, and consistent purged/embargoed walk-forward folds. The
best possible label in this repository is `PASS-CANDIDATE`; nothing is called
proven or promoted to `ACTIVE`.

Data-dependent M5 reports must use a verified MT5 demo refresh or a saved,
hashed M5 input. H1/H4 data must not be relabeled or substituted as M5.

## Context

The research lab prioritizes survival, data quality, realistic execution, and
robustness over apparent profitability. Multiple testing and small samples
make uncorrected backtest results unreliable.

## Consequences

Small or incomplete datasets produce an explicit insufficient-evidence result.
Framework tests and simulated examples can be validated without implying a
real trading edge. The final holdout remains sealed until the candidate has
passed earlier gates.
