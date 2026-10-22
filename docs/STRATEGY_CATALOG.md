# Strategy Catalog

## v4 hypothesis generators

| Template | Implemented rule | Parameters | Exits | Evidence |
|---|---|---|---|---|
| `opening_range_breakout` | prior rolling high/low breakout with fractional range buffer | `range_minutes`, `buffer_r` | fixed stop/target, 24-bar hold | negative gross on pre-holdout M5 |
| `prior_day_sweep` | prior completed daily high/low sweep and reclaim | `lookback_days`, `reclaim_bars` | fixed stop/target, 48-bar hold | negative gross on pre-holdout M5 |
| `volatility_trend` | causal fast/slow EMA direction | `ema_fast`, `ema_slow` | fixed stop/target, 72-bar hold | negative gross on pre-holdout M5 |
| `zscore_mean_reversion` | causal rolling z-score extremes, fade signal | `window`, `entry_z` | fixed stop/target, 24-bar hold | negative gross on pre-holdout M5 |
| `session_drift` | prior-bar close direction during UTC hour window | `start_hour_utc`, `end_hour_utc` | fixed stop/target, 24-bar hold | negative gross on pre-holdout M5 |

All signals are generated at bar close and filled at the next bar open in the v4 simulator. Stops are checked using high/low, with stop-first ordering if both barriers are touched. Exact formulas are in `hypotheses/generators.py` and `execution/causal_sim.py`.

## Public strategy interface

`strategies/real_strategies.py` contains `MomentumBreakout`, `MeanReversionZScore`, and `FundingContrarianDirectional`. The registry also contains explicit placeholders. `FundingContrarianDirectional` is directional contrarian logic, not delta-neutral funding carry; true carry requires spot/perpetual legs, basis, funding payments, and hedge accounting.

No strategy has credible out-of-sample evidence or permission to trade live.
