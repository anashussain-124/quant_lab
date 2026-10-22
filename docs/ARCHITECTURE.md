# Current Architecture

```mermaid
flowchart LR
  MT5[MT5 demo M5 parquet] --> Accessor[ResearchDataAccessor]
  Accessor --> Train[Pre-holdout training frame]
  Train --> Resample[M15/H1/H4 completed-bar resampling]
  Train --> Features[Causal features]
  Train --> Generators[Five hypothesis generators]
  Generators --> Signals[SignalPlan]
  Signals --> Ref[Reference causal simulator]
  Signals --> Fast[Fast numpy-array simulator]
  Ref --> Trades[Decimal SimTrade ledger]
  Fast --> Trades
  Trades --> Costs[Gross/cost/net decomposition]
  Trades --> Gates[Statistical gates]
  Gates --> Registry[Append-only experiment registry]
  Gates --> Report[Reports/checkpoints]
  Holdout[Sealed final holdout] -. only run_holdout_once .-> HoldoutReader
```

The current v4 pipeline is real for saved parquet inputs, but it is not yet a complete production research platform. The older `engine/` and `adapters/` tree coexists with v4 and contains simulated or incomplete paths. Those paths must not be confused with the v4 real-data path.

Important boundaries:

- Strategies generate proposals only.
- The v4 simulator is historical/backtest logic, not an order adapter.
- Holdout access is token- and lock-protected in `data/holdout.py`.
- No module may promote a strategy to `ACTIVE`.
