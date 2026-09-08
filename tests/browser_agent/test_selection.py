from pathlib import Path

from harness.browser_agent.selection import TIER_COUNTS, load_pilot


def test_pilot_has_pre_registered_tier_counts() -> None:
    selection = load_pilot(Path("evaluation/browser-agent/webarena-verified-pilot.json"))
    assert len(selection.tasks) == 30
    assert {
        tier: sum(task.tier == tier for task in selection.tasks) for tier in TIER_COUNTS
    } == TIER_COUNTS
    assert sum(task.official_hard for task in selection.tasks) == 16
    assert len(selection.sha256) == 64
