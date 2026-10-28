# Quant Research Lab — Phase 0 Restructure Audit

## Scope and safety

This is a source- and documentation-level audit of the current repository state. It is not a fresh reproduction of historical results and is not authorization to run the v6 grid.

- No backtest, experiment, challenge simulation, grid, test suite, data export, data hash, or broker action was run in this audit.
- No dataset content was opened. In particular, the sealed 2023–2026 holdout and potentially overlapping staged market-data exports were not inspected or hashed.
- The frozen H4 v6 preregistration, its base configurations, v5 results, and all existing source files were left unchanged.
- The supplied deep-research report was treated as unverified secondary material. Its parameter-tuning suggestions were not applied.
- The latest [`V6_PREFLIGHT_AUDIT.md`](./V6_PREFLIGHT_AUDIT.md), [`V6_PROPOSED_AMENDMENT.md`](./V6_PROPOSED_AMENDMENT.md), and [`PRE_REGISTRATION_H4_V6.md`](./PRE_REGISTRATION_H4_V6.md) were reviewed as requested.

## Repository and Git baseline

At the start of this audit, the repository was on branch `research/fundingpips-2026` at commit `3dd25e005a2f5bd1429d5fe6e87a453e73c4f9f9`. The worktree was already dirty, with staged, unstaged, and untracked changes. These included changes to `.gitignore`, `README.md`, and `research/strategies_v6.py`, as well as existing staged and untracked research, data/export, documentation, and test artifacts. They were preserved and not reset, staged, or committed.

`docs/RESTRUCTURE_AUDIT.md` did not exist at inspection time. This report is the only file created for this Phase 0 task.

## Architecture and actual entry points

The project is a Python research repository organized around `research/` modules, `scripts/` entry points, `data/` loaders and holdout controls, `configs/`, `tests/`, and `docs/`. Multiple generations of backtest and challenge code coexist; v6 reuses selected v5 components rather than being an entirely independent stack.

| Surface | Source evidence | Audit observation |
|---|---|---|
| v5 frozen search | [`scripts/run_v5_search.py`](../scripts/run_v5_search.py), `main()` and `trial_metrics()` | Loads the development/validation partition, checks manifest and preregistration hashes, evaluates configured trials, and writes historical report/registry outputs. It was not run. |
| H4 v6 entry point | [`scripts/run_v6_search.py`](../scripts/run_v6_search.py), [`research/v6_runner.py`](../research/v6_runner.py), `main()` | The command exits blocked before loading market data. It is not a functioning grid launcher. |
| v6 runner | [`research/v6_runner.py`](../research/v6_runner.py), `V6Runner.preflight()` / `execute()` | `preflight()` calls the readiness guard first; `execute()` raises `V6GridExecutionUnavailable`. Hash/preflight and engine primitives are therefore not an integrated runnable workflow. |
| v6 strategy | [`research/strategies_v6.py`](../research/strategies_v6.py), `all_specs()` / `build_plan_v6()` | Implements the frozen 24-spec set, causal indicators, warm-up, and signal plans. |
| v6 execution primitives | [`research/backtest_v6.py`](../research/backtest_v6.py), `_simulate_v6_plan()` / `run_v6_frozen_grid()` | Contains synthetic-testable mechanics, but public grid execution is guarded and requires a swap policy that is intentionally unavailable. |
| v6 validation | [`research/validation_pipeline_v6.py`](../research/validation_pipeline_v6.py), [`research/validation_v6.py`](../research/validation_v6.py) | Contains fold, PBO, DSR, perturbation, random-null, and gate functions; it is not connected to an authorized end-to-end run. |
| v6 costs and registry | [`research/costs_v6.py`](../research/costs_v6.py), [`research/registry_v6.py`](../research/registry_v6.py) | Versioned cost interface blocks unresolved swap; registry provides append-only records and duplicate-run rejection, but no active runner currently records a real run. |
| H4 construction | [`scripts/export_v6_h4.py`](../scripts/export_v6_h4.py), `audit()` / export flow | Resamples the authorized H1 parent into left-labeled, left-closed 4-hour bins with `origin="start_day"` and drops empty aggregate rows. No explicit completeness filter for a partial final bin is applied. The exporter was not run. |
| Historical result synchronization | [`scripts/obsidian_sync.py`](../scripts/obsidian_sync.py) | Reads configured research artifacts to generate Obsidian notes. It was not run in this audit. |

### Readiness checklist

| # | Area | Status | Finding |
|---|---|---|---|
| 1 | Entry points, strategy, engine, costs, registry, tests | PARTIAL | Identified; execution and validation are not wired to a runnable v6 workflow. |
| 2 | Exact 24 frozen configurations | PASS at source/test level | `all_specs()` and a dedicated exact-grid test exist; not run in this audit. |
| 3 | Causal indicators, warm-up, signal timing | PARTIAL | Causality tests exist; 2012 warm-up can be simulated as trades by the execution primitive. |
| 4 | Stops, gaps, trailing, terminal entries | PARTIAL | Representative mechanics and synthetic tests exist; protocol blockers prevent authorization. |
| 5 | Spread, slippage, commission, swap, 1.5x stress | PARTIAL | Non-swap model and stress interface exist; swap is blocked and no stress run was performed. |
| 6 | OOS, folds, embargo, bootstrap, random null, PBO, DSR, perturbation, ten gates | PARTIAL | Components and the ten gate IDs exist; no integrated run, and fold-crossing outcomes are not purged. |
| 7 | Authorized data only / holdout isolation | BLOCKED | Fail-closed path guard exists, but link indirection and the generic holdout accessor leave boundary gaps. |
| 8 | UTC+3 caveat and H4 bucket boundaries | BLOCKED | Code uses UTC-aware labels and start-of-day bins; true source-time mapping and reset/partial-bin semantics are unresolved. |
| 9 | Dataset and protocol hashes | NOT VERIFIED | Expected values are documented; this audit deliberately did not hash or read any data. |
| 10 | FundingPips commission, swap, and rules | BLOCKED | Local values exist; relevant broker and current challenge specifications remain unverified. |
| 11 | Registry preserves outcomes and provenance | PARTIAL | Append-only and duplicate protections have synthetic tests; no end-to-end run is recorded. |
| 12 | Coverage for leakage, costs, stops, timezones, and gates | PARTIAL | Synthetic tests cover many areas; H4 boundary/rollover, period isolation, cross-fold exits, and path-link defenses are missing. |

## v5 status and historical evidence

`docs/V5_RESULT.md`, `docs/CLOSEOUT.md`, and `docs/PROJECT_STATUS.md` record the historical `NO_EDGE_FOUND` outcome and describe why the v5 candidate did not pass its gates. These are existing records, not a reproduction in this audit. No v5 source or output was changed.

`docs/IMPLEMENTATION_AUDIT.md` records prior test results: 525 passed with five warnings before the Obsidian changes, then 539 passed with five warnings afterward, in each case excluding `tests/test_backtest_engine_fundingpips.pre_daily_percentage_fix.py`. The same report records that unfiltered collection failed because pytest treated that dotted filename as a module path and raised `ModuleNotFoundError`. These are historical recorded results; they were not independently rerun and do not establish the state of the current dirty worktree.

## H4 v6 evidence and findings

### Frozen configurations and causal signal generation — PASS at source/test level

- `research/strategies_v6.py:88` defines `all_specs()`; `tests/test_v6_strategies.py:38` asserts the exact 24 frozen IDs and parameters. The test was inspected, not executed in this audit.
- `build_plan_v6()` uses prior-bar Donchian values, Wilder ATR-14, a causal volatility-percentile calculation, and indicator warm-up checks. `tests/test_v6_strategies.py:72` and `:93` cover causal warm-up and future-truncation/shock invariance.
- `build_plan_v6()` rejects naive or non-UTC timestamps (`tests/test_v6_strategies.py:125`).
- This does not resolve whether the H4 timestamps represent true UTC boundaries, nor whether the resampler’s partial-bin treatment matches the frozen protocol.

### Execution mechanics — PARTIAL; not ready

The v6 engine primitives implement next-bar-open entry, initial protective stops, gap-through-stop fills at the bar open, intrabar stop fills at the stop level, close-updated trailing stops that become active on a later bar, a 120-bar next-open time exit, and terminal-entry skipping where a complete horizon cannot be simulated. Synthetic tests cover representative stop, gap, trailing-stop, time-exit, volume-sizing, cost-accounting, and invalid-frame cases (`tests/test_backtest_v6.py:101`, `:132`, `:156`, and `:291`; `tests/test_v6_infrastructure.py:551`).

These mechanics are not an authorized or complete execution pipeline: `run_v6_frozen_grid()` requires an approved swap policy, and the safety guard blocks the runner while policy decisions are unresolved. No v6 engine run has been established by this audit.

### Cost model — PARTIAL; external assumptions remain unverified

- `research/costs_v6.py` defines a versioned non-swap cost interface and rejects unapproved stress inputs. It refuses to calculate swap while H4 rollover/stop ordering and applicable swap rates are unresolved.
- `research/backtest_v6.py:195` builds the market using the existing v5 spread helper and a 10-point spread floor; the configured fallback also uses USD 10 round-turn commission per lot and 0.5 points of slippage per side. These are model inputs, not independently verified current broker specifications.
- The cost interface and tests cover declared cost scaling and rejection of missing swap policy (`tests/test_v6_infrastructure.py:126`, `:160`; `tests/test_backtest_v6.py:174`). No 1.5x run was performed.
- `configs/fundingpips.yaml` and the v6 challenge overlay contain dated/local assumptions. Commission-side basis, swap rates, rollover timing, winter reset offset, and current authoritative FundingPips terms remain unverified. A primary-source broker specification and applicable challenge-rule document, with effective dates and account/symbol context, are required before treating them as verified.

### Validation and research gates — PARTIAL; functions exist, orchestration does not

- The frozen DSR correction count is retained as `V6_REQUIRED_DSR_TRIALS = 74` in `research/constants_v6.py:24`. `evaluate_v6_dsr()` rejects a missing/invalid set rather than treating absent trial Sharpes as success (`research/validation_v6.py:258`; `tests/test_v6_infrastructure.py:373`).
- The validation code defines six calendar-year folds, first-24-bar entry embargo, PBO fold-matrix construction, bootstrap, gate ordering, perturbations, and a matched random-entry null. Tests cover missing evidence, fixed-seed synthetic nulls, perturbation scope, and gate sequencing (`tests/test_v6_validation_pipeline.py:113`, `:137`, `:164`, `:193`, `:219`, `:257`; `tests/test_v6_infrastructure.py:404`, `:463`, `:523`).
- The fold summary assigns a trade by entry timestamp and embargo eligibility (`research/validation_pipeline_v6.py:111–129`) but does not require its exit to be before the fold’s `end_exclusive`. Trades may therefore cross calendar-fold boundaries while contributing their full outcome to the entry fold. The handling needs to be resolved against the frozen fold/purging policy before using fold/PBO results.
- The challenge overlay is parameterized for 92 starts and delegates to the v5 challenge simulator. It is not connected to a completed v6 run; tests only verify schedule/gate behavior and that it is deferred until the core gates pass (`tests/test_challenge_overlay_v6.py:61–90`).
- No gate is reported as passed in this audit. A code path implementing a gate is not evidence that its inputs were produced or that the gate passed.

### Research-period boundary — FAIL in the current engine primitive

`research/backtest_v6.py:416–450` initializes `_simulate_v6_plan()` at `plan.warmup`, not at the protocol’s allowed trading-period boundary. When passed the full frozen frame, the simulator can process warm-up-adjacent trades in the 2012 warm-up period, despite the protocol reserving 2012 for indicator warm-up. The resulting equity path can affect later sizing and minimum-volume eligibility, even when later metrics filter trades by date. The engine needs an explicit trade-start boundary and a defined initial equity state for the scored period. This defect is in an un-runnable primitive; the fail-closed runner currently prevents its use.

### Data and holdout isolation — BLOCKED

- `research/v6_safety.py:29–54` blocks execution on unresolved `partial_h4_bin_policy` and `h4_rollover_stop_ordering` and restricts the v6 dataset to a specific approved relative artifact path. `V6Runner.preflight()` invokes this guard before path hash checks, and synthetic tests verify the runner blocks before dataset/provenance validation (`tests/test_v6_infrastructure.py:178–217`).
- The guard is useful fail-closed protection but is not a sufficient permanent holdout boundary. `Path.resolve()` follows filesystem links; an allowlisted path redirected through a symlink/reparse point can resolve to a different in-repository target before hashing. There is no corresponding symlink/reparse-point rejection test. Once the policy guard is cleared, reject links/reparse points and validate the canonical path before opening or hashing any file.
- The generic `data/holdout.py:48` `read_final_holdout(path, lock_path, caller_token=...)` accepts a caller-supplied path and string token. This is not a robust authorization boundary for arbitrary local code. No holdout was accessed. Any future holdout workflow requires an independently enforced authorization boundary and a separately approved access event.
- Documented hashes are evidence copied from existing manifests/docs only; they were not recomputed: H1 development/validation `5dbace90244b3b7e422890ef285b29f8e3f20591266ae2db641ce60bb82e267e`, H4 development/validation `3cdfee22323aed688424460c0f5b63c323c68033a973328f29aa4e3a7b51b38c`, and preregistration `10ee67e96473c81e9f6aba00003598237954111f08c1b79687c95f8d9b1798af`. A safe preflight must verify these before loading research data; this audit deliberately did not hash any artifact.

### H4 timestamp and rollover semantics — BLOCKED; protocol clarification required

`scripts/export_v6_h4.py:65–72` groups stored timestamps into left-labeled, left-closed 4-hour bins anchored to the start of each stored day. The H1 loader converts MT5 epoch seconds with `pd.to_datetime(..., unit="s", utc=True)` (`research/data_v5.py`, `_mt5_rates()`). That code establishes UTC-aware pandas values but does not establish that the source epoch values correspond to the intended broker-server wall-clock bars across UTC+3 and DST transitions. There is no inspected offset history or data-level timezone validation in this audit. Consequently, the exact market-session H4 boundaries remain unverified.

The partial final H4 bin is retained if it contains OHLC aggregates; no source-level completeness check excludes it. The policy remains unresolved and must not be inferred from observed results.

The FundingPips challenge overlay sets a 21:00 UTC daily reset (`research/challenge_overlay_v6.py:89`) and passes H4 bars to the v5 challenge simulator. That simulator processes resets while iterating bar timestamps (`research/challenge_v5.py:764–855`). Since 21:00 lies inside the 20:00–00:00 H4 interval, the simulator cannot observe the actual 21:00 mark from H4 OHLC. At the next H4 timestamp, its reset logic may use the preceding H4 bar’s close, which is after the reset instant for left-labeled bars. The reset equity/risk treatment is therefore not justified by available H4 data. Do not treat the challenge overlay as valid until an approved conservative rule or finer-grained data source resolves this.

### Registry and provenance — PARTIAL

`research/v6_registry.py` validates provenance, requires complete failure records, rejects non-finite JSON values and holdout-named result paths, appends records, and rejects duplicate run IDs. Synthetic tests inspect append-only behavior and invalid-input rejection (`tests/test_v6_infrastructure.py:219–318`). This is useful infrastructure, but it is not yet connected to an executable runner; consequently it does not yet demonstrate preservation of actual trial outcomes, failures, configuration details, and reproducibility metadata for a full 24-configuration run. Path-name filtering alone is not a general path-containment or holdout-access control.

## Metrics and observability coverage

The v6 trade-level records include gross/net R, USD P&L, volume, risk, reason, holding bars, and separated commission/swap fields, with spread/slippage combined in one component. `summarize_trade_outcomes()` covers trade count, gross/net totals, expectancy, net-based profit factor, R drawdown, Sharpe, and holding duration, and explicitly represents empty input as not evaluable.

The inspected summary layer does not provide the full requested report surface: daily/weekly loss and loss-limit violations, consecutive losses, position-size/risk violations, drawdown duration/recovery and monetary drawdown series, separate spread versus slippage aggregates, complete monthly/quarterly breakdowns, and all session/regime/long-short robustness cuts. Some may be derivable from trade records, but are not established as implemented outputs here. Missing inputs must remain `NOT EVALUABLE`, not be silently treated as passes.

## Prioritized findings and minimum work before research execution

1. **P0 — Preserve the current fail-closed state.** Partial H4-bin and rollover/stop-ordering policies are unresolved. Obtain explicit human approval for each, record the protocol version/hash, and do not edit the frozen preregistration or base grid as part of this audit.
2. **P0 — Complete a provenance-bound runner before any grid.** Wire the fixed 24 configurations, approved-path validation, hash/protocol checks, complete data quality checks, deterministic execution, all frozen validation stages, and append-only registry in one fail-closed workflow. Preflight must reject symlink/reparse-point substitution before file access.
3. **P1 — Correct the simulation period/equity boundary.** Enforce the frozen trade-start date while still allowing prior data for indicators. Define the scored-period initial balance so 2012 warm-up trades cannot change later eligibility or risk state.
4. **P1 — Resolve fold-boundary treatment.** Specify and test how overlapping trades are purged, embargoed, or assigned when exits cross a calendar fold. Do not change fold semantics after viewing results.
5. **P1 — Resolve timestamp/session mapping and challenge reset.** Establish MT5 epoch and broker-server timezone semantics across DST using authoritative metadata; specify H4 labels and partial-bin treatment; then define rollover/stop priority and the 21:00 daily reset treatment. If H4 cannot represent the required instant, use an approved conservative policy or suitable finer data.
6. **P1 — Verify costs and challenge rules from authoritative sources.** Record commission basis, swap rates/units, triple-swap day, rollover time, contract size, spread/slippage basis, and applicable FundingPips challenge version with source, effective date, account/symbol context, and verification date.
7. **P2 — Close reporting/test gaps without weakening existing tests.** Add targeted synthetic tests for path-link rejection, pre-start trade/equity isolation, cross-fold exits, H4 boundary/partial-bin behavior, rollover mark ordering, and complete metric/gate missing-input behavior. Extend summary artifacts for required risk and robustness cuts.
8. **P2 — Repair test collection separately.** Resolve the pre-existing dotted pytest filename issue without weakening or removing tests, then establish a safe test selection that cannot load prohibited data before running a suite.

## Audit conclusion

**H4 v6 status: BLOCKED — PROTOCOL CLARIFICATION REQUIRED.** Even after policy approval, the period boundary, fold crossing, robust holdout boundary, challenge reset treatment, full runner wiring, and audit-specified tests remain required before any frozen run.

### Evidence classification

- **Real:** repository source and configuration inspected without opening datasets; historical results as recorded in existing reports.
- **Simulated:** v5/v6 trade and challenge mechanics, plus synthetic fixtures described by existing tests; no simulation was run in this audit.
- **Unverified:** current broker commission/swap/rollover specifications, FundingPips terms, true MT5-to-UTC/session mapping, full v6 validation results, and any claimed readiness based on hashes not recomputed here.
- **Could invalidate the conclusions:** source/data revision, hidden path indirection, timestamp misinterpretation, partial-bar or rollover semantics, omitted/incorrect costs, cross-fold outcome overlap, leakage, or execution-model mismatch.
