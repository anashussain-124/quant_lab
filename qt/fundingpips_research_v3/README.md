# FundingPips Strategy Research Engine V3

**Purpose:** research and compare algorithmic strategy hypotheses for the FundingPips 2-Step Standard evaluation on a $5,000 account. This package is for historical research and demo-feed data only. It has no live order-placement or trade-management functions. It cannot guarantee a profitable strategy or a challenge pass.

The prior V2 files are not overwritten. V3 is deliberately a separate project so the previous backtester remains available for comparison.

## What is included

- `fundingpips_research_v3.py` — data loader, strategy definitions, execution/cost model, risk guards and sequential two-phase simulator.
- `test_fundingpips_research_v3.py` — regression tests for timestamps, OHLC validation, fills, costs, risk sizing, intrabar rule breaches, session closes, signal causality, inactivity and Phase 1 → Phase 2 sequencing.
- `requirements.txt` — Python dependencies.

## Initial risk and prop-firm configuration

- Starting balance: $5,000.
- Phase 1 target: 8% ($400); Phase 2 target: 5% ($250).
- Firm max loss: static 10% of starting balance ($500); the simulator checks equity, including floating P&L.
- Daily loss configuration: 5% by default. Select `--daily-loss-config 3pct` only if that is the purchased configuration. Under the current official 2-Step Standard rules the 3% add-on has no minimum-trading-day requirement; the 5% configuration requires at least 3 trading days in each phase.
- Initial risk per trade: 0.25% of current balance, subject to position-volume step and daily-room cap.
- Internal protections for challenge simulation: a 1% daily guard and 4% total-loss shutdown. These are our own safeguards, not FundingPips rules. Hitting the 4% guard stops that simulated attempt as `STOP_SOFT_TOTAL_GUARD`, without representing it as a firm hard breach.
- Daily reset: 00:00 platform time (UTC+3), represented as 21:00 UTC.
- Inactivity: a completed trade is required within 30 calendar days. Open positions do not reset this clock.
- Minimum trading days are counted conservatively from days on which trades close. Validate this interpretation against the purchased account's dashboard before relying on a fast-pass result.
- Time horizon: no evaluation timeout by default; attempts unfinished at the end of the available history are `INCOMPLETE`, never counted as passes.

Confirm the exact rules shown for your purchased account before relying on any modelled outcome.

## Strategies compared

All strategies use completed M5 bars, enter at the next bar open, use completed higher-timeframe data where applicable, and apply a London session filter in `Europe/London` time so daylight-saving changes are handled.

1. `asian_breakout`: completed 00:00–08:00 London Asian range, breakout close between 08:00–12:00, M15 EMA 50/200 trend filter, ATR(14) stop, initial 2R target.
2. `failed_breakout`: range-boundary sweep followed by a close back inside the Asian range, higher-timeframe trend confirmation, stop beyond the sweep candle and a minimum target-distance filter.
3. `trend_pullback`: M15 EMA 50/200 directional filter and M5 EMA 20 pullback/reclaim, ATR/swing stop and initial 2R target.
4. `donchian_baseline`: prior 20 M5 bars' high/low break, M15 EMA 50/200 filter, ATR stop and initial 2R target. This is retained as a baseline, not treated as a proven strategy.

These are explicit hypotheses to test—not established profitable strategies. The initial parameter values are fixed before seeing your next MT5 report. Do not keep tuning against the final segment until it becomes green.

## Windows setup

From PowerShell, enter the V3 folder. You can either use your existing project virtual environment or create a clean one:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q
```

Expected test output from this package: `22 passed` (the exact count may change if tests are added later).

## First run: MT5 data, all candidates

Make sure MetaTrader 5 is open and logged into the demo feed where XAUUSD is available, then run:

```powershell
python fundingpips_research_v3.py --mt5 XAUUSD --days 1095 --strategy all --daily-loss-config 5pct --outdir reports
```

This requests around three years of M5 history. MT5 returns bar timestamps in UTC, so **do not apply the server UTC offset to `--mt5` data**. The report prints the actual returned date range. If it is much shorter than requested, increase MT5's *Max bars in chart* setting, open XAUUSD M5 and scroll/load more history, then rerun.

To run one strategy at a time:

```powershell
python fundingpips_research_v3.py --mt5 XAUUSD --days 1095 --strategy asian_breakout --outdir reports
python fundingpips_research_v3.py --mt5 XAUUSD --days 1095 --strategy failed_breakout --outdir reports
python fundingpips_research_v3.py --mt5 XAUUSD --days 1095 --strategy trend_pullback --outdir reports
python fundingpips_research_v3.py --mt5 XAUUSD --days 1095 --strategy donchian_baseline --outdir reports
```

To match a purchased 3% daily-loss configuration, use `--daily-loss-config 3pct`. Do not select it just because it makes the simulation look better.

## CSV mode

CSV export should preferably contain UTC timestamps and OHLC columns, optionally `spread` in **symbol points**. For UTC time:

```powershell
python fundingpips_research_v3.py --csv XAUUSD_M5.csv --csv-timezone utc --strategy all --outdir reports
```

For a CSV with naive broker-server timestamps, use `--csv-timezone broker --server-utc-offset 3` only if the offset was actually UTC+3 for the full file. Broker offsets can change with DST; convert each historical timestamp to UTC correctly first when offsets differ over the file's date range.

CSV input defaults to `point=0.01`, contract size 100, min volume 0.01, volume step 0.01 and max volume 50. Override those values if the source broker's XAUUSD specification differs, for example `--point 0.001 --contract-size 100`.

### Optional high-impact news blackout

Create a CSV with one `time_utc` column containing the release timestamps in UTC. The filter suppresses new entry signals within 15 minutes before and after each timestamp by default, while preserving exit signals:

```powershell
python fundingpips_research_v3.py --mt5 XAUUSD --days 1095 --strategy all --news-blackout-csv high_impact_news_utc.csv --news-before-minutes 15 --news-after-minutes 15 --outdir reports
```

Use a reliable historical calendar and correct UTC timestamps. This option does not download or verify economic-calendar events, and it does not close positions already open at the blackout boundary.

## Costs: verify before trusting results

- The default commission is **$2.50 per lot per side**, equivalent to $5 round turn per lot. Treat that as an explicit assumption until the exact per-side/round-turn convention is confirmed from the evaluation/demo contract specifications or deal history.
- Default slippage is 0.05 in gold price units per fill.
- If the data has an MT5 `spread` column, its values are read as points and multiplied by the symbol's point size. Otherwise, the fallback is 30 points.
- The MT5 demo feed may not match FundingPips pricing, contract size or execution behavior. Replace assumptions with verified account values when possible.

## Output files

For each strategy, the `reports` directory contains:

- `*_research_trades.csv` — each entry/exit, volume, price, gross P&L, costs, net P&L, R multiple, holding bars and reason.
- `*_research_equity.csv` — historical marked equity series.
- `*_research_report.json` — summary metrics, configuration, chronological segments and rolling walk-forward results.
- `*_walk_forward.csv` — per-fold results from a fixed 365-day warm-up followed by 90-day out-of-sample windows, moving forward in 90-day steps. This evaluates predefined rules across changing time periods; it does not fit or optimize rules automatically.
- `*_sequential_attempts.csv` — overlapping historical start dates simulated as Phase 1 then Phase 2.
- `*_stress_attempts.csv` — sequential challenge attempts under spread ×2 and slippage ×2, commission unchanged.

The rolling attempts overlap, so they are **not independent samples** and the observed pass percentage is exploratory. An unfinished attempt at the data boundary is not a failed attempt, and the pass rate should be read alongside incomplete, hard-breach and internal-stop outcomes.

## Recommended research sequence

1. Run the tests and inspect the actual date range, spread points and symbol specification.
2. Run `--strategy all` on the same data and cost assumptions.
3. Compare net expectancy, profit factor, drawdown, costs and validation behavior. Reject negative or unstable candidates; do not optimize only for pass percentage.
4. Freeze a short list, then validate on genuinely unseen newer data and forward-demo observations. The V2 test period has already been inspected and should not be reused as an untouched lockbox.
5. Only consider a challenge fee if the strategy shows positive, robust out-of-sample behavior under conservative costs and the sequential simulator passes meaningful stress tests.

## Model limitations to understand

- M5 OHLC is not tick data. If stop and target can both be touched inside one candle, the stop is assumed first. An intrabar adverse excursion that could violate loss rules before a same-candle target is also treated conservatively.
- It approximates a bid OHLC feed using the recorded spread for ask-side logic. It cannot reproduce every tick, partial fill, requote, market closure, swap or broker-specific execution rule.
- The research report disables hard evaluation stops so we can measure the strategy's whole-sample expectancy; the sequential challenge report enables the firm rules and internal guards. Those are different questions and should not be mixed up.
- A backtest is evidence to assess, not proof the edge will persist. This code has no live execution path.

## Policy and technical references

- [FundingPips — 2 Step Standard](https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard): phase targets, loss limits, daily reset, minimum trading days and inactivity rules.
- [FundingPips — Trading Conduct and Security Standards](https://help.fundingpips.com/hc/en-us/articles/34505029138449-Trading-Conduct-and-Security-Standards): current EA/automation policy. The policy distinguishes personally developed EAs from third-party trade-generating EAs and may request source-code/version-control evidence. Check the policy before using any future automated execution on an evaluation account.
- [MetaTrader 5 Python `copy_rates_range` documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py): MT5 accepts UTC date ranges and data returned from the terminal has UTC bar times; available history depends on terminal chart-history settings.
