# Quant Lab — XAUUSD Donchian-50 MT5 Demo System

## System Overview

```
quant-lab/
├── mt5_donchian_exec.py    # Main execution script (run hourly)
├── daily_report.py         # Daily P&L and status report
├── engine/
│   ├── backtest_fixed.py   # Backtesting engine (verified)
│   └── research_standalone.py  # Full research suite
├── logs/
│   ├── mt5_donchian.log    # Execution log
│   ├── trades.json         # Trade history
│   ├── equity_curve.json   # Equity tracking
│   └── strategy_state.json # Persistent state
└── reports/                # Daily reports
```

## Strategy Configuration

| Parameter | Value |
|-----------|-------|
| Symbol | XAUUSD |
| Timeframe | H1 |
| Session | London 08:00-16:00 UTC |
| Entry | Donchian(50) breakout |
| Stop | 2.5x ATR(14) |
| Risk | 0.25% per trade ($12 at $5K) |
| Max holding | 100 bars |
| Max daily loss | 5% |
| Max drawdown | 10% |
| Max consec losses | 10 |

## Research Summary

### Backtest Results (2025-2026 unseen data)
- **Profit Factor**: 2.03
- **Sharpe**: 3.14
- **Max Drawdown**: -5.9%
- **Return**: +40.3%
- **Win Rate**: 29%
- **Expectancy**: +$11.30/trade

### Monte Carlo (10K simulations)
- Median DD: -6.4%
- P95 DD: -4.4%
- Breach probability: 5.7%

### Classification
**REGIME-CONDITIONAL EDGE CANDIDATE**

The strategy works because XAUUSD volatility tripled from 2025 onwards (ATR $4.89 → $16.85). Donchian breakouts need volatility for follow-through.

## Risk Warnings

⚠️ Edge only appears from 2025 onwards (2021-2024: PF 0.57, no edge)
⚠️ If volatility contracts back to 2021-2024 levels, strategy will lose money
⚠️ Monitor ATR(14) — if it drops below $8, consider pausing

## Next Steps

1. **MT5 Demo Forward Test** (30-50 trading days minimum)
   - Run the cron job hourly during London session
   - Monitor execution quality (slippage, rejected orders)
   - Compare demo PF vs backtest PF

2. **Position in System**
   - `mt5_donchian_exec.py` — main script (run hourly)
   - Cron job: `MT5 Donchian Strategy Monitor` (08:00-15:00 UTC, weekdays)
   - Telegram alerts for signals and errors

3. **Success Criteria for Funded Account**
   - Demo PF > 1.3 for 30+ days
   - Demo DD < 8%
   - No significant degradation from backtest
   - Execution quality acceptable (slippage < 2 pips)

## Emergency Controls

If any of these trigger, STOP and investigate:
- Daily loss approaches 5%
- Max drawdown approaches 8%
- 5 consecutive losses
- ATR drops below $8 (volatility regime change)
- MT3 demo balance drops below $4,500

**Remember:** "No trade" is a valid decision. The system should prefer survival over profit.
