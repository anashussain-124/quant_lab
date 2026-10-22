# Final historical backtest protocol

Status: research-only. No order routing is performed by this command.

## Authoritative command

Run from the repository root with the configured project interpreter:

```powershell
& "qt/.venv/Scripts/python.exe" qt/fundingpips_research_v3/fundingpips_research_v3.py `
  --mt5 XAUUSD `
  --days 1095 `
  --strategy all `
  --daily-loss-config 5pct `
  --save-input `
  --outdir reports/verified_YYYY-MM-DD
```

The command fetches M5 bars from MT5, saves the exact input snapshot as
`input_m5.parquet`, and writes one report, trade log, equity curve, walk-forward
file, sequential challenge file, and stress file per strategy.

To reproduce the same run later without depending on a changing terminal
history, use the saved snapshot:

```powershell
& "qt/.venv/Scripts/python.exe" qt/fundingpips_research_v3/fundingpips_research_v3.py `
  --parquet reports/verified_YYYY-MM-DD/input_m5.parquet `
  --strategy all `
  --daily-loss-config 5pct `
  --point 0.01 --contract-size 100 --volume-min 0.01 --volume-step 0.01 --volume-max 100 `
  --outdir reports/replay_YYYY-MM-DD
```

The Parquet snapshot must be retained with its report. Reports include the
input data hash, configuration hash, code version, environment label, and data
quality summary.

Verified run completed on 2026-10-22 from the MT5 demo terminal:

- Snapshot: `reports/verified_2026-10-22/input_m5.parquet`
- Rows: 210,212
- UTC range: 2023-10-23 14:10:00 to 2026-10-21 09:25:00
- Duplicate timestamps: 0
- Gaps over five minutes: 817; these were not silently filled
- Spread column: present
- Snapshot SHA-256: `30f58642248ffe9c80547879ca5705385bb5de0d195e8d16ca646f55a762c1ee`

The MT5 terminal was used as a demo data source. The historical research run
did not invoke order routing.

## Execution assumptions

The current V3 engine assumes BID OHLC bars, uses observed spread when present
or a 30-point fallback, applies $2.50 per-lot-per-side commission, applies
$0.05 adverse slippage per fill, enters on the next bar open, resolves same-bar
stop/target ambiguity conservatively in favor of the stop, and does not model
swap, partial fills, requotes, or true tick-path sequencing. These assumptions
must be replaced or separately verified before treating a result as execution
evidence.

## Current data limitation

The committed Parquet inventory contains XAUUSD D1, H1, and H4 data plus a
short H1 snapshot. It does not contain a committed XAUUSD M5 source file. The
previous V3 reports identify their source only as `MT5:XAUUSD`; their exact raw
input cannot be reconstructed from the repository. The `--save-input` option is
therefore mandatory for future comparable runs.

## FundingPips rule verification

The current account model is FundingPips 2 Step Standard, 5% daily-loss
configuration: Phase 1 target 8%, Phase 2 target 5%, minimum three trading days
per phase, 10% static maximum loss, 30-day inactivity rule, and daily loss
based on the higher of opening balance or opening equity, resetting at 00:00
platform time (UTC+3). The 3% add-on removes the minimum-day requirement.
These values were checked against the [official 2 Step Standard account-model
page](https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard)
on 2026-10-21, but the purchased account/dashboard remains the authority.

## v4 framework status (2026-10-22)

The `redesign/v4` branch adds typed fail-closed configuration, public-source
collection with checksums and manifests, UTC-aware causal feature primitives,
an append-only experiment registry, next-open execution tests, Decimal cost
accounting, explicit risk profiles, Monte Carlo utilities, and a stoppable
seven-day checkpoint runner. These additions are framework code and tests;
they do not constitute a new market-data result or a strategy promotion.

The v4 data collector has not been used to claim an XAUUSD M5 refresh in this
run. If the verified MT5 demo session or saved M5 input is unavailable, the
M5-dependent report must stop rather than substitute H1/H4 data.
