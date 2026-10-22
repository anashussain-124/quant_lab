from risk.profiles import FUNDINGPIPS_STANDARD_PHASE1, RuleEngine


def test_rule_engine_locks_at_half_daily_limit_and_caps_trades():
    engine = RuleEngine(FUNDINGPIPS_STANDARD_PHASE1)
    assert engine.allow_trade()
    engine.record(-0.025)
    assert not engine.allow_trade()


def test_stops_can_only_tighten():
    engine = RuleEngine(FUNDINGPIPS_STANDARD_PHASE1)
    assert engine.tighten_stop(1.0) == 1.0
    assert engine.tighten_stop(0.5) == 1.0
    assert engine.tighten_stop(1.5) == 1.5
