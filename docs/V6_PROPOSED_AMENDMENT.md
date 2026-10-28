# Proposed H4 v6 Protocol Amendment — Pending Human Approval

**Status: PROPOSAL ONLY — NOT ADOPTED.**
This document does not amend or supersede `PRE_REGISTRATION_H4_V6.md`. No v6 configuration, threshold, validation window, result, or dataset has been changed. Do not execute the frozen grid until each applicable decision is approved and recorded in the formal protocol amendment process.

## Why an amendment decision is needed

The existing preregistration freezes the H4 data hash and specifies execution and evaluation behavior, but does not fully define how several observable edge cases map to those requirements. The implementation cannot safely guess those semantics. In particular:

- The current H4 file was created using fixed four-hour bins on the stored index. The protocol also records that source labels are broker server time represented as UTC and observed at UTC+3, but does not state whether those labels represent genuine UTC instants or broker wall-clock labels, nor whether the existing H4 file's bucket boundaries are authoritative.
- The existing resampler retains any non-empty H4 bin. The authorized source has both complete and partial bins; the protocol does not specify whether partial bins are valid.
- The protocol specifies 21:00 UTC rollover, while its H4 bars are labelled 00/04/08/12/16/20. H4 OHLC alone cannot establish the exact time an intrabar stop closed a position within the 20:00–00:00 candle.
- The protocol requires max-hold exit at the next open, but a late signal may lack the necessary future bars at the end of the authorized dataset.
- “Every numeric parameter” does not unambiguously delimit fixed indicator constants versus per-configuration parameters.
- DSR must account for 74 preregistered trials, while a v6 no-trade configuration has no finite per-trade Sharpe.

## Decisions requested

### A. Clock and H4 data policy

Choose one before any v6 run:

1. **Freeze the existing H4 artifact as the sole input.** Treat the currently recorded H4 content/hash and its exact resampler rule as the v6 dataset definition; do not rederive or re-time it. Explicitly state that this does not certify correspondence to UTC-aligned or broker-session-aligned H4 candles. Reconcile timestamp provenance before any later MT5 comparison.
2. **Require independent timestamp provenance first.** Keep the run blocked until authoritative export documentation establishes how timestamps map to UTC and what H4 alignment is intended. If resolution requires rederiving H4 or changing its hash, obtain approval for a new data/protocol version; do not replace the frozen artifact silently.

No historical DST conversion is proposed because no verified historical offset series was identified.

### B. Partial H4 bins and H1 gaps

Choose whether the frozen H4 artifact's current non-empty-bin rule is acceptable (including its partial bins), or whether incomplete bins should be excluded in a separately versioned dataset after approval. Do not silently filter, forward-fill, or change the existing H4 artifact. Market closures and missing H1 observations must not be treated as equivalent without an explicit rule.

### C. Swap accrual in a rollover-spanning H4 candle

Choose an execution convention for the 21:00 UTC rollover inside the 20:00–00:00 H4 interval:

1. **Conservative bar-level charge:** charge one rollover if the position is open at the start of the H4 candle containing 21:00, even if an intrabar stop later exits in that candle. This avoids inventing an intrabar path but can overcharge positions that actually exited before rollover.
2. **H1-based accrual:** use the authorized H1 observations to determine whether a position remained open through 21:00, with an explicit ordering rule for an H1 bar that also touches a stop.
3. **Remain blocked:** do not model swap until a higher-resolution execution/accrual source or an approved rule is available.

Whichever convention is chosen, Wednesday triple swap, long/short rates, point-to-price conversion, commission units, volume scaling, and 1.5x stress must be explicit. The rates remain assumptions until FundingPips supplies authoritative XAUUSD specifications.

### D. Terminal-bar behavior

Choose how to handle a signal when the authorized dataset does not contain the next-open exit or full max-hold horizon:

1. **Skip terminal-risk entries:** do not open a position unless the complete max-hold exit bar and its next-open price are available in the authorized sample. Record skipped signals separately.
2. **Use an approved censoring/settlement rule:** specify it here and amend the frozen execution protocol before use.

Do not settle an open position at the final close unless that is explicitly approved as a protocol amendment; it differs from the specified next-open exit.

### E. Perturbation scope

Confirm whether ±20% variants apply only to numeric values in each strategy configuration (`n`, stop/trail ATR multiples, and the volatility threshold where present), or also to fixed model constants (`ATR_PERIOD`, volatility lookback, and max-hold bars). Keep categorical settings fixed. The base 24 configurations remain unchanged either way.

### F. DSR count when a trial has no finite Sharpe

Choose whether:

1. The multiple-testing correction uses total preregistered trial count 74 while the empirical Sharpe distribution uses only finite trial Sharpes, and missing Sharpe values are recorded as `Not Available` (never treated as an edge/pass); or
2. Any non-finite v6 trial Sharpe blocks DSR evaluation and therefore prevents a core-gate pass.

The implementation must assert the total trial count is 74 and must not silently shrink the correction count by filtering no-trade configurations.

## Human direction received — 2026-10-26

These are the user's implementation choices for this remediation task. They do not edit or replace the frozen preregistration. Choices that explicitly remain blocked are not implemented as runtime behavior.

- **Clock and H4 artifact:** preserve the existing H4 artifact and its index/bucket construction exactly; treat it as authoritative only for the simulation, not as proof of real-UTC alignment. Do not re-align or regenerate it. Keep the timestamp provenance caveat and prohibit claims of verified real-UTC session alignment.
- **Partial H4 bins:** unresolved and blocking. Preserve the existing artifact unchanged. Do not silently include or exclude partial bins; a separately approved policy/version is required before a grid run.
- **Swap rollover:** unresolved and blocking. Do not infer intrabar ordering from H4 OHLC. An explicitly approved and consistently implemented rollover/stop ordering rule or higher-resolution evidence is required before a grid run.
- **Terminal bar:** do not enter unless the complete 120-bar maximum holding horizon and its next-open exit price are available. Record skipped signals with timestamp and reason. Do not fabricate, truncate, or extend prices beyond the authorized sample.
- **Perturbation scope:** perturb only per-configuration numeric values (`n`, stop ATR multiple, trail ATR multiple, and volatility threshold where applicable). Keep ATR period 14, volatility lookback 100, and maximum holding period 120 fixed. Do not use variants to tune the base grid.
- **DSR:** require finite valid Sharpes for all 74 preregistered trials. If any is missing or invalid, mark DSR `NOT EVALUABLE`, prevent a core-gate pass, retain the full count of 74, and record the reason; do not drop or impute trials.

The remaining partial-bin and swap decisions prevent authorization of cost-complete v6 runs. The v6 grid remains prohibited.

## Not proposed for change

- The 24 frozen configuration IDs and parameter dictionaries.
- The v5 `NO_EDGE_FOUND` verdict or any v5 results.
- The 2019–2022 primary validation period; the 2017–2022 folds and 24-bar embargo.
- The 10 core-gate thresholds, gate ordering, challenge overlay rules, selection rule, or stopping rule.
- The sealed holdout boundary. This task and any v6 grid implementation must not read or derive the sealed 2023–2026 holdout.

## Additional explicit policies and evidence request — proposal only

Nothing below is adopted. The frozen preregistration remains unchanged, and the
runtime guard continues to reject execution while unresolved decisions remain.

### Partial H4 bins: alternatives and impact

1. **Retain every non-empty frozen bin.** This preserves the existing H4
   artifact and its SHA-256. It can include bins with fewer than four H1
   samples; OHLC, ATR, Donchian breakouts, and trade timing near those bins may
   differ from complete four-hour bars. Approval must explicitly accept that
   limitation and must not describe the buckets as verified UTC bars.
2. **Require four H1 samples per H4 bin.** This would exclude partial bars and
   could change the available sample, indicators, warm-up alignment, signals,
   trade count, folds, metrics, and hash. It requires a newly versioned H4
   artifact and a formal protocol amendment; it must not replace the frozen
   artifact in place.
3. **Remain blocked.** This preserves the protocol and dataset unchanged but
   prevents any v6 run until the policy is decided.

Missing H1 samples caused by market closure must not be treated as partial-bar
corruption without a separately specified calendar/gap rule.

### Rollover, stop, and challenge daily-reset ordering: alternatives and impact

1. **Conservative H4-bar ordering.** Charge the rollover and evaluate daily
   reset exposure for a position if it was open at the start of the H4 candle
   containing 21:00 UTC, even if the same candle's low/high implies a stop.
   This is deterministic and conservative, but can charge swap or flag a daily
   breach for a position that actually exited before 21:00.
2. **Authorized higher-resolution ordering.** Reconstruct event ordering from
   approved H1/tick data and define what happens when the same H1 interval
   crosses rollover/reset and touches a stop. This can reduce bar-level
   ambiguity, but requires matching timestamps, a specific stop-fill rule,
   verified offsets/DST, and a new frozen data/execution provenance record if
   the source differs from the registered H4 artifact.
3. **Remain blocked.** Do not charge swap or evaluate a challenge daily-reset
   boundary inside an H4 candle. This is the current implementation behavior.

The existing challenge simulator samples H4 intrabar extremes but cannot know
whether those extremes occurred before or after 21:00. Its 21:00 day reset is
also inside the 20:00–00:00 candle. Reusing that simulator without an approved
ordering policy would not resolve the issue.

### External evidence required before a cost-complete run

Obtain dated, authoritative FundingPips documentation or account-specific
statements for the applicable **2-Step Standard, USD 5,000, XAUUSD** profile:

- **Commission:** whether the quoted USD 5 per lot is per side or round turn;
  contract/unit basis; whether entry and exit are each charged; effective date
  and account type. This determines whether the preregistered USD 10 round-turn
  assumption is applicable.
- **Swap:** signed long and short XAUUSD rates, rate units (points/currency per
  lot), contract size, triple-swap weekday, holiday/weekend handling, effective
  dates across 2019–2022, and any account-type distinction.
- **Rollover and daily reset:** exact server/platform time, UTC offset and
  historical DST transitions, rollover instant, daily-loss reset instant, and
  whether positions stopped within the same H4/H1 candle are considered open
  for that rollover/reset.
- **Instrument specification:** tick/point size, contract size, volume minimum
  and step, and any symbol suffix or pricing convention applicable to the
  account. The existing `configs/fundingpips.yaml` values are not a substitute
  for missing dated broker specifications.

Screenshots without account/profile context, undated web snippets, and
MetaQuotes-Demo swap values are insufficient to verify FundingPips costs.
Until these documents are supplied and reconciled, commission and swap remain
`UNVERIFIED`; no backtest may be reported as cost-complete.

## Safe work status at proposal creation

The exact 24 configuration dictionaries have been verified against the frozen table. Existing tests for shared v5 components passed (24 tests), but no v6 grid or v6 backtest was run. No strategy implementation, cost model, dataset, registry, or protocol has been changed as part of preparing this proposal. Implementation of semantics in sections A–F remains pending approval where applicable.

## Implementation status update — fail-closed infrastructure

This update records code added after the proposal was drafted. It is an implementation note only: it does **not** approve the unresolved partial-bin or rollover policy, change the frozen preregistration, or authorize the v6 grid.

- The 24 frozen v6 strategy specifications and the H4 artifact remain unchanged. `docs/PRE_REGISTRATION_H4_V6.md` still has SHA-256 `10ee67e96473c81e9f6aba00003598237954111f08c1b79687c95f8d9b1798af`.
- `research/v6_safety.py` has an exact project-relative allowlist for the frozen H4 development/validation artifact. Its run-readiness check refuses execution while `partial_h4_bin_policy` or `h4_rollover_stop_ordering` is unresolved. The refusal occurs before data path resolution or file hashing.
- `research/v6_runner.py` is intentionally a **shell, not a grid implementation**. Its preflight is written to verify the allowlisted H4 SHA-256 and preregistration SHA-256 before creating provenance if readiness is ever approved. `execute()` still raises `V6GridExecutionUnavailable`; the CLI reports blocked and never runs a configuration.
- `research/costs_v6.py` pins the preregistered USD 10 round-turn commission and 0.5-point-per-side slippage assumptions, requires explicit point/contract-size values, and supports only base and 1.5x non-swap costs. FundingPips broker specifications remain `UNVERIFIED`. `swap_cost()` raises rather than choosing a rollover/stop order or inventing swap rates.
- `research/registry_v6.py` provides an append-only JSONL writer with lock/fsync, duplicate-run rejection, frozen configuration/provenance validation, all ten gate statuses, finite JSON outcomes, failure reasons, and result paths. Synthetic tests use temporary files only; no live registry was created.
- `research/terminal_policy_v6.py` skips an entry unless both the next-bar entry open and the complete 120-bar next-open exit are present; it serializes the skip timestamp and reason.
- `research/validation_v6.py` pins bootstrap to 10,000 resamples/seed 0; requires all 74 valid trial Sharpes for DSR; requires a finite six-fold by 24-configuration PBO matrix and confirms 20 CSCV splits; limits perturbations to the per-configuration numeric parameters; and evaluates core gates in protocol order. Gate 9 requires evidence of a 500-simulation, seed-0 null matched on trade count, direction mix, stop-distance distribution, and holding period. The actual null generator and v6 backtest/fold orchestrator do not exist, so no full gate or outcome is claimed.
- Synthetic verification after these changes: **20 tests passed** across `tests/test_v6_strategies.py` and `tests/test_v6_infrastructure.py`; Ruff passed on the targeted v6 files; targeted mypy passed on ten v6 source/test files with imported v5 modules excluded from traversal; `git diff --check` passed (only Git LF-to-CRLF working-copy warnings). The frozen grid and sealed holdout were not accessed.

### Decisions and work that remain blocking

1. Explicitly approve and document a partial-H4-bin policy. Do not silently include/exclude bins or regenerate the frozen artifact.
2. Explicitly approve H4 rollover/stop ordering or provide sufficient authorized higher-resolution evidence, and verify FundingPips commission units and swap schedule. Until then, complete costs cannot be calculated.
3. After these protocol decisions, implement and test the actual v6 execution engine, chronological six-fold/24-bar-embargo orchestration, full stress-cost pipeline, and reproducible matched random-entry null. The existing runner intentionally cannot execute a grid.
4. Re-run final focused checks and the frozen-input hash preflight, then stop for human review. No v6 grid may be launched as part of this remediation.

The current status remains **BLOCKED — PROTOCOL CLARIFICATION REQUIRED**. The v5 `NO_EDGE_FOUND` result, historical outputs, and all 24 base configurations remain untouched.

## Latest execution-readiness implementation update

This section supersedes the earlier implementation-status bullets that said no
v6 backtest mechanics, fold orchestration, or random-null generator existed.
It records code status only; it does not adopt any proposed protocol policy or
authorize a run.

### Added, synthetic-tested components

- `research/backtest_v6.py`: H4 execution mechanics for causal plans,
  completed-bar signal/next-open entry, initial and trailing stops, stop-level
  and gap-at-open fills, next-open 120-bar exit, terminal-entry skip records,
  no pyramiding, Decimal lot flooring, trade/equity accounting, explicit swap
  policy, frozen 24-config function, 1.5x cost-stress function, and the
  per-configuration perturbation execution helper. A trade/result records its
  cost-stress multiplier. All public execution entry points call the unresolved
  policy guard.
- `research/validation_pipeline_v6.py`: chronological 2017–2022 fold windows
  and 24-bar embargo; 6×24 PBO matrix construction; OOS-only Sharpe assembly
  from supplied 50 v5 and 24 v6 results; perturbation-result completeness;
  OOS, yearly, ATR-tercile, stress, drawdown, and ordered core-gate evidence.
- `research/random_null_v6.py`: 500 attempts, seed 0, non-overlapping random
  entry bars without replacement; candidate direction and stop/ATR profiles
  are permuted; each attempt must reproduce trade count, direction mix,
  stop-distance margins, and realized holding-period margins. A failed or
  unmatched attempt is not silently replaced; any invalid attempt makes the
  null gate NOT EVALUABLE. This exact sampling/conditioning interpretation
  should be reviewed before a future gate-9 result is interpreted.
- `research/challenge_overlay_v6.py`: 92 fixed rolling starts and the
  preregistered primary 2-Step challenge thresholds; gate 7b only runs after a
  `V6ValidationReport` shows every core gate passed. It adapts v6 cost fields to
  the existing v5 challenge simulator's $7 baseline so the $10 v6 assumption
  is applied once. It still calls the policy guard before any simulation.
- `research/constants_v6.py`, `research/validation_v6.py`, and the previously
  added safety, cost, terminal-policy, registry, and runner-shell modules
  provide the shared limits, environment labels, provenance checks, and
  fail-closed infrastructure.

Synthetic tests cover execution stops/gaps/time exits, cost/volume accounting,
fold/embargo boundaries, missing DSR/PBO/perturbation evidence, 500-run null
matching, 92-start challenge thresholds and ordering, holdout-path rejection,
registry duplicate protection, terminal skipping, and fail-closed grid entry.
They do not demonstrate performance on repository data.

### Still not approved or complete

- Partial H4-bin treatment and H4 rollover/stop ordering are still unresolved.
  The daily-loss reset and swap rollover occur at 21:00 inside the 20:00–00:00
  H4 bar; H4 OHLC cannot identify the actual event order.
- FundingPips commission basis, XAUUSD swap rates/units/triple day, rollover
  time, daily-reset timezone/DST, and instrument tick/contract specifications
  remain `UNVERIFIED`. The proposed external evidence checklist above remains
  required.
- `V6Runner.execute()` remains intentionally unavailable. The 24-config
  engine function is not wired to a runner that loads only after exact path,
  dataset-hash, and frozen-protocol-hash preflight, executes every workflow
  stage, and appends every success/failure to the registry. Do not clear the
  run guard or call frame-based execution functions until that integration and
  provenance binding are reviewed.
- No complete v6 validation or challenge result exists. Perturbation, stress,
  core gates, and challenge simulation were not run on real H4 data.
- The frozen protocol and datasets were not hashed or loaded in this
  remediation turn. No grid, exporter, broker action, or holdout access
  occurred.

### Verification and repository state

- Focused synthetic tests: **37 passed**.
- Ruff on 11 targeted v6 implementation/test files: **passed**.
- Targeted strict mypy with imported legacy diagnostics suppressed: **no
  issues found in six v6 source modules**. The unsuppressed mypy run reported
  **56 existing diagnostics in four imported v5 modules**; no v5 source was
  modified.
- Scoped working/staged `git diff --check`: **no whitespace errors reported**.
- Branch `research/fundingpips-2026`, HEAD `3dd25e0`; worktree remains dirty
  with pre-existing staged and untracked files. No commit was created.
  `docs/PRE_REGISTRATION_H4_V6.md` remains listed as a staged addition, and
  `research/strategies_v6.py` has pre-existing staged/unstaged state; neither
  was changed for this update. No status changes were shown for v5 source paths
  or `reports_v5`.

**Status remains BLOCKED.** This is a code-readiness update, not a protocol
amendment or approval to run the frozen grid.
