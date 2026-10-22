# ADR 0001: Require reproducible evidence before strategy optimization

## Status

Accepted — 2026-10-21

## Decision

The lab will not tune strategy parameters solely to increase historical profit
factor. A candidate must be evaluated with the existing chronological split,
walk-forward windows, realistic costs, and a sealed lockbox on real M5 data.
Every generated report must identify its environment, data hash, code version,
configuration hash, and data-quality checks.

## Context

The checked-in historical reports show unstable results: the trend-pullback
candidate has aggregate profit factor 1.068, but validation profit factor 0.817
and a recent walk-forward fold at 0.517. The repository does not contain the
M5 source data needed to reproduce or safely retune those results.

Synthetic random-walk data is retained for plumbing tests only and cannot be
used as evidence of profitability.

## Consequence

The V3 report now records provenance and data-quality metadata. A future
optimization run must use a verified M5 export or MT5 demo history and must
report whether the candidate survives out-of-sample and stress evaluation.
