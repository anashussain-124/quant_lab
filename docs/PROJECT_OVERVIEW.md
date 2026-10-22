# Quant Research Lab — Verified Project Overview

Status date: 2026-10-22. Branch: `redesign/v4`.

This repository is a research and demo/paper execution laboratory for XAUUSD and exploratory crypto research. It is not a live-trading system and contains no authorized live execution path. The required priority is survival, data quality, realistic execution, robustness, then profitability.

Current verified scope:

- XAUUSD MT5 demo historical data, primarily M5.
- Causal next-bar-open simulation with explicit spread/slippage assumptions.
- Five v4 hypothesis templates: opening-range breakout, prior-day sweep, volatility trend, z-score mean reversion, and session drift.
- A physically separated pre-holdout accessor.
- Search registry, causal resampling, fast/reference simulator comparison, statistical gates, and risk-profile loading.

The current evidence is negative: all five templates had negative gross expectancy in the full pre-holdout cost decomposition. No strategy is promoted beyond research and no result is proof of live viability.
