# Amendment 1 to Pre-Registration v5 — Data window and spread model

Date: 2026-10-22. Written **before any v5 strategy test**.

## Reason

The data audit found that the MT5 H1 `spread` column is unavailable for 100% of
bars in 2007-2009, 85% of 2010, and 11-18% of 2011-2012. It is also missing for
multi-month patches in 2021 (2021-04-27 .. 2021-07-16) and 2022 (2022-04-27 ..
2022-12-30), distributed randomly across hours and weekdays. Treating those zeros
as zero cost would make early trades and parts of the validation period look
artificially profitable. Price/volume data are intact.

## Change 1: analysis window

- Warmup: 2012-01-01 .. 2012-12-31.
- Development: **2013-01-01 .. 2018-12-31** (was 2008-01-01 .. 2018-12-31).
- OOS validation: **2019-01-01 .. 2022-12-31** (unchanged).
- Sealed holdout: **2023-01-01 .. 2026-10-22** (unchanged).
- Stability folds: 2017-2022 (unchanged).

The 2007-2012 price history is not used because its transaction-cost field cannot
be audited. This reduces the development sample but does not change any strategy
rule, parameter set, gate, or holdout date.

## Change 2: causal spread model

For every bar, the simulator uses:

```
observed = bar.spread
fallback = median of the last 100 non-zero spreads (strictly past bars)
effective_spread_points = max(observed if observed > 0 else fallback, 10)
```

The 10-point floor is conservative for 2020-2026, when observed spreads are
typically 4-9 points. For 2013-2019, observed spreads of 15-43 points dominate.
A missing-spread bar never receives a zero cost.

All other cost assumptions are unchanged: 0.5 point slippage per side, USD 7 per
lot round-turn commission, MT5 swap, 1.5x cost stress.

## Change 3: data-quality gate

The run is valid only if, in 2013-2022, fewer than 1% of bars have invalid OHLC,
zero/negative prices, duplicate timestamps, or missing volume. The spread rule
above is applied to all remaining gaps and is reported per year.

## Binding

Original protocol SHA-256 is recorded in `data/store/v5/manifest.json`.
This amendment is part of the frozen protocol. No further changes are allowed
after the first strategy result is seen.
