# Data Dictionary and Provenance

## MT5 XAUUSD M5 snapshot

- File: `data/store/v4/mt5_XAUUSD_M5_a052314f19605be8.parquet`.
- Rows: 267,927.
- Full range: 2023-01-03 01:00 UTC through 2026-10-22 15:00 UTC.
- SHA-256: `a052314f19605be8497be9fd4df2168e7845c6c917bdc0be0aa5254164dd3952`.
- Pre-holdout rows: 227,737; final 15% remains sealed.
- Columns include timestamp, OHLC, volume, real volume, and spread.
- Audit recorded 1,027 gaps, zero duplicate timestamps, and zero invalid OHLC rows.

Timestamps are timezone-aware UTC at the adapter boundary. MT5 broker-server offset assumptions are documented in the existing protocol and require further DST-specific verification.

## Units

- OHLC: broker price units.
- Spread: MT5 points; v4 cost reports convert points to price using 0.01 for XAUUSD.
- Statistical results: R means return divided by configured stop distance.
- Monetary ledger: Decimal values in the simulator.

## Other sources

Binance Vision BTCUSDT and ETHUSDT minute archives were fetched and timestamp-unit detection supports milliseconds and microseconds. Dukascopy retry logic exists, but a verified multi-year Dukascopy dataset and cross-source comparison are not yet available.
