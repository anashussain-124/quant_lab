# H4 v6 Pre-flight Audit and Remediation Status

**Initial audit date:** 2026-10-26
**Current status:** V6 execution and validation mechanics have been added behind fail-closed policy guards, but execution and a full run-to-registry workflow remain blocked. No v6 grid was run; no frozen parameters, datasets, historical results, or protocol were modified. The sealed 2023–2026 holdout was not accessed, derived, hashed, or loaded.

## Verdict

# BLOCKED — PROTOCOL CLARIFICATION REQUIRED

The dedicated v6 engine mechanics, fixed-grid function, versioned non-swap cost interface, terminal-entry policy, chronological fold/matrix helpers, DSR/PBO/perturbation/null evaluation, challenge-overlay adapter, input allowlist, provenance checks, and append-only registry are implemented or drafted and covered by synthetic tests. Public execution remains blocked while partial-H4-bin handling and H4 rollover/stop ordering are unresolved. The preflight shell is not yet wired to load the verified dataset, run the complete workflow, and append every run to the registry. Commission and swap specifications are not broker-verified.

`NO_EDGE_FOUND` for v5 remains unchanged.

## Checklist

| # | Status | Audit conclusion |
|---|---|---|
| 1 | **BLOCKED** | The H4 engine and `run_v6_frozen_grid()` exist behind `require_v6_run_ready()`. `V6Runner.execute()` still raises `V6GridExecutionUnavailable`; there is no end-to-end dataset-preflight → grid → validation → registry runner. |
| 2 | **PASS** | The 24 frozen configurations still match the protocol; no specification or parameter dictionary was changed. |
| 3 | **PASS** | `build_plan_v6()` uses prior-bar Donchian channels, ATR14, causal percentile rank, and warm-up 113. Synthetic tests cover future-truncation/shock invariance. `backtest_v6._simulate_v6_plan()` executes completed-bar signals at the next open; terminal entries without the full horizon are skipped. |
| 4 | **BLOCKED** | Synthetic v6 tests now cover initial/trailing stops, gap-at-open fills, stop-level fills, no target ambiguity, and the 120-bar next-open exit. Actual H4 rollover/stop ordering and partial-bin interpretation remain unresolved, so these mechanics cannot be authorized for research runs. |
| 5 | **BLOCKED** | `V6CostModel` implements versioned spread, slippage, commission and 1.5x non-swap stress calculations. `swap_cost()` always raises because rollover ordering and FundingPips rates are unresolved/unverified; no full-net cost result can be produced. |
| 6 | **BLOCKED** | Six chronological folds/24-bar embargo, 10,000 bootstrap resamples, all-74 DSR assembly, 6×24 PBO matrix, scoped perturbation simulation helpers, 500-seed-0 strict matched-null generator, ten ordered core gates, and a conditional 92-start challenge adapter exist. Full grid-to-validation execution and actual policy-dependent simulations remain unrun/unintegrated. |
| 7 | **BLOCKED** | The runner path allowlist rejects all but the frozen development H4 path and synthetic tests exercise forbidden paths. However, the new engine functions accept an in-memory `DataFrame`; the not-yet-wired runner must be the only production loader and bind the frame to successful path/hash preflight before any blocker is cleared. |
| 8 | **BLOCKED** | Existing artifact construction is fixed four-hour, left-closed/left-labelled buckets from start-of-day (00/04/08/12/16/20). The approved simulation-only artifact policy does not certify true UTC alignment. Partial-bin inclusion/exclusion remains unresolved and blocks execution. |
| 9 | **PASS** | `V6Runner.preflight()` hashes the exact allowlisted H4 artifact and frozen preregistration and compares both to pinned SHA-256 values after readiness is granted. Current protocol SHA-256 was verified as `10ee67e96473c81e9f6aba00003598237954111f08c1b79687c95f8d9b1798af`. No v6 preflight was run because the unresolved-decision gate fires first. |
| 10 | **BLOCKED** | Commission is the protocol's USD 10 round-turn-per-lot assumption; spread/slippage conversion requires explicit point/contract inputs. FundingPips commission basis and XAUUSD swap schedule/rates remain unverified; swap charging is disabled by exception. |
| 11 | **BLOCKED** | The append-only writer validates frozen configuration/provenance, all gate statuses, finite JSON, failure reasons, and duplicate IDs. It is not yet connected to a grid runner that persists every config outcome, failure, and reproducibility artifact. |
| 12 | **PASS** | Focused synthetic tests now cover strategy causality, execution/stops/costs/accounting, fold/embargo boundaries, missing-evidence gate behavior, DSR/PBO/null matching, challenge gate thresholds/order, registry/path protection, and terminal skips. No test runs real H4 data or the full grid. |

## Current remediation evidence

- **Runner and input boundary:** [research/v6_runner.py](../research/v6_runner.py): `V6Runner.preflight()` calls `require_v6_run_ready()` before resolving/hashing inputs, then hashes only the allowlisted development H4 artifact and `docs/PRE_REGISTRATION_H4_V6.md`. `execute()` raises `V6GridExecutionUnavailable` even if preflight passes; [scripts/run_v6_search.py](../scripts/run_v6_search.py) also returns blocked status. The actual run was not invoked.
- **Frozen configuration and terminal policy:** [research/strategies_v6.py](../research/strategies_v6.py) remains unchanged during this remediation. [research/terminal_policy_v6.py](../research/terminal_policy_v6.py): `assess_terminal_entry()` requires the next-bar entry and `entry_index + 120` next-open exit; rejected entries carry a UTC timestamp and reason through `as_skip_record()`.
- **Cost model:** [research/costs_v6.py](../research/costs_v6.py): `V6CostModel` pins USD 10 round-turn commission, 0.5 point slippage per side, and only 1.0/1.5 stress. It computes spread/slippage/commission in price-per-ounce units from caller-supplied point and contract sizes. `swap_cost()` raises `V6CostSemanticsBlockedError`; it does not invent rollover ordering or broker rates.
- **Registry:** [research/registry_v6.py](../research/registry_v6.py): `V6Registry.append()` writes JSONL append-only under an exclusive lock, flushes/fsyncs, and rejects duplicate `run_id`s. Records include config/parameters, UTC creation time, all ten gate statuses, outcome or failure reason, result paths, commit, dataset/protocol hashes, date range, seed, cost-model and engine versions. It protects the v5 registry filename and rejects holdout result paths.
- **Metrics and gates:** [research/validation_v6.py](../research/validation_v6.py): `bootstrap_mean_ci_v6()` pins 10,000 resamples/seed 0; `evaluate_v6_dsr()` requires exactly 74 finite trial Sharpes and otherwise returns NOT EVALUABLE; `evaluate_v6_pbo()` requires a finite 6×24 fold matrix and verifies 20 CSCV splits; `build_v6_perturbations()` changes only per-spec numeric parameters, leaving ATR14/lookback100/max-hold120 fixed. `evaluate_v6_core_gates()` uses the frozen thresholds and runs gate 9 only after gates 1–8 and 10 pass. Random-entry gate 9 requires 500 simulations/seed 0 and evidence that trade count, direction mix, stop-distance distribution, and holding period are matched; the null-generation procedure itself is not implemented and cannot be claimed as run.
- **Safety blockers:** [research/v6_safety.py](../research/v6_safety.py) keeps `partial_h4_bin_policy` and `h4_rollover_stop_ordering` unresolved and raises before data hashing or access. The holdout tests use synthetic path strings only.
- **Protocol integrity:** `docs/PRE_REGISTRATION_H4_V6.md` was not edited. Its current SHA-256 matches the pinned value above. No dataset was read or hashed in this remediation turn; prior audit evidence records the authorized H4 hash.
- **Verification:** `pytest -q tests\test_v6_strategies.py tests\test_v6_infrastructure.py`: **20 passed**. Ruff on the ten targeted v6 Python files: **passed**. Targeted mypy on the same ten files with `--follow-imports=silent --explicit-package-bases`: **no issues found in 10 source files**. An earlier unsuppressed broader check reported pre-existing v5 typing errors; no v5 files were changed. Final `git diff --check`: **passed** (Git emitted only LF-to-CRLF working-copy warnings).

### Remaining blockers and minimum work before a frozen run

1. Obtain and document explicit approved policies for partial H4 bins and H4 rollover/stop ordering (or authoritative higher-resolution evidence and verified FundingPips swap terms). Do not encode either by inference.
2. Implement a v6-specific backtest and orchestration after those policy decisions: chronological six-fold construction with the 24-bar embargo, full cost/swap application, 1.5x stress, persistence of every trial outcome, and a reproducible random-entry null satisfying all matching requirements.
3. Add synthetic execution tests for stop/gap/stop-first behavior, rollover ordering, fold boundaries/embargo, and generated-null matching. Then perform the final readiness and hash preflight; human review remains required.

## Initial audit evidence (pre-remediation baseline; superseded by the current status above)

## Evidence by checklist item

### 1. Components and entry points — FAIL

- [docs/PRE_REGISTRATION_H4_V6.md](./PRE_REGISTRATION_H4_V6.md) specifies the frozen program.
- [research/strategies_v6.py](../research/strategies_v6.py) defines `SPECS`, `all_specs()`, and `build_plan_v6()`.
- [scripts/export_v6_h4.py](../scripts/export_v6_h4.py) is a data-frame exporter/auditor, not a strategy-search entry point. Its `main()` creates the H4 Parquet file and metadata.
- [research/backtest_v5.py](../research/backtest_v5.py) contains the reusable `run_backtest()` and `simulate_trade()` engine.
- [research/costs_v5.py](../research/costs_v5.py) contains the reusable `CostModel` and spread fallback.
- [research/validation_v5.py](../research/validation_v5.py) contains reusable bootstrap, DSR, CSCV/PBO, random-null, perturbation, and generic gate helpers.
- The repository contains `experiments/registry_v5.jsonl`, but no `experiments/registry_v6.jsonl`; there is no `scripts/run_v6_search.py` or other v6 orchestrator. No v6 test file is present.

The analogous [scripts/run_v5_search.py](../scripts/run_v5_search.py) is a v5 runner. It instantiates v5 specs and a default v5 `CostModel`; it is not a v6 implementation and must not be repurposed without matching the frozen v6 requirements. A synthetic-only [tests/test_v6_strategies.py](../tests/test_v6_strategies.py) was added during remediation; no v6 runner, registry, or gate orchestration was added.

### 2. Frozen configuration parity — PASS

`research.strategies_v6.all_specs()` returned 24 unique IDs. A focused runtime assertion compared every full parameter dictionary with the Cartesian table in protocol section 5 and passed:

- `h4d01`–`h4d12`: the 12 unfiltered configurations.
- `h4v01`–`h4v12`: the matching 12 configurations with `vol_filter: 0.5`.
- All use the specified lookbacks 20/50/100, stop multiples 1.5/2.5, and trail multiples 2.0/3.5.

This check only enumerated constants; it did not build plans, load data, or run configurations.

### 3. Indicators, causality, warm-up, and signal timing — FAIL

Positive code evidence:

- `build_plan_v6()` calls `wilder_atr()` from [research/indicators_v5.py](../research/indicators_v5.py), which uses current-bar high/low and prior close, then Wilder recurrence.
- Donchian bounds come from `rolling_max_prior()` and `rolling_min_prior()`. Both rolling windows shift by one bar, excluding the signal bar from the channel.
- The filter is `ATR14 / close` compared through `rolling_percentile_rank()`, which compares the current value against the preceding 100 observations.
- `run_backtest()` enters at `signal_index + 1`, and uses the signal bar's ATR for initial stop distance. This is consistent with close-of-bar signals and next-open execution.

The initial audit found the filtered-plan warm-up set to 102 although the first valid rank is at index 113. Remediation changed only this v6 metadata calculation to `max(..., ATR_PERIOD - 1 + VOL_LOOKBACK)`. A synthetic runtime test verifies the first valid rank and `Plan.warmup` are both 113. Additional synthetic tests exercise all 24 v6 specs under future truncation and future-price shocks, and assert non-UTC/naive indices are rejected. These checks do not establish execution-level causality or certify the missing v6 runner.

### 4. Stops, fills, and end-of-data — FAIL

`research.backtest_v5.simulate_trade()`:

- Enters at the supplied bar open; `run_backtest()` supplies the bar after the signal.
- Uses the signal-bar ATR to set the initial stop.
- Uses prior bar closes/ATR for trailing levels, making a newly updated trailing stop active on the next bar.
- Fills a stop gap at the bar open; otherwise fills at the stop level.
- Orders same-bar stop before target when both are ordinary intrabar touches.
- Uses `entry_index + max_hold` as the time-exit bar and exits at that bar's open when available.

However, when no event occurs before the data ends, `simulate_trade()` exits at the final bar's **close** with reason `end_of_data`. That is not the pre-registered max-hold exit at the next open. The authorized H4 file ends at `2022-12-30 20:00 UTC`; a late-window position may not have 120 future H4 bars or even a next bar available without crossing the authorized sample boundary. The initial audit found no terminal-position rule; the user has since directed that such entries be skipped and logged. No v6 runner or v6-specific stop, gap, or terminal-bar tests exist to implement or verify this direction.

### 5. Costs and stress — FAIL

The shared [CostModel](../research/costs_v5.py) defaults to:

- Commission: USD 7 round turn.
- Slippage: 0.5 point per side.
- Swap rollover hour: 21.
- Swap points: -12.6 long / -4.6 short.

Its cost calculation multiplies spread, slippage, commission, and swap by `stress`; the v5 runner uses `CostModel(stress=1.5)` for cost stress. The spread function floors positive observed spreads and uses a causal median of prior non-zero spreads for zero/missing spread values. The v6 exporter aggregates H1 spread by the maximum within each H4 bucket.

The v6 protocol requires USD 10 round-turn commission and 0.5-point slippage per side. No v6 runner constructs that configuration; using the shared default would charge USD 7. The YAML profile also has USD 5/lot as the source commission figure, a USD 10 round-turn primary assumption, and a USD 5 round-turn sensitivity. These are not interchangeable.

**Measured H4 rollover defect:** `make_market()` stores only each bar timestamp's hour, while `simulate_trade()` counts rollovers with `bar_hour == 21`. The H4 timestamps generated by the frozen resampler have hours 0, 4, 8, 12, 16, and 20. A synthetic multi-day H4 trade run through `simulate_trade()` returned `swap_r = -0.0`. Thus the shared engine charges no swap on H4 data, including no Wednesday triple swap. Fixing this requires an explicit H4 rollover-accrual rule; do not simply infer intrabar order from OHLC.

No H4-specific test verifies ordinary swap, Wednesday triple swap, USD 10 commission, or 1.5x scaling.

### 6. Evaluation and ten gates — FAIL

The generic helpers in `research/validation_v5.py` provide:

- 10,000-resample bootstrap mean CI with seed 0 by default.
- DSR from supplied net returns and supplied finite trial Sharpes.
- CSCV/PBO with an even fold count; six folds produce 20 splits.
- 20% numeric perturbations for values present in `StrategySpec.params`.
- A generic evaluator with thresholds corresponding to the ten v6 gate cutoffs: 100 trades, positive mean and CI lower bound, four positive folds, DSR 0.95, PBO 0.30, positive stressed mean, a supplied perturbation boolean, regime/year limits, a supplied null boolean, and 25R drawdown.

These helpers do not constitute v6 gate execution:

- There is no v6 runner to select 2019–2022 entries, compute 2017–2022 annual fold data with the 24-bar embargo, generate a 24-column PBO matrix, run perturbations, conditionally execute gate 9, or produce the final gate outcomes.
- The v5 report contains 50 finite trial Sharpes. The v6 DSR input must combine those with the 24 v6 trial Sharpes to make 74. The helper silently filters non-finite Sharpes and uses the resulting count; it does not assert exactly 74. The user has directed that any missing/non-finite trial Sharpe makes DSR `NOT EVALUABLE` and blocks a core-gate pass; no v6 orchestration enforces this.
- `random_entry_null()` samples entry bars with replacement, assigns directions probabilistically, and samples stop ratios and holding horizons independently. It can therefore duplicate/overlap entries, fail to produce the candidate's exact trade count or direction mix, and stop trades before the sampled horizon. This is not an exact match on the gate-9 attributes in protocol section 7.
- The v5 perturbation orchestration discards non-finite variant means before checking that all variants are positive. A v6 implementation must not let an undefined variant count as passing the “every variant” requirement.
- `perturb_spec()` only sees values in `spec.params`. The user has clarified that perturbation applies to per-spec numeric values only, keeping `ATR_PERIOD=14`, `VOL_LOOKBACK=100`, and `MAX_HOLD_H4=120` fixed; no v6 orchestration implements this scope yet.
- The v6 protocol's 7b challenge-deployment simulation also has no v6 orchestration or registered result path.

### 7. Dataset scope and holdout barrier — BLOCKED

The current `scripts/export_v6_h4.py` imports `DEV_PATH` and `load_dev_validation()` from `research.data_v5`. That loader reads only `data/store/v5/xauusd_h1_dev_validation.parquet`; the exporter does not call a holdout loader. The generated H4 artifact ends in 2022 and corresponds to that development/validation input.

This is evidence that the **existing exporter** uses the authorized input, not proof that a v6 **run** is structurally unable to read holdout. No v6 runner or immutable path allowlist exists to inspect. The shared data module separately exposes `load_sealed_holdout()` behind a caller-token/lock check; that generic facility is not a v6 boundary. A v6 run must fail closed on any dataset path/hash other than the frozen development/validation artifact and must not import or invoke a holdout reader. No holdout data file was read during this audit.

### 8. UTC/server-time and H4 bucket semantics — BLOCKED

`research.data_v5.resample_h4()` uses:

```text
resample("4h", label="left", closed="left", origin="start_day")
```

Therefore, it makes fixed four-hour buckets beginning at 00:00 on each indexed day: labels at 00:00, 04:00, 08:00, 12:00, 16:00, and 20:00 in the index timezone. The H1 import path converts MT5 epoch seconds with `pd.to_datetime(..., utc=True)`; it does not apply a historical server-offset or DST normalization before resampling. The protocol and v6 manifest say the labels inherit broker server time observed as UTC+3 and characterize the effect as bucket-label-only.

That characterization is not established by the implementation: if the source index represents server-clock labels rather than actual UTC, the fixed bins also change which H1 OHLC observations are grouped into each H4 candle. A lack of session filters does not make that distinction immaterial; indicators and trade signals consume those OHLC candles.

A read-only count on the authorized H1 development/validation data found 16,944 non-empty H4 bins: 13,912 contained four H1 samples; 2,970 contained three; 31 contained two; and 31 contained one. `resample_h4()` retains any non-empty bin, and the H4 audit checks reconstruction against the same grouping rule but does not test completeness or define whether partial bins are acceptable. Before a run, freeze whether H4 means server-aligned or UTC-aligned bars, how the +2/+3 seasonal offset is treated, and how partial buckets are handled.

### 9. Dataset and protocol hashes — FAIL

Read-only SHA-256 checks confirmed the current files match the recorded values:

| Artifact | SHA-256 | Result |
|---|---|---|
| H1 development/validation Parquet | `5dbace90244b3b7e422890ef285b29f8e3f20591266ae2db641ce60bb82e267e` | Matches v5 manifest and v6 H4 manifest parent hash |
| H4 development/validation Parquet | `3cdfee22323aed688424460c0f5b63c323c68033a973328f29aa4e3a7b51b38c` | Matches v6 manifest and `.sha256` sidecar |
| Frozen H4 v6 pre-registration | `10ee67e96473c81e9f6aba00003598237954111f08c1b79687c95f8d9b1798af` | Matches the value currently recorded in `data/store/v6/manifest_v6.json` |

The exporter checks the parent H1 hash against the v5 manifest, but it does not calculate or verify the v6 pre-registration hash. Its manifest-writing code also omits `pre_registration_sha256`, so rerunning the exporter would replace the existing manifest without that protocol-hash field. There is no v6 run preflight to verify both expected data hashes and the frozen protocol hash before execution.

### 10. FundingPips commission and swap — BLOCKED

[configs/fundingpips.yaml](../configs/fundingpips.yaml) records a local 2-Step Standard profile, USD 5,000 account, 8%/5% phase targets, 10% overall loss, 5% daily loss, and 21:00 UTC daily reset. The same file explicitly says the metals commission side basis is unstated, the FundingPips XAUUSD swap schedule is unverified, and the winter reset offset is unresolved.

The v6 protocol's USD 10 round-turn commission represents the assumption of USD 5 per lot per side; USD 5 round turn is listed as a sensitivity, and the shared v5 engine's default is USD 7 round turn. Swap points in the local v5 manifest (`-12.6` long, `-4.6` short) are from MetaQuotes-Demo and are not verified FundingPips costs. No authoritative current FundingPips rule or broker statement was rechecked for this audit. These assumptions may be run only as explicitly labelled assumptions after the H4 accounting defect and protocol interpretation are resolved.

### 11. Registry and reproducibility — FAIL

There is no `experiments/registry_v6.jsonl` and no v6 writer. The v5 writer in `scripts/run_v5_search.py` demonstrates append-only trial and summary records with configuration, seed, commit, data/protocol hashes, cost-model version, metrics, gates, and verdict. That does not persist v6 outcomes. A v6 registry must record every configuration, including failures and not-evaluated gates, plus the frozen data/protocol hashes, commit, cost-model version, parameters, seeds, output paths, and failure details. It must not overwrite v5 history.

### 12. Test coverage and safe checks performed — FAIL

Tests inspected:

- `tests/test_v6_strategies.py`: synthetic exact spec parity, filtered-rank warm-up, future-truncation/shock invariance, and timestamp validation.
- `tests/test_v5_strategies.py`: v5 spec invariants, causal future-truncation/shock checks, and leakage canary.
- `tests/test_v5_backtest.py`: v5 next-open, stop, gap, trailing, cost, and Wednesday swap cases on synthetic hourly data.
- `tests/test_v5_validation.py`: v5 bootstrap, DSR, PBO, perturbation, and generic gate tests.
- `tests/test_lookahead.py`: timezone offsets/DST for a separate CSV strategy path; not the H4 resampler.

Safe validation actually run:

- A runtime equality assertion for `all_specs()` versus all 24 frozen protocol parameter dictionaries: **passed**.
- Focused new and existing tests: `pytest -q tests/test_v6_strategies.py tests/test_v5_strategies.py tests/test_v5_backtest.py tests/test_v5_validation.py`: **28 passed**.
- `ruff check research/strategies_v6.py tests/test_v6_strategies.py`: **passed**.
- `mypy --follow-imports=silent --explicit-package-bases research/strategies_v6.py tests/test_v6_strategies.py`: **passed**. (A direct imported-module type check also reports three pre-existing `Index` typing errors in `research/strategies_v5.py`; this remediation did not alter v5.)
- `git diff --check`: **passed**; Git emitted only existing LF-to-CRLF working-copy warnings.
- A synthetic single H4 trade demonstrated the shared engine returned zero swap over a multi-day position.
- A read-only count of buckets used only `data/store/v5/xauusd_h1_dev_validation.parquet`; no strategy or grid was executed.

The four v6 tests are synthetic strategy-plan checks only. No grid command, v6 backtest, holdout test against real holdout data, or demo/broker action was run.

## Protocol inconsistencies or unresolved definitions

1. **Clock and aggregation:** the user directed the implementation to preserve the existing H4 artifact and treat it as simulation-authoritative, without claiming true-UTC/session alignment. This is recorded outside the frozen protocol. Historical clock provenance remains unverified, and partial-bin treatment is still unresolved.
2. **Swap timing on H4:** the protocol specifies 21:00 UTC rollovers and Wednesday triple swap, but H4 bar labels never equal 21:00. OHLC data cannot establish whether a position stopped within the 20:00–00:00 bar before or after rollover; accrual remains unresolved and blocks the run.
3. **End of validation data:** the user directed that entries be skipped unless the full 120-bar horizon and next-open exit price are available, with skipped signals recorded. No v6 runner exists to implement or verify that behavior yet.
4. **Perturbation scope:** the user directed ±20% changes only to per-spec numeric parameters; fixed ATR/filter-lookback/max-hold constants remain unchanged. No v6 orchestration implements or tests this yet.
5. **DSR trial count:** the user directed that all 74 Sharpes must be finite and valid; otherwise DSR is `NOT EVALUABLE` and cannot pass a core gate. No v6 orchestration enforces this yet.
6. **Null matching:** the protocol requires matched count, direction mix, stop-distance distribution, and holding period, while the reusable implementation is stochastic and only approximately matches these margins. No v6-specific null implementation exists.

The user has directed that the existing H4 artifact remain unchanged and not be represented as verified real-UTC alignment; terminal-risk entries be skipped unless the full 120-bar horizon and next-open price are available; perturbations affect only per-spec numeric parameters; and any missing/non-finite Sharpe makes DSR `NOT EVALUABLE` across the full 74 trials. These choices are recorded in [docs/V6_PROPOSED_AMENDMENT.md](./V6_PROPOSED_AMENDMENT.md), without changing the frozen protocol. Partial-bin and swap-accrual semantics remain unresolved and block the run.

The other unresolved points must be resolved without changing the 24 base configurations or looking at results. The frozen protocol is untouched by this audit.

## Minimum corrective work before a frozen run

1. Close the partial-candle and H4 rollover-accrual decisions using a separately approved policy/version. Keep the user's recorded terminal-bar, perturbation-scope, and DSR choices explicit in the formal run configuration/protocol record; do not tune the base grid.
2. Implement a dedicated v6 runner that enumerates exactly the verified 24 specs, uses only the allowlisted H4 development/validation artifact, checks H1/H4/protocol hashes before any run, asserts the 2019–2022 primary window and 2017–2022 folds, and has no holdout reader or arbitrary dataset-path escape.
3. Implement an explicit v6 cost configuration: USD 10 round-turn commission, 0.5-point slippage per side, specified spread aggregation/fallback, corrected H4 swap accrual, and 1.5x scaling of every cost component.
4. Implement the directed terminal-entry skip and skipped-signal audit record without reading post-2022 data.
5. Implement and assert every v6 gate, including exactly 74 DSR trial inputs, six-fold/20-split PBO, exact gate-9 matching and ordering, all required perturbations, and challenge gate 7b only for core-gate passers.
6. Add an append-only v6 registry writer that preserves all outcomes, failures, configs, hashes, seeds, code/cost versions, and result paths.
7. Add focused v6 tests for exact spec parity; causal future-truncation/shock; rank/ATR warm-up; H4 boundary, DST, and partial bins; next-open, stop/trailing/gap/EOD behavior; spread, commission, rollover and 1.5x costs; date/fold/embargo boundaries; DSR/PBO/perturbation/null/gate behavior; registry failure/idempotency; and fail-closed dataset/hash/holdout isolation.
8. Run those safe tests and a non-grid preflight validation. Do not run the frozen 24-configuration grid until the blockers above are closed and the preflight is reviewed.

## Remediation outcome

Changes made after the initial audit:

- [research/strategies_v6.py](../research/strategies_v6.py), `build_plan_v6()`: filtered plans now report warm-up `ATR_PERIOD - 1 + VOL_LOOKBACK` (113 for the frozen ATR-14/100-lookback filter), the first observed finite rank index. Added an explicit `DatetimeIndex` type guard before timezone access. The frozen configuration dictionaries are unchanged.
- [tests/test_v6_strategies.py](../tests/test_v6_strategies.py): four synthetic tests now verify the exact 24-spec grid, the first finite filter rank and warm-up, signal/ATR invariance under future truncation and price shocks, and rejection of naive/non-UTC timestamps. These tests build synthetic frames only; they do not load repository data or invoke a backtest.
- [docs/V6_PROPOSED_AMENDMENT.md](./V6_PROPOSED_AMENDMENT.md): records the user's implementation choices separately from the untouched preregistration. It is not an adopted protocol amendment.

Verification:

- Focused test selection: **28 passed** (4 v6 synthetic tests and 24 existing v5 strategy/backtest/validation tests).
- Ruff on the two changed Python files: **passed**.
- Targeted mypy on the two changed files with imported-module diagnostics suppressed: **passed**. A non-suppressed attempt exposed pre-existing `pandas.Index` typing errors in shared `research/strategies_v5.py`; the v6-local error was fixed with the index type guard, and v5 was not changed.
- `git diff --check`: **passed**.

No v6 entry point, execution/cost engine, registry, holdout barrier, or evaluation-gate orchestration was created. No exporter or grid was run; the sealed holdout, datasets, frozen protocol, v5 source/results, and existing H4 artifact were not modified. Partial-bin treatment and H4 rollover swap accrual remain blockers, so this remediation does not authorize a grid.

## Evidence classification

- **Real:** repository code, frozen protocol, current H1 development/validation artifact, H4 derivative, manifests, and the hashes checked above.
- **Simulated:** the synthetic H4 swap check and the existing v5 unit tests; no v6 performance result was produced.
- **Unverified:** current FundingPips commission basis and swap schedule; intended server-time versus UTC H4 semantics; and all v6 performance and gate results.
- **Could invalidate conclusions:** timezone/DST mapping, partial-bar composition, missing H4 swap, commission mismatch, end-of-data handling, leakage, a wrong hash or protocol version, selection bias, or any mismatch between the implementation and frozen gates.

**Final verdict: BLOCKED — FIXES REQUIRED.**

## Latest implementation update — engine readiness remediation

This section supersedes earlier statements above that the v6 execution engine,
fold orchestrator, random-entry generator, or challenge adapter did not exist.
It does not authorize execution.

### Implemented mechanics

- `research/backtest_v6.py` now provides synthetic-tested H4 plan execution:
  causal strategy plans; next-open entries; initial and close-updated trailing
  stops; gap-at-open and stop-level fills; 120-bar next-open exits; approved
  terminal-entry skips; Decimal volume flooring; spread/slippage/commission
  accounting; explicit swap-policy injection; realized equity points; base
  frozen-grid, single-config, 1.5x stress, and scoped perturbation entry points.
  Public execution functions call `require_v6_run_ready()` before processing
  input.
- Each trade/result retains its 1.0x or 1.5x cost-stress label so validation
  cannot mistake a base run for the stress run.
- `research/validation_pipeline_v6.py` constructs 2017–2022 yearly folds with
  the first 24 bars embargoed; forms an exact 6×24 PBO matrix; assembles 50
  supplied v5 and 24 v6 OOS trial Sharpes without imputation; summarizes
  OOS/ATR-tercile/yearly evidence; requires all expected perturbation results;
  and evaluates the ten core gates in the frozen order.
- `research/random_null_v6.py` implements a deterministic 500-attempt,
  seed-0 null on synthetic-verified machinery. Each attempted simulation must
  match trade count, direction mix, stop/ATR distribution, and realized
  holding-period distribution; any missing or mismatched attempt prevents
  gate 9 from being evaluable.
- `research/challenge_overlay_v6.py` adapts completed v6 trades to the
  existing challenge simulator's $7-baseline cost convention so the $10
  round-turn assumption is applied once. The 92 rolling starts and primary
  gate thresholds are explicit. It only accepts a `V6ValidationReport` whose
  ten core gates pass, then calls the common safety blocker before simulation.
- The append-only registry, provenance validation, frozen configuration
  table, and exact dataset-path allowlist remain available from the earlier
  remediation. No registry record was written.

### Remaining blockers and limitations

1. `partial_h4_bin_policy` and `h4_rollover_stop_ordering` remain unresolved.
   The latter also affects the 21:00 daily reset inside the 20:00–00:00 H4
   candle. The cost model has no approved swap policy; a synthetic zero-swap
   callback is test scaffolding only.
2. FundingPips commission basis, dated long/short swap values, triple-swap
   day, rollover and daily-reset timestamp/DST behavior, and symbol
   specification remain unverified. See the evidence checklist in
   [V6_PROPOSED_AMENDMENT.md](./V6_PROPOSED_AMENDMENT.md).
3. `V6Runner.execute()` still refuses to run. The engine functions accept an
   in-memory `DataFrame`; an end-to-end runner must be wired so it alone loads
   the exact path after path/hash/protocol preflight. Until then, do not clear
   the safety guard: direct frame APIs are not a substitute for provenance
   enforcement.
4. The fixed grid and public perturbation/stress/challenge paths were not run.
   The grid function is not connected to the runner/registry. Every
   configuration's success/failure and reproducibility metadata therefore
   have not been persisted or demonstrated as a complete workflow.
5. The null generator's exact realized-holding match is deliberately strict;
   the frozen text does not specify a sampling/conditioning algorithm. Review
   that implementation interpretation before treating a gate-9 result as
   meaningful. It cannot pass if any of the 500 attempts is invalid.
6. The pinned dataset and protocol hashes were not recomputed in this
   remediation turn. No data artifact was loaded or hashed.

### Verification and Git state

- Focused synthetic selection:
  `pytest -q tests\test_backtest_v6.py tests\test_v6_strategies.py tests\test_v6_infrastructure.py tests\test_v6_validation_pipeline.py tests\test_challenge_overlay_v6.py`
  — **37 passed**.
- Ruff on the 11 targeted v6 implementation/test files — **passed**.
- Targeted strict mypy with imported legacy diagnostics suppressed on six v6
  source modules — **no issues found**. The unsuppressed check reported **56
  errors in four imported v5 modules** (`challenge_v5.py`, `strategies_v5.py`,
  `backtest_v5.py`, and `validation_v5.py`); no v5 source was changed.
- Scoped `git diff --check` for working and staged diffs — **no whitespace
  errors reported**.
- Branch `research/fundingpips-2026`, HEAD `3dd25e0`. Worktree remains dirty
  with pre-existing staged/unstaged additions and untracked files; no commit
  was made. `docs/PRE_REGISTRATION_H4_V6.md` remains a pre-existing staged
  addition and `research/strategies_v6.py` remains pre-existing staged plus
  unstaged state; neither was changed in this remediation. The v5 source paths
  and `reports_v5` showed no status changes.
- No grid, data load/hash, exporter, broker order, or holdout access occurred.

**Current verdict: BLOCKED — PROTOCOL CLARIFICATION REQUIRED.** The
implementation is not ready for a frozen run because partial-bin and
rollover/stop ordering remain unresolved, broker costs are unverified, and
the dataset-preflight-to-registry runner is not complete.
