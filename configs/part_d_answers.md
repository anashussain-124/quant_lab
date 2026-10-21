# Part D Decisions — Answered by Anas

Date: 2026-09-20

| # | Question | Answer |
|---|----------|--------|
| **1** | **Jurisdiction/exchange ToS?** | **Crypto spot/perpetual markets where legally permitted** |
| **2** | **Capital/infrastructure budget?** | *(Not explicitly stated — default assumption applies)* |
| **3** | **MT5 broker?** | **XAUUSD through MT5 demo** |
| **4** | **Historical data source?** | *(Default: CCXT for crypto, MT5 for XAUUSD)* |
| **5** | **Time horizon?** | **Intraday initially. Do not optimize for ultra-low-latency HFT** |
| **6** | **Risk tolerance?** | **Hard daily-loss limit, hard maximum drawdown, position-size limits, kill switch, no automatic increase of risk** |
| **7** | **Build-vs-buy on backtester?** | *(Default: build custom — already recommended in Phase 0)* |
| **8** | **Who approves promotions?** | **ONLY the user can promote a strategy. Every promotion requires a written experiment report.** |
| **9** | **Success/failure definition?** | **Research → Backtest → Paper/Demo → Evaluation → Promotion (pipeline defined)** |
| **10** | **Skills gap check?** | *(Default: agent explains as it works)* |

## Additional constraints stated:
- **MARKETS**: Crypto spot/perpetual (where legally permitted), XAUUSD via MT5 demo
- **EXECUTION**: Demo/paper only. No live capital until explicitly authorized.
- **SELF-IMPROVEMENT**: System may generate hypotheses, test, compare against baselines, reject failures, propose improvements
- **RESEARCH**: Continuous research, backtesting, walk-forward, OOS, Monte Carlo/stress, parameter stability, regime analysis
- **STRATEGY**: Multiple independent families, market-regime detection, stat-arb, cross-exchange spread/arb research, XAUUSD market-structure, transaction-cost/slippage modelling

## Default assumptions still needed:
- Q2 (budget) — needs explicit answer
- Q4 (data source) — needs explicit answer  
- Q7 (build-vs-buy) — can proceed with custom per Phase 0 analysis
- Q10 (skills gap) — agent will explain as it works
