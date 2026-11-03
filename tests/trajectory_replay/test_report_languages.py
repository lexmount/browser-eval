import json

import pytest

from harness.trajectory_replay.report_languages import language_roots, report_languages
from harness.trajectory_replay.webarena_report_visual import _routes


def test_explicit_languages_control_visual_scope(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/results.json").write_text("[]", encoding="utf-8")
    manifest = tmp_path / "report-manifest.json"
    manifest.write_text(json.dumps({"languages": ["zh"]}), encoding="utf-8")
    assert language_roots(tmp_path) == (tmp_path,)
    assert [route for route in _routes(tmp_path) if route.startswith("en/")] == ["en/index.html"]
    manifest.write_text(json.dumps({"languages": ["zh", "en"]}), encoding="utf-8")
    assert language_roots(tmp_path) == (tmp_path, tmp_path / "en")
    assert "en/index.html" in _routes(tmp_path)


def test_unknown_language_contract_is_rejected(tmp_path):
    (tmp_path / "report-manifest.json").write_text('{"languages": ["en"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        report_languages(tmp_path)
