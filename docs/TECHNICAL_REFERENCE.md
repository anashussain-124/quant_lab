# Quant Lab — Complete Technical Reference

## 1. Market & Instrument Concepts

### XAUUSD Contract Specifications

```
Symbol: XAUUSD (Gold vs US Dollar)
Contract Size: 100 troy ounces per standard lot
Tick Size: 0.01 (one cent)
Tick Value: $0.10 per 0.01 lot per tick
           = $10.00 per 1.0 lot per tick
           = $0.10 per 0.01 lot per 0.01 price move

Position Value Formula:
  P&L = (exit_price - entry_price) × lots × 100 (oz)

Example:
  Buy 0.10 lots at $2,650.00, exit at $2,655.00
  P&L = ($2,655.00 - $2,650.00) × 0.10 × 100
      = $5.00 × 10.0
      = $50.00 profit

Stop Loss in Dollars:
  Risk = (entry - stop) × lots × 100

Position Size:
  lots = risk_dollars / (stop_distance × 100)
       = risk_dollars / (stop_points × 10)
  where stop_points = stop_distance / 0.01
```

### Session Theory

```
UTC Time Zones (no DST adjustment needed):

Asian Session:  00:00 - 08:00 UTC
  - Tokyo, Hong Kong, Sydney
  - Lower liquidity, choppy range
  - Mean-reverting behavior
  - Wider spreads

London Session:  08:00 - 16:00 UTC
  - European markets open
  - Highest liquidity for XAUUSD
  - Tightest spreads
  - Trend-following works best
  - ~40% of daily volume

New York Session:  16:00 - 22:00 UTC
  - US markets open
  - High volatility at open (15:00-16:00 UTC overlap)
  - Spread blowouts during overlap
  - Mixed behavior

Off-Hours:  22:00 - 00:00 UTC
  - Thin liquidity
  - Avoid trading
```

---

## 2. Technical Indicators

### 2.1 Donchian Channels

```
Definition:
  Donchian Channel shows the highest high and lowest low
  over a rolling window of N periods.

Formula:
  Upper(t) = max(High(t-N), High(t-N+1), ..., High(t-1))
  Lower(t) = min(Low(t-N), Low(t-N+1), ..., Low(t-1))

IMPORTANT: The current bar (t) is EXCLUDED from calculation.
This prevents look-ahead bias.

Breakout Signal:
  Long:  Close(t) > Upper(t)
  Short: Close(t) < Lower(t)

Implementation (O(N) per bar):
  For bar i with period P:
    upper[i] = np.max(high[i-P:i])  # Excludes bar i
    lower[i] = np.min(low[i-P:i])

Why 50 periods?
  - Captures ~2 days of H1 data
  - Balances between too many and too few signals
  - Robust across parameter sweep (40-60 all profitable)

Breakout Buffer (Variant B):
  buf = 0.10 × ATR(14)
  Long:  Close > Upper + buf
  Short: Close < Lower - buf
  Purpose: Filters false breakouts
```

### 2.2 Average True Range (ATR)

```
True Range:
  TR(t) = max(
    High(t) - Low(t),
    |High(t) - Close(t-1)|,
    |Low(t) - Close(t-1)|
  )

ATR (Simple Moving Average):
  ATR(t) = (1/N) × Σ(i=1 to N) TR(t-i+1)

ATR (Wilder's Smoothing):
  ATR(t) = (ATR(t-1) × (N-1) + TR(t)) / N

We use SMA for simplicity.

ATR as Volatility Measure:
  ATR% = ATR / Close × 100

Position Sizing with ATR:
  stop_distance = ATR × multiplier
  position_size = risk_amount / (stop_distance × contract_size)

Example:
  ATR = $21.54
  Multiplier = 2.5
  Stop distance = $53.85
  Risk = $12.50 (0.25% of $5,000)
  Position size = $12.50 / ($53.85 × 100) = 0.0023 ≈ 0.01 lots (minimum)

Wait, that's wrong. Let me recalculate:
  P&L per lot = price_move × 100 (oz)
  Risk per lot = stop_distance × 100
  Position size = risk_amount / (stop_distance × 100)

  Position size = $12.50 / ($53.85 × 100) = 0.0023 lots
  Minimum is 0.01 lots, so actual risk = $53.85 × 100 × 0.01 = $53.85

  For proper risk control:
  Position size = risk_amount / (stop_distance × 100)
  = $12.50 / ($53.85 × 100) = 0.0023 lots → round to 0.01 (minimum)
  Actual risk = 0.01 × $53.85 × 100 = $53.85

  This is too high for 0.25% risk!
  Need to use tighter stops or larger account.

  Actually:
  ATR = $21.54
  Stop distance = 2.0 × ATR = $43.08
  Position size = $12.50 / ($43.08 × 100) = 0.0029 lots
  Still below minimum.

  This is a known problem with small accounts and ATR stops.
  Solutions:
  1. Use smaller ATR multiplier (1.5x)
  2. Accept higher risk per trade
  3. Use fixed lot size with wider stops
  4. Increase account size

  Our system uses 0.01 lots minimum and accepts that actual risk
  may be higher than target when volatility is high.
```

### 2.3 Average Directional Index (ADX)

```
Directional Movement:
  +DM(t) = High(t) - High(t-1)  if > Low(t-1) - Low(t) and > 0
  -DM(t) = Low(t-1) - Low(t)    if > High(t) - High(t-1) and > 0

Directional Indicators:
  +DI(t) = 100 × Smoothed(+DM) / ATR(t)
  -DI(t) = 100 × Smoothed(-DM) / ATR(t)

Directional Index:
  DX(t) = 100 × |+DI(t) - -DI(t)| / (+DI(t) + -DI(t))

ADX:
  ADX(t) = Smoothed average of DX

Interpretation:
  ADX < 20:  Weak trend / ranging
  ADX 20-40: Moderate trend
  ADX > 40:  Strong trend

  +DI > -DI: Bullish trend
  -DI > +DI: Bearish trend
```

---

## 3. Trading Logic

### 3.1 Breakout Entry

```
Signal Conditions:
  1. Close > Donchian_Upper(50) + buffer × ATR(14)  → LONG
  2. Close < Donchian_Lower(50) - buffer × ATR(14)  → SHORT

Additional Filters:
  - Time: 08:00 <= UTC hour < 16:00 (London session)
  - No existing position
  - Not halted by safety limits
  - ATR > minimum threshold

Signal → Execution:
  Signal is generated at bar CLOSE.
  Execution happens at next bar's OPEN.
  (This introduces 1-bar delay but is realistic)

Order Types:
  MARKET order for immediate entry.
  STOP-LOSS attached at entry.
  No TAKE-PROFIT (use max holding bars or signal reverse).
```

### 3.2 Exit Logic

```
Exit Conditions (checked every bar):
  1. Stop Loss hit:
     Long:  Low(t) <= Entry - 2.5 × ATR
     Short: High(t) >= Entry + 2.5 × ATR

  2. Max Holding Bars:
     If bars_held >= 100: Exit at current close

  3. Reverse Signal:
     If opposite signal generated: Close + reopen

  4. End of Data: Exit at last available price

Exit Priority:
  Check stop loss first (intrabar).
  Check max holding.
  Check reverse signal.
```

### 3.3 Session Filter

```
Time Check:
  current_hour = bar.timestamp.hour
  is_london = 8 <= current_hour < 16

Why London?
  - Data shows 2025-2026 London range = $16.11 (vs Asian $5.98)
  - Volatility expansion = breakout follow-through
  - Tighter spreads = better execution
  - Institutional order flow

Session Windows Tested:
  08:00-16:00 (full London)
  08:00-14:00 (London morning)
  08:00-15:00 (extended morning)
  09:00-16:00 (skip open)
  10:00-16:00 (late London)
  16:00-22:00 (NY session)

Full London window chosen as most robust.
```

---

## 4. Risk Management

### 4.1 Position Sizing

```
Risk-Based Sizing:
  risk_amount = account_equity × risk_percentage
  stop_distance = ATR × atr_multiplier

  position_size = risk_amount / (stop_distance × contract_size)

  For XAUUSD:
  contract_size = 100 (oz per lot)
  point_value = $10 (per lot per point)

  position_size_lots = risk_amount / (stop_distance × 100)

Constraints:
  min_lot = 0.01
  max_lot = 10.0 (prop firm limit)
  lot_step = 0.01

  position = round(position / lot_step) × lot_step

Validation:
  actual_risk = stop_distance × position × 100
  assert actual_risk <= risk_amount × 1.10  # 10% tolerance

Problem with Small Accounts:
  When ATR is high, position_size < min_lot.
  Solution: Accept higher risk or wait for lower volatility.
```

### 4.2 Stop Loss

```
ATR-Based Stop:
  Long:  SL = Entry - 2.5 × ATR(14)
  Short: SL = Entry + 2.5 × ATR(14)

Why 2.5× ATR?
  - Wide enough to avoid noise-triggered stops
  - Tight enough to limit loss per trade
  - Robust across parameter sweep

Intrabar Stop Check:
  For LONG:  if Low(t) <= SL → exit at SL
  For SHORT: if High(t) >= SL → exit at SL

Execution Note:
  In live trading, stop is placed as pending order with MT5.
  In backtest, we simulate intrabar check using High/Low.
```

### 4.3 Drawdown Controls

```
Daily Loss Limit:
  if daily_pnl < -initial_balance × 0.05:  # -5%
    halt new entries for the day
    close existing positions

Max Drawdown (Trailing):
  peak_equity = max(peak_equity, current_equity)
  if current_equity < peak_equity × 0.90:  # -10% from peak
    halt trading
    close positions

Max Consecutive Losses:
  if consecutive_losses >= 10:
    halt for the day
    requires manual reset

Soft vs Hard Limits:
  Soft:  4% daily, 8% total (early warning)
  Hard:  5% daily, 10% total (mandatory halt)

DD Calculation:
  DD(t) = (Equity(t) - Peak(t)) / Peak(t) × 100

Implementation:
  peak = max(peak, equity)
  current_dd = (equity - peak) / peak * 100
```

---

## 5. Backtesting Methodology

### 5.1 Event-Driven Simulation

```
Loop Structure:
  for each bar i in data:
    1. Update mark-to-market P&L
    2. Check exits (stop, max holding, reverse signal)
    3. Check entries (signal + filters)
    4. Update equity curve
    5. Check safety limits

State Machine:
  IDLE → SIGNAL_DETECTED → ORDER_PENDING → POSITION_OPEN → EXITED → IDLE

No Look-Ahead:
  - Indicators use only bars before current
  - Signal at bar i uses bars [0, i-1]
  - Execution at bar i uses bar i data (close)
  - Profit/loss calculated using actual prices
```

### 5.2 Transaction Costs

```
Commission:
  commission = trade_value × commission_rate
  rate = 0.04% (typical for XAUUSD)
  round-trip = 0.08%

Slippage:
  slippage = price × lots × (slippage_bps / 10000) × 10
  2 bps typical for XAUUSD during London

Spread:
  spread_cost = spread_pips × point_value × lots
  2 pips × $10 × lots = $20 × lots per round-trip

Total Cost Per Trade:
  total_cost = commission + slippage + spread
  ≈ 0.04% + 0.02% + spread_pips × 0.02% of trade value

In Backtest:
  Apply cost to each trade's P&L:
  net_pnl = gross_pnl × (1 - commission_rate) - slippage_adjustment
```

### 5.3 Walk-Forward Testing

```
Method:
  Rolling window with fixed training/test split.

Windows Used:
  2018-2020 → 2021
  2019-2021 → 2022
  2020-2022 → 2023
  2021-2023 → 2024
  2022-2024 → 2025
  2023-2025 → 2026

Interpretation:
  Each window tests if parameters from training period
  generalize to the following year.

Success Criteria:
  Profit Factor > 1.0 in each window (minimum)
  Profit Factor > 1.2 in most windows (target)
  No window with PF < 0.8 (robustness)
```

### 5.4 Monte Carlo Simulation

```
Trade-Level Resampling:
  1. Extract all trade returns from backtest
  2. Randomly shuffle trade order (with replacement)
  3. Apply compounding: equity = equity × (1 + return)
  4. Track max DD for this simulation
  5. Repeat 10,000+ times

Metrics Extracted:
  - Median max DD across all simulations
  - Percentiles (75th, 90th, 95th, 99th)
  - Probability of exceeding DD limit
  - Expected longest losing streak

Compounding Formula:
  equity(t) = equity(t-1) × (1 + return(t))

Max DD Calculation:
  peak = max(peak, equity)
  dd = (equity - peak) / peak
  max_dd = min(max_dd, dd)

Why Monte Carlo?
  - Tests robustness to trade sequence
  - Accounts for path dependency
  - More conservative than historical DD
  - Identifies tail risks
```

---

## 6. Performance Metrics

### 6.1 Risk-Adjusted Returns

```
Sharpe Ratio:
  S = (R_p - R_f) / σ_p
  
  Where:
  R_p = mean return per trade (annualized)
  R_f = risk-free rate (assumed 0)
  σ_p = standard deviation of returns

  Annualized: S_annual = S × √252 (for daily) or √(252×24) for H1

  Interpretation:
  S < 0:   Losing strategy
  S 0-1:   Poor
  S 1-2:   Good
  S 2-3:   Excellent
  S > 3:   Exceptional (may indicate overfitting)

Sortino Ratio:
  So = (R_p - R_f) / σ_d
  
  Where:
  σ_d = downside deviation (only negative returns)

  More relevant than Sharpe for asymmetric strategies.

Calmar Ratio:
  C = Annualized Return / |Max Drawdown|
  
  Measures return per unit of drawdown risk.
  C > 1 is good, C > 2 is excellent.
```

### 6.2 Profitability Metrics

```
Profit Factor:
  PF = Gross Profit / Gross Loss
  
  PF > 1.0: Profitable
  PF > 1.2: Good
  PF > 1.5: Excellent
  PF > 2.0: Outstanding

Expectancy:
  E = (Win% × AvgWin) + (Loss% × AvgLoss)
  
  Per-trade expected profit.
  E > 0 required for profitability.

Win Rate:
  WR = Winning Trades / Total Trades
  
  Not meaningful alone.
  Can have WR < 40% with high PF (trend following).
  Can have WR > 60% with low PF (mean reversion with large losses).
```

### 6.3 Drawdown Metrics

```
Max Drawdown:
  MDD = min((Equity(t) - Peak(t)) / Peak(t)) × 100
  
  Worst peak-to-trough decline.

Average Drawdown:
  ADD = mean of all negative drawdown values
  
  More representative than max for typical experience.

Recovery Factor:
  RF = Net Profit / |Max Drawdown|
  
  How much profit per unit of risk taken.

Profit Concentration:
  What % of total profit comes from top 1, 5, 10 trades?
  
  High concentration (>50% from top 5) suggests
  edge depends on rare events — less robust.
```

---

## 7. Statistical Validation

### 7.1 Hypothesis Testing

```
Core Hypothesis:
  H0: Strategy has zero or negative expectancy
  H1: Strategy has positive expectancy

Test: Bootstrap confidence intervals on profit factor.
  If 95% CI lower bound > 1.0, reject H0.

Walk-Forward Test:
  Strategy must show positive expectancy in most windows.
  Not just overall average.

Parameter Robustness:
  Strategy should work across range of parameters.
  If only one exact combo works → overfit.
```

### 7.2 Regime Detection

```
Regime Classification (pre-entry information only):
  - ADX > 25 and Efficiency > 0.3 → TRENDING
  - ATR percentile > 70% → HIGH_VOL
  - ATR percentile < 30% → LOW_VOL
  - Otherwise → RANGING

Directional Efficiency:
  E = |Close(t) - Close(t-N)| / Σ|Close(i) - Close(i-1)| for i in [t-N, t]
  
  Measures how much of total movement was in net direction.
  E close to 1 = strong trend.
  E close to 0 = choppy range.

Regime-Conditional Testing:
  Calculate strategy performance separately for each regime.
  If strategy only works in one regime → regime-conditional edge.
  If works across all regimes → robust edge.
```

### 7.3 Multiple Testing

```
Family-Wise Error Rate:
  When testing 25 parameter combinations,
  some will appear profitable by chance.

  Bonferroni correction: α_adj = α / n_tests
  For α=0.05 and 25 tests: α_adj = 0.002

  Only Sharpe > 3.0 would survive this correction.
  Our results (Sharpe 3.0-3.3) are borderline.

Practical Approach:
  - Require profitability in walk-forward (not just overall)
  - Require stability across parameters
  - Require positive Monte Carlo median
  - Don't cherry-pick best result
```

---

## 8. Execution Infrastructure

### 8.1 MT5 API

```
Connection Flow:
  1. MT5.initialize() — start API
  2. MT5.account_info() — verify demo account
  3. MT5.symbol_select() — subscribe to market data
  4. MT5.copy_rates_from_pos() — fetch historical bars
  5. MT5.symbol_info_tick() — get current bid/ask
  6. MT5.order_send() — submit trade
  7. MT5.positions_get() — check open positions
  8. MT5.shutdown() — cleanup

Order Structure:
  {
    "action": TRADE_ACTION_DEAL,  # Immediate execution
    "symbol": "XAUUSD",
    "volume": lots,
    "type": ORDER_TYPE_BUY/SELL,
    "price": current_bid/ask,
    "sl": stop_loss_price,
    "tp": 0 (none),
    "deviation": max_slippage_in_points,
    "magic": unique_id,
    "comment": "strategy_name",
    "type_time": ORDER_TIME_GTC,
    "type_filling": ORDER_FILLING_IOC
  }

Response Handling:
  Check result.retcode:
  - TRADE_RETCODE_DONE (1000) = success
  - TRADE_RETCODE_REQUOTE = price changed
  - TRADE_RETCODE_REJECT = order rejected
  - TRADE_RETCODE_INVALID_VOLUME = lot size wrong
  - TRADE_RETCODE_INVALID_PRICE = price wrong
  - TRADE_RETCODE_INVALID_STOPS = SL/TP wrong
```

### 8.2 Scheduling (Cron)

```
Cron Pattern:
  0 8-15 * * 1-5
  │ │    │   │ │
  │ │    │   │ └─ Weekdays only (Mon-Fri)
  │ │    │   └─── Every month
  │ │    └─────── Every day
  │ └──────────── Hours 8-15 (covers 08:00-15:00 UTC)
  └────────────── Minute 0 (top of hour)

This runs at:
  08:00, 09:00, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00 UTC

Why hourly?
  - H1 candles close at the top of each hour
  - Signal can only be confirmed at bar close
  - One check per hour is sufficient
  - Reduces API calls and system load
```

### 8.3 State Management

```
State Persistence:
  strategy_state.json stores:
  - trades_today: counter reset daily
  - daily_pnl: accumulated P&L for today
  - consecutive_losses: running count
  - last_trade_date: for daily reset
  - open_position: current trade details

  State is loaded at startup, saved after each trade.

Trade Log:
  trades.json stores all completed trades:
  - entry/exit price
  - P&L
  - lots, SL
  - timestamp
  - exit reason

Equity Curve:
  equity_curve.json stores:
  - timestamp
  - equity value
  - used for drawdown calculation and reporting
```

---

## 9. Software Architecture

### 9.1 Project Structure

```
quant-lab/
├── engine/
│   ├── backtest_fixed.py       # Backtesting engine
│   ├── backtest_simple.py      # Simplified backtest
│   ├── research_standalone.py  # Full research suite
│   ├── prop_firm_research.py   # Prop-firm specific research
│   ├── donchian_research_challenge.py  # Deep dive analysis
│   ├── regime.py               # Regime detection + indicators
│   ├── strategy_analyzer.py    # Strategy matrix testing
│   ├── validation.py           # DSR/PSR/PBO/AUUC
│   └── validation_gates.py     # Gate framework
├── adapters/
│   ├── crypto/                 # CCXT crypto adapters
│   └── mt5/                    # MT5 XAUUSD adapter
├── data/
│   ├── fetch_extended.py       # Data fetching
│   ├── scrape_mt5.py           # MT5 data scraper
│   ├── save_max_data.py        # Save to parquet
│   └── store/
│       ├── max/                # Historical data
│       │   ├── XAUUSD_H1_MAX.parquet
│       │   ├── XAUUSD_H4_MAX.parquet
│       │   └── XAUUSD_D1_MAX.parquet
│       └── XAUUSD_H1_LONDON.parquet
├── tests/
│   ├── test_config.py
│   ├── test_orchestrator.py
│   └── ...
├── configs/
│   ├── part_d_answers.md
│   └── config.yaml.example
├── logs/
│   ├── mt5_donchian.log
│   ├── trades.json
│   ├── equity_curve.json
│   └── strategy_state.json
├── reports/
│   └── *.json daily reports
├── mt5_donchian_exec.py        # Live execution script
├── daily_report.py             # Report generator
├── prop_firm_research.py       # Research runner
└── README_STRATEGY.md          # Documentation
```

### 9.2 Key Design Decisions

```
1. No ML/LLM in risk engine:
   - All safety limits are deterministic code
   - Can be audited line-by-line
   - No "black box" decisions

2. Pre-entry information only:
   - Indicators never use future bars
   - Regime classified using historical data only
   - Signals use only closed bars

3. Minimum position size handling:
   - When ATR is high, actual risk > target
   - Strategy accepts this or waits for lower vol
   - Never hard-codes lot size

4. State persistence:
   - Strategy state survives restarts
   - Daily counters auto-reset
   - Trade history is append-only

5. Modular architecture:
   - Each component is testable independently
   - Backtest → Research → Live are separate
   - Easy to swap components
```

---

## 10. Mathematical Formulas Reference

```
P&L Calculation:
  P&L = (exit - entry) × lots × 100  (for XAUUSD)
  Return = P&L / equity

Position Size:
  lots = (equity × risk_pct) / (stop_distance × 100)

Drawdown:
  DD(t) = (equity(t) - peak(t)) / peak(t)

Sharpe:
  S = mean(returns) / std(returns) × √252

Sortino:
  So = mean(returns) / std(negative_returns) × √252

Profit Factor:
  PF = sum(winners) / abs(sum(losers))

Expectancy:
  E = (win_rate × avg_win) + (loss_rate × avg_loss)

ATR:
  ATR = mean(TR, period)
  TR = max(H-L, |H-C_prev|, |L-C_prev|)

Donchian:
  Upper = max(H, period)  [excluding current]
  Lower = min(L, period)  [excluding current]

ADX:
  DX = 100 × |+DI - -DI| / (+DI + -DI)
  ADX = smoothed_mean(DX)

Monte Carlo:
  equity = 5000
  for each shuffled return:
    equity *= (1 + return)
    track max DD
  repeat 10,000 times
  extract percentiles

Compounding:
  equity(t) = equity(t-1) × (1 + return(t))

Tick Value:
  1 point (0.01) = $0.10 per 0.01 lot
  1 point = $10.00 per 1.0 lot
  P&L = price_move × lots × 100
```

---

## 11. Failure Modes & Mitigations

```
1. Overfitting:
   - Mitigation: Walk-forward, parameter sweep, MC
   - Detection: Only works on specific dates

2. Regime Change:
   - Mitigation: Regime detection, volatility filter
   - Detection: Performance degrades over time

3. Execution Failure:
   - Mitigation: Retry logic, error logging
   - Detection: MT5 retcode monitoring

4. Data Quality:
   - Mitigation: Null/duplicate/spike detection
   - Detection: Data audit at startup

5. Floating Point:
   - Mitigation: Decimal for financial calculations
   - Detection: Tolerance checks

6. Look-Ahead Bias:
   - Mitigation: Indicators use only closed bars
   - Detection: Audit of signal timestamps
```

---

## 12. Prop Firm Compliance

```
Daily Loss Limit:
  max_daily_loss = initial_balance × 0.05
  Track: daily_pnl = sum of today's trade P&Ls
  Action: if daily_pnl < -max_daily_loss → halt

Max Drawdown (Equity-Based):
  peak = max(peak, equity)
  if equity < peak × 0.90 → halt

Max Drawdown (Balance-Based):
  if equity < initial_balance × 0.90 → halt

Consistency Rule:
  No single day > 40% of total profit
  Track: daily_pnl / total_pnl

News Filter:
  Avoid entries ±30 min around high-impact USD events
  Configurable per prop firm rules

Overnight/Weekend:
  Close positions before Friday close
  No new entries Thursday-Friday

EA Restrictions:
  Must comply with prop firm's EA rules
  Some firms require manual trading only
  Some restrict certain strategies
```
