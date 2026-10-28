# Repository and Obsidian Implementation Audit

Audit performed 2026-10-26. This is a source and artifact inventory, not a new strategy evaluation.

## Repository state

- Repository: `C:\Users\<USER>\Desktop\quant_lab`
- Branch: `research/fundingpips-2026`
- HEAD: `3dd25e005a2f5bd1429d5fe6e87a453e73c4f9f9`
- The starting worktree had a large set of staged additions (including data, reports, scripts, and tests). They were treated as user changes and not reset, unstaged, deleted, or replaced.
- Python interpreter used for validation: `qt\.venv\Scripts\python.exe`, Python 3.13.15.

## Architecture observed

The repository is a modular monolith with current and legacy research paths:

- `data/` loads, audits, resamples, stores, and guards historical data. `data/holdout.py` and `scripts/run_holdout_once.py` implement the sealed-holdout boundary.
- `features/`, `hypotheses/`, `execution/`, `search/`, `validation/`, `risk/`, and `orchestrator/` contain the current v4 research pipeline.
- `research/` contains the frozen v5 backtest and v6 H4 strategy definitions; `scripts/run_v5_search.py` is the v5 search entry point. The sealed holdout is not loaded by that search.
- `experiments/registry.jsonl` contains 204 v4 registry records; `experiments/registry_v5.jsonl` has the v5 trial/search records; `experiments/registry_2026-10-22.jsonl` overlaps earlier records and is deliberately not imported twice.
- `engine/`, `adapters/`, and the root scripts contain older, mixed, or partial paths. The v4 pipeline docs distinguish these from the saved-real-data research path.
- Current recorded data is Parquet (XAUUSD H1/H4 and MT5 M5) plus CSV result/trade logs and JSON/JSONL manifests/registries. The v5 report identifies a 64,651-row XAUUSD H1 dataset with SHA-256 `5dbace90244b3b7e422890ef285b29f8e3f20591266ae2db641ce60bb82e267e`.

## Entry points and output schemas

- Frozen v5 search: `qt\.venv\Scripts\python.exe -m scripts.run_v5_search --help` (the script's argument parser is authoritative; no full search was run during this implementation).
- v4 cost decomposition: `qt\.venv\Scripts\python.exe -m scripts.cost_decomposition --help`; input Parquet plus JSONL registry, outputs `cost_decomposition_<data-hash-prefix>.csv`.
- Current registered outputs are JSONL (`timestamp_utc`, strategy, parameters, result metrics, hashes/seeds when recorded); v5 search summary is JSON with campaign, data, cost model, PBO, gate failures, and 50 trial objects; v4 cost decomposition is CSV with gross/cost/net R, win rate, profit factors, spread, and stop-distance fields.
- The research integration reads these existing formats; it does not calculate replacement performance metrics or launch strategy runs.

## Baseline and test facts before changes

- Unfiltered `python -m pytest -q` **failed during collection**: `tests/test_backtest_engine_fundingpips.pre_daily_percentage_fix.py` is interpreted as a dotted package path and raises `ModuleNotFoundError`. This is a pre-existing test-file naming issue; that file was not renamed or removed.
- `python -m pytest -q --ignore=tests/test_backtest_engine_fundingpips.pre_daily_percentage_fix.py`: **525 passed, 5 warnings** in 20.46 seconds.
- Warnings: python-json-logger deprecation, a test-class collection warning, and numerical warnings from existing phase tests.

## Current strategy and result evidence

- v4 tested five templates: opening-range breakout, prior-day sweep, volatility trend, z-score mean reversion, and session drift. `docs/CLOSEOUT.md` records negative gross expectancy and worse net expectancy for each default template; no candidate passed.
- v5 is a frozen 50-trial XAUUSD H1 Donchian/trailing-stop search. Its actual report (`reports_v5/search_2026-10-22/report.json`) says `NO_EDGE_FOUND`, `selected_config: null`, and `holdout_accessed: false`; all 50 trials failed the DSR/PBO and mean-CI gates. The best raw OOS result is not a candidate. Demo fills, live viability, and a robust edge are not established.
- v6 H4 has a pre-registration and data preparation in the repository. No v6 result report was present in the inspected report directories, so no v6 conclusion is asserted.
- Searches of the existing `strategies/` source did not find Liquidity Sweep or Supertrend implementations. Their vault notes explicitly label them as untested hypotheses rather than existing code or results.
- Existing FundingPips configuration records a 2-Step Standard profile (version 2026-10-22) and cites official help pages. Local unresolved blockers include commission side basis, swap, winter daily-reset offset, and EA ownership approval. This audit does not independently verify that website rules remain current.

## Data, costs, and execution assumptions

- v5 reports next-bar execution, cost assumptions in `cost_model`, OOS 2019-2022, 50 frozen configurations, and no holdout access. These are historical backtest assumptions, not execution proof.
- The v4 closeout describes observed-spread costs and next-bar-open fills, with no contract sizing, commission/swap model, or floating-equity reconstruction for the cost-decomposition run.
- Existing MT5 demo modules are separate and guarded; this implementation does not import them or add order execution. No live code path, credentials, or network dependency was added.
- Data gaps are reported diagnostically; legitimate market closures/session gaps are not automatically marked corrupt.

## Obsidian discovery

- The configured Obsidian vault registry points to the existing directory `C:\Users\<USER>\Documents\Obsidian Vault`.
- It already contains root notes including `Quant Research Dashboard.md`, `03 Experiments.md` (with an `Experiment Registry` heading), and `Data Quality Checklist.md`, plus root index notes. No numeric folder hierarchy existed at discovery.
- Existing note bodies are preserved. Synchronization appends/replaces only a clearly marked generated section in the dashboard, experiment registry, and data-quality note. Missing curated notes are created only if no same-title note already exists. Generated experiment notes live under `07 Results & Reports/Generated by Quant Lab`; absent result sources never trigger note deletion.
- Because the existing experiment page's filename is `03 Experiments.md` rather than `Experiment Registry.md`, a new registry link-target note is created and points back to that preserved page.
- The generated ID map is persisted in that generated directory. Existing `EXP-###` identifiers are reserved before assigning new IDs.

## Known risks and rationale for changes

1. There was no Python-to-Obsidian result importer or portable experiment-note registry. A standard-library CLI was added to parse the existing JSON, JSONL, and CSV schemas, preserve missing values as explicit unknowns, and write notes atomically.
2. The vault's on-disk structure differs from the requested folder layout, so missing folders are added without moving existing files.
3. Source report timestamps/commands/metadata are incomplete in some legacy records. The integration labels these `Unknown`/`Not Available`; it does not infer a backtest command, strategy performance, or validation status.
4. The full v5 search was not rerun because it is a frozen 50-trial historical experiment and a complete rerun is unnecessary for building the documentation bridge. Existing recorded results were imported; the sealed holdout was not read.
5. Current project documents contain older optimistic legacy README content, but the README already identifies those claims as legacy and superseded. This work adds a synchronization section without rewriting the old material.

## Implementation delivered

- Added `scripts/obsidian_sync.py` and `configs/obsidian_sync.json` to import the existing JSON, JSONL, and cost-decomposition CSV artifacts; generate per-record notes, an ID map, a derived registry and research summary; and validate configured CSV/Parquet candle data read-only.
- Experiment IDs are persisted, unique, and assigned against existing project/vault IDs. Source-content fingerprints distinguish changed records; a legacy-key migration preserves the 264 IDs already assigned before the fingerprint change.
- Generated notes include source artifact hashes where available and explicit evidence classification. Missing source metrics stay `Unknown`, `Not Available`, or `Not Tested`; no replacement metric engine was introduced.
- Added focused tests for source parsing, malformed inputs, missing metrics, stable IDs, fingerprint migration, note preservation, idempotency, Windows paths, and OHLC/timestamp validation.
- Added `docs/OBSIDIAN_QUANT_WORKFLOW.md` and a README synchronization section.

## Final implementation verification

- Baseline before implementation (excluding the unrelated dotted-name test file): **525 passed, 5 warnings**. Unfiltered collection failed because `tests/test_backtest_engine_fundingpips.pre_daily_percentage_fix.py` is interpreted by pytest as a dotted module path and raises `ModuleNotFoundError`; that existing file was not renamed or modified.
- After implementation: focused synchronizer tests **14 passed**; available full suite **539 passed, 5 warnings** with only that same file excluded.
- `ruff check` passed for the synchronizer and its tests. Strict mypy passed for the synchronizer with `--explicit-package-bases`. The documented `python -m scripts.obsidian_sync --help` entry point was verified.
- Initial vault setup created the 10 missing curated notes/folders without moving or deleting existing notes. Existing dashboard and data-quality notes received only marked generated sections; the existing `03 Experiments.md` was preserved and linked via a registry bridge note.
- Synchronization imported 264 source-backed experiment/trial records from the configured 204 JSONL registry rows, 50 v5 trials, and 10 cost-decomposition rows. The ID map has 264 entries and 264 unique IDs; exactly 264 experiment notes (`EXP-003` through `EXP-266`) exist. Two post-fingerprint sync runs each created 0 missing curated notes and retained all 264 notes/IDs.
- The generated summary accurately retains the v5 `NO_EDGE_FOUND` verdict, PBO 0.55, and the raw top OOS configuration only as non-candidate evidence. Generated notes include source-artifact hashes and simulated/unverified/invalidating-evidence labels. Existing authored content remains present; synchronized wikilinks were checked.
- Read-only validation passed for H1 (64,651 rows) and H4 (16,944 rows): sorted timestamps, zero invalid/naive/duplicate timestamps, zero missing/invalid/non-finite OHLC rows, and data-quality scores of 1.0 against the configured 0.99 threshold. Candidate gaps (2,841 H1; 627 H4) are descriptive only; closures are not auto-classified as corruption.
- No backtest was rerun, no historical result artifact was replaced, and the sealed holdout was not accessed.

## Remaining evidence limits

- The v5 search is imported historical evidence, not a newly reproduced baseline in this task. The separate 24-trial H4 v6 program is pre-registered but has no result report here.
- FundingPips values in the local profile were last recorded as verified on 2026-10-22; commission side basis, swap, winter reset offset, and EA ownership approval remain unresolved. Current official rules were not independently rechecked.
- Strategy results are historical/backtest simulations, not demo/live fill evidence. No candidate passed the reported v5 gates; no strategy is claimed profitable or validated.
- **Real:** configured local source artifacts and historical-data identifiers stated by their sources.
- **Simulated:** reported strategy metrics, fills, and any backtest or demo/paper performance.
- **Unverified:** current FundingPips terms, missing source fields, live viability, and the not-yet-run v6 results.
- **Could invalidate these conclusions:** data revisions or timezone/session errors, leakage, omitted or misestimated costs, selection bias, execution-model mismatch, or market-structure changes.
