from pathlib import Path

import pytest

from risk.profile_loader import load_profile, require_numeric
from risk.profiles import TrailingDrawdown, daily_loss_basis


def test_assumption_profiles_fail_closed_and_fundingpips_has_source():
    assert load_profile(Path("risk/profiles/fundingpips.yaml"))["source_url"]
    ftmo = load_profile(Path("risk/profiles/ftmo.yaml"))
    with pytest.raises(ValueError):
        require_numeric(ftmo, "daily_loss_limit")


def test_trailing_drawdown_ratchets_up_only():
    dd = TrailingDrawdown(limit=5.0, high_water_mark=100.0)
    assert not dd.update(103.0)
    assert dd.high_water_mark == 103.0
    assert dd.update(97.0)
    assert dd.high_water_mark == 103.0


def test_daily_loss_basis_is_explicit():
    assert daily_loss_basis(100.0, 98.0, "equity") == 98.0
    assert daily_loss_basis(100.0, 98.0, "max_balance_equity") == 100.0
    with pytest.raises(ValueError):
        daily_loss_basis(100.0, 98.0, "unknown")
