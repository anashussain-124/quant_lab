import numpy as np

from risk.profiles import FUNDINGPIPS_STANDARD_PHASE1
from sim.monte_carlo import analytical_pass_probability, run_monte_carlo


def test_zero_drift_analytical_benchmark():
    assert analytical_pass_probability(0.0, 1.0, 0.08, 0.10) == 0.08 / 0.18


def test_monte_carlo_is_seeded_and_reports_rule_mode():
    values = np.array([1.0, -0.5, 0.25, -1.0])
    a = run_monte_carlo(values, FUNDINGPIPS_STANDARD_PHASE1, simulations=20, seed=3)
    b = run_monte_carlo(values, FUNDINGPIPS_STANDARD_PHASE1, simulations=20, seed=3)
    assert a == b
    assert a.rules_enabled is True
