import json

import pytest

from harness.trajectory_replay.report_languages import language_roots, report_languages
from scripts.check_webarena_replay_visual import _routes


def test_explicit_languages_control_visual_scope(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/results.json").write_text("[]")
    manifest = tmp_path / "report-manifest.json"
    manifest.write_text(json.dumps({"languages": ["zh"]}))
    assert language_roots(tmp_path) == (tmp_path,)
    assert not any(route.startswith("en/") for route in _routes(tmp_path))
    manifest.write_text(json.dumps({"languages": ["zh", "en"]}))
    assert language_roots(tmp_path) == (tmp_path, tmp_path / "en")
    assert "en/index.html" in _routes(tmp_path)


def test_unknown_language_contract_is_rejected(tmp_path):
    (tmp_path / "report-manifest.json").write_text('{"languages": ["en"]}')
    with pytest.raises(ValueError, match="unsupported"):
        report_languages(tmp_path)
