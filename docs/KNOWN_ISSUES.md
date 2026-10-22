# Known Issues and Gap Analysis

| Priority | Evidence | Issue | Acceptance criterion |
|---|---|---|---|
| P0 | `data/holdout.py`, `scripts/run_holdout_once.py` | Holdout controls exist but need an independent access audit across all search/validation code | automated test proves no non-authorized path can read holdout |
| P1 | `validation/walkforward.py` | Walk-forward and DSR are compact approximations, not full dependence-adjusted validation | frozen train/test folds, embargo, full registry correction, and documented DSR implementation |
| P1 | `search/run_search.py` | Cost-aware K constraint, max-trades/day, and timeframe-spread search are incomplete | 500 logged non-degenerate trials across M5/M15/H1/H4 |
| P1 | `data/collectors.py` | Dukascopy multi-year collection and cross-source disagreement are incomplete | three-year hashed dataset, gap report, overlap statistics |
| P1 | `engine/` and `adapters/` | Legacy paths contain simulated data and incomplete adapters | each entry point explicitly marked or removed from research execution |
| P2 | `validation/core.py` | Original signal stream is not persisted for a fully faithful permutation test | registry stores signal identity/stream or a formally equivalent null |
| P2 | `risk/profiles/*.yaml` | Most profile fields are null and rule variants are incomplete | verified source, timestamp, basis, reset, and ratchet tests per firm |
| P2 | `tests/` | Existing leakage tests cover v4 helpers but not every legacy engine path | CI leakage canaries cover all result-producing paths |
| P3 | Ruff | `ruff check .` reports 936 findings | clean or explicitly scoped lint baseline |
| P3 | docs | older audit documents report stale test counts/status | all status docs identify their snapshot date and current verified state |

The negative gross expectancy is a confirmed empirical result for the reported v4 cost-decomposition run, not evidence that every possible strategy fails.
