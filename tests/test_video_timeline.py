"""Generic video skill guards preserve material and reject broken timelines."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / ".agents/skills/video-gen/scripts/validate_timeline.py")
SPEC = importlib.util.spec_from_file_location("video_timeline_validator", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


@pytest.fixture
def timeline():
    return {
        "scenes": [{"id": "a", "start": 0, "speechStart": 1, "speechEnd": 4, "end": 6}],
        "captions": [{"scene": "a", "start": 1, "end": 4, "text": "A complete thought."}],
        "events": [{"scene": "a", "start": .8, "target": "object"}],
        "targets": {"a": ["object"]},
    }


def test_intentional_orientation_and_hold_are_valid(timeline):
    assert validator.validate(timeline) == []


def test_renamed_object_cannot_leave_silent_animation(timeline):
    timeline["events"][0]["target"] = "removed"
    assert validator.validate(timeline)


def test_scene_cannot_truncate_utterance(timeline):
    timeline["scenes"][0]["end"] = 3
    assert validator.validate(timeline)


def test_renderer_migration_cannot_drop_retained_material(timeline):
    contract = {"retained": [{"id": "source-map", "scenes": ["lost"]}]}
    assert validator.validate(timeline, contract)


def test_retained_asset_must_exist(timeline, tmp_path):
    contract = {"retained": [{"id": "source-map", "scenes": ["a"], "asset": "diagram.txt"}]}
    assert validator.validate(timeline, contract, tmp_path)
    (tmp_path / "diagram.txt").write_text("retained diagram")
    assert validator.validate(timeline, contract, tmp_path) == []


def test_caption_must_belong_to_visible_scene(timeline):
    timeline["captions"][0]["scene"] = "wrong"
    assert validator.validate(timeline)


@pytest.mark.parametrize("start,end", [(-1, 2), (5, 7), (3, 2)])
def test_caption_interval_must_fit_its_scene(timeline, start, end):
    timeline["captions"][0].update(start=start, end=end)
    assert validator.validate(timeline)


def test_overlapping_captions_are_rejected(timeline):
    timeline["captions"].append({"scene": "a", "start": 3, "end": 5, "text": "Another thought."})
    assert validator.validate(timeline)


def test_focus_must_remain_in_its_scene(timeline):
    timeline["events"][0]["start"] = 6
    assert validator.validate(timeline)


@pytest.mark.parametrize("broken", [False, True])
def test_cli_reports_valid_and_invalid_timeline(timeline, tmp_path, broken):
    if broken:
        timeline["scenes"][0]["end"] = 3
    source = tmp_path / "timeline.json"
    source.write_text(json.dumps(timeline))
    result = subprocess.run([sys.executable, str(SCRIPT), str(source)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == int(broken), result.stdout + result.stderr
