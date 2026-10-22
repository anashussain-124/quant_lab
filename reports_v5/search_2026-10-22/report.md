# v5 swing search — 2026-10-22T17:52:41.679224+00:00

Environment: HISTORICAL_REAL. Data: 64651 H1 XAUUSD bars, 2012-01-03 01:00:00+00:00 .. 2022-12-30 23:00:00+00:00.
Protocol SHA-256: `d02a6a8ef71f3122dc23ce967a9b275a25c9036cf252aaede253ee0b82646b56`; amendment: `33538557b71e8f875babaa470abacb8c7f42e2a3353cb1c8b149e856a820cd7c`. Code: `c7526a11721b009012b11f21511f888d8c9b90b4-dirty`.
Sealed holdout accessed: **no**.

## Verdict

No configuration passed all ten gates. The holdout remains sealed.

PBO (CSCV, 6 folds, 20 splits): **0.550**.
Gate failures across 50 trials: g1=3/50, g2=50/50, g3=37/50, g4=50/50, g5=50/50, g6=31/50, g7=50/50, g8=38/50, g9=50/50, g10=31/50.

`g7` (perturbation) and `g9` (random-entry null) were not reached by any
configuration because earlier gates failed; they are reported as not passed,
not as evaluated-and-failed.

## Data quality 2013-2022

{
  "invalid_ohlc": 0.0,
  "zero_or_negative_price": 0.0,
  "duplicate_timestamp": 0.0,
  "missing_volume": 0.0,
  "missing_values": 0.0
}
Gate (<1% each): **PASS**.

## All 50 pre-registered trials (OOS 2019-2022)

| ID | Family | n | Mean net R | 95% CI | Max DD R | Folds+ | DSR | Stress>0 | Failed gates |
|---|---|---:|---:|---|---:|---:|---:|---|---|
| dc01 | donchian_trail | 449 | +0.0112 | [-0.1289, +0.1601] | 39.4 | 3/6 | 0.005 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| dc02 | donchian_trail | 401 | +0.0209 | [-0.0907, +0.1380] | 29.2 | 3/6 | 0.012 | True | g2,g3,g4,g5,g7,g8,g9,g10 |
| dc03 | donchian_trail | 309 | +0.0837 | [-0.1028, +0.2830] | 22.7 | 4/6 | 0.067 | True | g2,g4,g5,g7,g9 |
| dc04 | donchian_trail | 237 | +0.1484 | [-0.0638, +0.3888] | 14.3 | 3/6 | 0.203 | True | g2,g3,g4,g5,g7,g8,g9 |
| dc05 | donchian_trail | 174 | +0.3131 | [-0.0586, +0.7365] | 21.8 | 3/6 | 0.411 | True | g2,g3,g4,g5,g7,g8,g9 |
| dc06 | donchian_trail | 157 | +0.1883 | [-0.0963, +0.5154] | 22.6 | 3/6 | 0.310 | True | g2,g3,g4,g5,g7,g8,g9 |
| dc07 | donchian_trail | 363 | +0.0905 | [-0.0536, +0.2421] | 17.1 | 4/6 | 0.089 | True | g2,g4,g5,g7,g9 |
| dc08 | donchian_trail | 293 | +0.0534 | [-0.0851, +0.1991] | 19.3 | 4/6 | 0.062 | True | g2,g4,g5,g7,g9 |
| dc09 | donchian_trail | 352 | +0.1131 | [-0.0774, +0.3322] | 34.2 | 3/6 | 0.068 | True | g2,g3,g4,g5,g7,g8,g9,g10 |
| dc10 | donchian_trail | 193 | +0.1251 | [-0.0858, +0.3497] | 18.8 | 3/6 | 0.237 | True | g2,g3,g4,g5,g7,g9 |
| em01 | ema_pullback | 520 | -0.0298 | [-0.1626, +0.1137] | 43.7 | 2/6 | 0.001 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| em02 | ema_pullback | 307 | +0.0663 | [-0.1479, +0.3051] | 24.4 | 3/6 | 0.035 | True | g2,g3,g4,g5,g7,g8,g9 |
| em03 | ema_pullback | 367 | -0.0869 | [-0.2323, +0.0734] | 51.2 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| em04 | ema_pullback | 608 | -0.0419 | [-0.1893, +0.1174] | 71.3 | 3/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| em05 | ema_pullback | 218 | +0.0583 | [-0.1523, +0.3006] | 19.4 | 2/6 | 0.067 | True | g2,g3,g4,g5,g7,g8,g9 |
| em06 | ema_pullback | 475 | -0.0278 | [-0.1270, +0.0757] | 31.3 | 3/6 | 0.001 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| em07 | ema_pullback | 68 | +0.0627 | [-0.3708, +0.6400] | 14.7 | 3/6 | 0.191 | True | g1,g2,g3,g4,g5,g7,g8,g9 |
| em08 | ema_pullback | 108 | +0.2085 | [-0.1434, +0.6211] | 12.2 | 4/6 | 0.363 | True | g2,g4,g5,g7,g9 |
| em09 | ema_pullback | 167 | +0.0587 | [-0.2391, +0.3911] | 18.6 | 3/6 | 0.086 | True | g2,g3,g4,g5,g7,g9 |
| em10 | ema_pullback | 52 | +0.0095 | [-0.3596, +0.4317] | 9.3 | 4/6 | 0.190 | False | g1,g2,g4,g5,g6,g7,g9 |
| ar01 | asian_range_breakout | 794 | -0.0479 | [-0.1214, +0.0279] | 45.9 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| ar02 | asian_range_breakout | 788 | -0.0166 | [-0.0962, +0.0640] | 38.4 | 4/6 | 0.000 | False | g2,g4,g5,g6,g7,g8,g9,g10 |
| ar03 | asian_range_breakout | 744 | -0.0423 | [-0.1191, +0.0367] | 47.8 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| ar04 | asian_range_breakout | 738 | -0.0277 | [-0.1088, +0.0558] | 42.2 | 3/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| ar05 | asian_range_breakout | 794 | -0.0446 | [-0.1047, +0.0154] | 38.4 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| ar06 | asian_range_breakout | 736 | -0.0187 | [-0.0885, +0.0492] | 36.9 | 3/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| ar07 | asian_range_breakout | 790 | +0.0287 | [-0.0607, +0.1205] | 28.1 | 5/6 | 0.001 | True | g2,g4,g5,g7,g9,g10 |
| ar08 | asian_range_breakout | 740 | +0.0328 | [-0.0583, +0.1287] | 28.4 | 5/6 | 0.002 | True | g2,g4,g5,g7,g9,g10 |
| ar09 | asian_range_breakout | 785 | +0.0029 | [-0.0731, +0.0792] | 33.2 | 4/6 | 0.000 | False | g2,g4,g5,g6,g7,g9,g10 |
| ar10 | asian_range_breakout | 744 | -0.0198 | [-0.1034, +0.0670] | 45.1 | 4/6 | 0.000 | False | g2,g4,g5,g6,g7,g8,g9,g10 |
| mr01 | lowvol_meanrev | 634 | -0.0756 | [-0.1554, +0.0035] | 56.8 | 0/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| mr02 | lowvol_meanrev | 291 | -0.0556 | [-0.1766, +0.0664] | 24.0 | 1/6 | 0.001 | False | g2,g3,g4,g5,g6,g7,g8,g9 |
| mr03 | lowvol_meanrev | 419 | -0.0795 | [-0.2052, +0.0460] | 56.4 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| mr04 | lowvol_meanrev | 180 | -0.0867 | [-0.2541, +0.0853] | 21.2 | 0/6 | 0.004 | False | g2,g3,g4,g5,g6,g7,g8,g9 |
| mr05 | lowvol_meanrev | 242 | +0.0209 | [-0.1370, +0.1893] | 22.2 | 4/6 | 0.039 | True | g2,g4,g5,g7,g8,g9 |
| mr06 | lowvol_meanrev | 90 | -0.1568 | [-0.3937, +0.0856] | 19.8 | 1/6 | 0.010 | False | g1,g2,g3,g4,g5,g6,g7,g8,g9 |
| mr07 | lowvol_meanrev | 578 | -0.1191 | [-0.2153, -0.0162] | 73.2 | 0/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| mr08 | lowvol_meanrev | 121 | -0.2035 | [-0.4255, +0.0285] | 30.7 | 2/6 | 0.002 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| mr09 | lowvol_meanrev | 400 | -0.0589 | [-0.1561, +0.0350] | 41.5 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| mr10 | lowvol_meanrev | 229 | -0.0714 | [-0.2601, +0.1213] | 29.4 | 3/6 | 0.005 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| sq01 | squeeze_breakout | 518 | -0.0312 | [-0.1497, +0.0915] | 43.1 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| sq02 | squeeze_breakout | 497 | +0.0369 | [-0.0952, +0.1778] | 31.4 | 4/6 | 0.008 | True | g2,g4,g5,g7,g9,g10 |
| sq03 | squeeze_breakout | 507 | -0.0567 | [-0.1804, +0.0674] | 39.5 | 1/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| sq04 | squeeze_breakout | 259 | -0.0085 | [-0.1797, +0.1623] | 23.5 | 2/6 | 0.016 | False | g2,g3,g4,g5,g6,g7,g8,g9 |
| sq05 | squeeze_breakout | 246 | -0.0150 | [-0.1873, +0.1683] | 30.8 | 3/6 | 0.016 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| sq06 | squeeze_breakout | 630 | -0.0350 | [-0.1458, +0.0745] | 41.8 | 2/6 | 0.000 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| sq07 | squeeze_breakout | 307 | +0.0161 | [-0.1424, +0.1800] | 21.8 | 2/6 | 0.020 | False | g2,g3,g4,g5,g6,g7,g8,g9 |
| sq08 | squeeze_breakout | 601 | +0.0410 | [-0.0773, +0.1685] | 25.9 | 4/6 | 0.005 | True | g2,g4,g5,g7,g9,g10 |
| sq09 | squeeze_breakout | 264 | -0.0632 | [-0.2446, +0.1213] | 43.9 | 2/6 | 0.004 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |
| sq10 | squeeze_breakout | 347 | -0.0139 | [-0.1814, +0.1607] | 30.2 | 2/6 | 0.006 | False | g2,g3,g4,g5,g6,g7,g8,g9,g10 |

## Top ten by raw OOS mean (before multiple-testing correction)

| ID | Family | n | Mean net R | DSR | Folds+ | Yearly total R |
|---|---|---:|---:|---:|---:|---|
| dc05 | donchian_trail | 174 | +0.3131 | 0.411 | 3/6 | 2019:+17.8 2020:+51.3 2021:-2.1 2022:-12.6 |
| em08 | ema_pullback | 108 | +0.2085 | 0.363 | 4/6 | 2019:+0.8 2020:+3.5 2021:+14.3 2022:+4.0 |
| dc06 | donchian_trail | 157 | +0.1883 | 0.310 | 3/6 | 2019:+14.6 2020:+33.8 2021:-7.6 2022:-11.2 |
| dc04 | donchian_trail | 237 | +0.1484 | 0.203 | 3/6 | 2019:+11.7 2020:+31.6 2021:-2.9 2022:-5.3 |
| dc10 | donchian_trail | 193 | +0.1251 | 0.237 | 3/6 | 2019:+11.3 2020:+24.5 2021:+1.3 2022:-13.0 |
| dc09 | donchian_trail | 352 | +0.1131 | 0.068 | 3/6 | 2019:+11.7 2020:+56.2 2021:-12.9 2022:-15.2 |
| dc07 | donchian_trail | 363 | +0.0905 | 0.089 | 4/6 | 2019:+15.6 2020:+21.0 2021:+7.0 2022:-10.7 |
| dc03 | donchian_trail | 309 | +0.0837 | 0.067 | 4/6 | 2019:+13.2 2020:+22.3 2021:+6.5 2022:-16.1 |
| em02 | ema_pullback | 307 | +0.0663 | 0.035 | 3/6 | 2019:-13.9 2020:+39.3 2021:-5.3 2022:+0.2 |
| em07 | ema_pullback | 68 | +0.0627 | 0.191 | 3/6 | 2019:+5.7 2020:-6.3 2021:+8.9 2022:-4.0 |

## Honest interpretation

- Real: MT5 XAUUSD H1 prices/volumes from a demo feed; observed spreads where present;
  costs applied causally with a floor and fallback.
- Simulated: fills are next-open with stop-first, fixed slippage, and no queue/partial-fill
  model. Swap is the documented MT5 points, not a broker statement.
- Unverified: OOS 2019-2022 was used to select among 50 trials and is therefore selection
  data, not a clean out-of-sample test. Only the sealed 2023-2026 holdout is clean, and it
  was not opened because no candidate passed.
- Invalidating: a regime break, a different broker's spread/swap, or a coding error in the
  causal cost series. Reconciliation in the MT5 Strategy Tester is required before any demo.
- Interpretation: the positive raw means (best +0.3131R)
  are not statistically distinguishable from zero after the pre-registered 50-trial
  multiple-testing correction. This is a no-edge result within this protocol, not proof
  that every possible strategy fails.
