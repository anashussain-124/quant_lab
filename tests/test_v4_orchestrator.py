from configs.v4 import SafetyConfig, WeekRunConfig
from orchestrator.week import WeekOrchestrator


def test_week_orchestrator_stops_at_stop_file(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("operator", encoding="utf-8")
    result = WeekOrchestrator(WeekRunConfig(checkpoint_dir=tmp_path,
                                             safety=SafetyConfig(stop_file=stop))).run()
    assert result[-1].status == "STOPPED"


def test_week_orchestrator_writes_seven_checkpoints(tmp_path):
    result = WeekOrchestrator(WeekRunConfig(checkpoint_dir=tmp_path)).run()
    assert len(result) == 35
    assert (tmp_path / "day_7.json").exists()
