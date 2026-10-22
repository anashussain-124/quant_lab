# Data inventory — 2026-10-21

All checked-in Parquet files were inspected with the project Python 3.13.15
environment. Timestamps were parsed as UTC. `OHLC invalid rows` means a
non-positive price, high below low, or open/close outside the high-low range.

| File | Rows | Modal interval | UTC range | Duplicates | OHLC invalid | Spread column |
|---|---:|---:|---|---:|---:|---|
| `data/store/binance_btc_BTC_USDT__20260921_170729.parquet` | 300 | 1h | 2026-09-09 to 2026-09-21 | 0 | 0 | no |
| `data/store/binance_btc_BTC_USDT__20260921_170836.parquet` | 300 | 1h | 2026-09-09 to 2026-09-21 | 0 | 0 | no |
| `data/store/binance_btc_BTC_USDT__20260921_170934.parquet` | 300 | 1h | 2026-09-09 to 2026-09-21 | 0 | 0 | no |
| `data/store/max/XAUUSD_D1_MAX.parquet` | 5,725 | 1d | 2004-06-11 to 2026-09-22 | 0 | 0 | no |
| `data/store/max/XAUUSD_H1_LONDON.parquet` | 17,526 | 1h | 2018-03-27 to 2026-09-22 | 0 | 0 | no |
| `data/store/max/XAUUSD_H1_MAX.parquet` | 50,000 | 1h | 2018-03-26 to 2026-09-22 | 0 | 0 | no |
| `data/store/max/XAUUSD_H4_MAX.parquet` | 34,132 | 4h | 2004-06-11 to 2026-09-22 | 0 | 0 | no |
| `data/store/mt5_xauusd_XAUUSD__20260921_170934.parquet` | 1,000 | 1h | 2026-07-20 to 2026-09-21 | 0 | 0 | no |

Conclusion: none of the committed XAUUSD files is M5. The checked-in files
cannot reproduce the previous V3 M5 reports. Their report metadata says
`source=MT5:XAUUSD`, with 99,974 bars for the shorter run and 210,218 bars for
the three-year run, but no raw snapshot or data hash was stored.

The verified MT5 session on 2026-10-21 initialized successfully against
`MetaQuotes-Demo`, returned account trade mode `0` (demo), and returned 69,018
M5 bars for the last 365 days. The 1,095-day run returned 210,334 M5 bars from
2023-10-23 01:00 UTC through 2026-10-21 06:25 UTC. That fetched dataset was not
committed; future runs must use `--save-input`.
