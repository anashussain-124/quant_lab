# Repository Map

## Research path

| Path | Responsibility | Status |
|---|---|---|
| `data/` | collection, normalization, storage, holdout split, resampling | mixed: v4 real collectors plus older utilities |
| `features/` | causal EMA, z-score, Donchian, ATR, sessions | implemented and tested |
| `hypotheses/` | five bounded v4 generators and parameter registry | implemented; parameter wiring regression-tested |
| `execution/` | reference and numpy-array causal simulators | implemented; fast/reference equality tested |
| `search/` | seeded sampling, registry, trial outcomes | implemented; full multi-timeframe search incomplete |
| `validation/` | t-statistics, permutation reports, gates, walk-forward helpers | partial; DSR and walk-forward require hardening |
| `risk/` | YAML profile loader, rule engine, trailing drawdown | partial; several profile values are null |
| `orchestrator/` | v4 pipeline and resumable checkpoints | implemented for current dry-run path |
| `strategies/` | public strategy interface, placeholders, real strategy implementations | mixed; placeholders remain registered but must not run |
| `engine/` and `adapters/` | older broad architecture | many interfaces are simulated, partial, or legacy |
| `tests/` | unit, causal, integration, v4, fixture, and control tests | 406 passing |
| `docs/` | protocol, audit, runbook, ADRs, and status | partially current; this audit adds current specifications |

Generated/untracked data and reports are intentionally not treated as source code: `data/store/`, `reports_v4/`, and local registry outputs remain in the worktree.

## Main entry points

- `scripts/dry_run.py` → `orchestrator.week.WeekOrchestrator.run_pipeline`.
- `scripts/cost_decomposition.py` → full pre-holdout template metrics.
- `scripts/run_holdout_once.py` → the only intended holdout reader.
- `search/run_search.py` → bounded v4 search.
- `strategies/real_strategies.py` → public strategy interface implementations.
