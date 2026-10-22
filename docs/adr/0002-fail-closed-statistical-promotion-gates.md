# ADR 0002: Fail closed on statistical promotion gates

## Status

Accepted — 2026-10-22

## Decision

Strategies remain `FAIL` unless all of the following are met on frozen,
causal, after-cost results: at least 300 trades, t-statistic greater than 3,
permutation percentile at least 95%, DSR at least 0.95, and consistent
positive purged walk-forward folds. The strongest result from this layer is
`PASS-CANDIDATE`; nothing here can promote a strategy to `ACTIVE`.

## Consequence

Small samples, negative expectancy, weak walk-forward consistency, and
multiple-testing risk cause failure or inconclusive results even when one
aggregate backtest looks attractive. The lab prefers `NO_TRADE` and more data
to unsupported promotion.
