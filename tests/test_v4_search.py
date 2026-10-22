import numpy as np

from search.registry import ExperimentRegistry
from validation.walkforward import deflated_sharpe, purged_walk_forward


def test_registry_is_append_only_and_counts_trials(tmp_path):
    registry = ExperimentRegistry(tmp_path / "registry.jsonl")
    registry.record(strategy="x", params={"p": 1}, data_hash="d", code_hash_value="c", results={}, seed=1)
    registry.record(strategy="x", params={"p": 2}, data_hash="d", code_hash_value="c", results={}, seed=1)
    assert registry.trial_count() == 2


def test_registry_retains_but_excludes_degenerate_trials(tmp_path):
    registry = ExperimentRegistry(tmp_path / "registry.jsonl")
    registry.record(strategy="x", params={}, data_hash="d", code_hash_value="c",
                    results={"degenerate": True}, seed=1)
    registry.record(strategy="x", params={}, data_hash="d", code_hash_value="c",
                    results={"degenerate": False}, seed=2)
    assert registry.trial_count(include_degenerate=True) == 2
    assert registry.trial_count() == 1
    assert len(registry.records()) == 2


def test_walk_forward_preserves_frozen_params_and_embargo():
    result = purged_walk_forward(np.arange(12, dtype=float), {"window": 5}, folds=3, embargo=1)
    assert len(result) == 3
    assert all(f.params == {"window": 5} for f in result)
    assert result[1].test_start == 5


def test_deflated_sharpe_is_bounded():
    value = deflated_sharpe(np.ones(20), 10)
    assert 0.0 <= value <= 1.0
