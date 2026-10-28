# MT5 XAUUSD Historical Data Export & Initial Audit

- Created (UTC): 2026-10-22T21:45:38.932161+00:00
- Broker symbol: `XAUUSD`
- Requested lookback: 3 years
- Broker/server: `MetaQuotes-Demo`
- Data use: research and offline backtesting only; no orders are placed.

## Export audit

| Timeframe | Rows | Actual start UTC | Actual end UTC | Duplicate timestamps | Invalid OHLC rows | Gaps > one bar | Largest gap (minutes) |
|---|---:|---|---|---:|---:|---:|---:|
| M1 | 1,052,792 | 2023-10-23T01:00:00+00:00 | 2026-10-22T21:44:00+00:00 | 0 | 0 | 924 | 4442.0 |
| M5 | 210,781 | 2023-10-23T01:00:00+00:00 | 2026-10-22T21:40:00+00:00 | 0 | 0 | 818 | 4445.0 |

Gap counts are diagnostic only: normal weekends, market breaks, holidays, and missing broker history may all create timestamp gaps. Inspect them before classifying gaps as missing data.

## Symbol snapshot

- Digits: `2`
- Point: `0.01`
- Trade tick size: `0.01`
- Tick value: `0.1`
- Contract size: `100.0`
- Volume min/step/max: `0.01` / `0.01` / `100.0`
- Snapshot spread (price units): `0.30999999999994543`
- Snapshot spread is not a historical spread distribution; historical tick data or an execution-cost assumption is needed for that.

## FundingPips assumption

The file `fundingpips_5k_2step_standard_assumptions.json` records the current official 2-Step Standard figures as research assumptions. Confirm the account model and daily-loss add-on on the actual dashboard before using them.

## Not yet a strategy-performance report

This export establishes the historical-data foundation. A Pine Script strategy cannot be run directly in MT5. Next, the strategy rules must be ported faithfully to a Python bar backtester or an MQL5 Expert Advisor, then validated against these exported bars with realistic costs. Do not describe this data audit as a completed strategy backtest.

## Source documentation

- MetaTrader 5 Python `copy_rates_range`: https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py
- MT5 Market Watch/history export: https://www.metatrader5.com/en/terminal/help/trading/market_watch
- FundingPips official 2-Step Standard rules: https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard
