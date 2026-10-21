DONCHIAN PROP-FIRM BACKTESTER V2 — HISTORICAL RESEARCH ONLY

What's new
- Default strategy: M5 Donchian breakout (20 completed prior bars), filtered by completed M15 EMA 50/200 trend.
- Entry signals only during 08:00–12:00 Europe/London local time; daylight-saving time is handled by the timezone database.
- ATR(14) stop at 1.5 x ATR; target initially 2R from the signal close. Because fills occur at next bar open, actual reward/risk may differ when price gaps.
- Risk sizing remains 0.25% of current balance per trade, with the existing soft daily stop and daily-room cap.
- --oos adds chronological 60% development / 20% validation / 20% final holdout segment metrics.
- --strategy meanreversion retains the earlier strategy for comparison.
- Commission, slippage and fallback spread are explicit CLI parameters.
- Existing FundingPips 5% and 3% daily-loss presets are retained.

Safety
- Historical bars only. The code has no order_send/order_check/position-management calls.
- This does not promise profitability or a prop-firm pass.
- Default commission ($3.50 per lot per side), slippage ($0.05 price units), and fallback spread (25 points) are illustrative assumptions, not confirmed FundingPips execution costs. Verify the actual account contract/commission convention and use actual exported spread data when available.
- --server-utc-offset must match the MT5 broker server time for the requested historical period. London session is converted from UTC timestamps to Europe/London and DST-adjusted.
- Data from a MetaQuotes demo feed may differ from the evaluation platform.
- Synthetic data is for plumbing tests only, never performance claims.

Windows setup
1. Back up your existing propfirm_backtest.py and test_propfirm_backtest.py.
2. Extract these two .py files into C:\Users\<USER>\Downloads\qt, replacing the files only after the backup.
3. Activate your environment:
   .\.venv\Scripts\Activate.ps1
4. Run tests:
   python -m pytest -q
   Expected: 14 passed

Run the default Donchian strategy on 365 days of XAUUSD M5 MT5 history:
   python propfirm_backtest.py --mt5 XAUUSD --days 365 --server-utc-offset 3 --preset fundingpips_2step_p1 --strategy donchian --oos

Phase 2:
   python propfirm_backtest.py --mt5 XAUUSD --days 365 --server-utc-offset 3 --preset fundingpips_2step_p2 --strategy donchian --oos

Compare the prior mean-reversion strategy:
   python propfirm_backtest.py --mt5 XAUUSD --days 365 --server-utc-offset 3 --preset fundingpips_2step_p1 --strategy meanreversion --oos

Costs can be adjusted explicitly, for example:
   --commission-per-lot-side 5 --slippage 0.08 --fallback-spread-points 30
These values are examples only; choose values supported by the actual account/data.

Out-of-sample interpretation
- The 60/20/20 report is chronological and resets the challenge balance for each segment, but it is a preliminary split, not a substitute for walk-forward tests across multiple regimes.
- Indicators are computed causally from the history; each segment's trading/equity simulation starts with fresh balance. Do not tune repeatedly against the final holdout.
- The rolling challenge attempts overlap and are not independent. Treat pass rates as exploratory estimates, not reliable probability forecasts.
- Phase 1 and Phase 2 are still separate simulations, not a sequential two-phase challenge simulator.

Pass-time sanity guard
- Rolling challenge results now flag a reported PASS as INVALID_SIMULATION if it violates the configured minimum trading-day count or the conservative minimum elapsed-time sanity check. Investigate any such result before trusting pass-rate metrics.
