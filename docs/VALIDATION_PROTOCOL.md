# Validation Protocol

1. Load saved historical data and normalize timestamps to UTC.
2. Physically exclude the latest 15% through `ResearchDataAccessor`.
3. Freeze parameters before each chronological test fold.
4. Generate signals only from information available at bar close.
5. Fill at the next bar open and apply entry/exit costs.
6. Use the reference simulator as the oracle; the fast simulator must match it.
7. Report gross, cost, and net returns separately.
8. Require positive net expectancy, at least 300 trades, registry-adjusted t-statistic above 3, net permutation percentile at least 95%, positive walk-forward coverage, neighborhood stability, cost stress, and regime coverage.
9. Record every trial with data hash, code hash, parameters, seed, and verdict.
10. Read the sealed final holdout only once through `scripts/run_holdout_once.py` after all research decisions are frozen.

Current limitations: `validation/walkforward.py` is a compact helper rather than a complete train/test optimizer; its DSR is an approximation; the current permutation implementation requires stronger signal-stream preservation; and the full 500-trial multi-timeframe study has not been run.
