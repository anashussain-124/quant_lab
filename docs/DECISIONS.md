# Architecture Decisions

## Current decisions

- Historical research uses UTC-aware timestamps.
- The final holdout is physically separated and read only through the single-use holdout script.
- v4 signals are causal and fill at the next bar open.
- Decimal arithmetic is used in order/fill/PnL paths; vectorized indicators may use floats.
- A failed or unverified data source is reported, not silently replaced.
- No result may be described as proven or promoted to `ACTIVE`.

Existing detailed ADRs are in `docs/adr/`. This file is a current index, not a replacement for those records.
