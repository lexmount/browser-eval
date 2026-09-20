import hashlib
import json

import pytest
from bs4 import BeautifulSoup

from harness.trajectory_replay.webarena_report import (
    _supplemental_diagnostic,
    _supplemental_diagnostic_html,
)


def _diagnostic():
    return {
        "schema": "lex_replay_supplemental_diagnostic/1",
        "task_id": 576,
        "scope": "read_only_prefix_on_current_site_not_a_replacement_score",
        "mutations_submitted": False,
        "observations": [
            {"method": method, "observations": [
                {"delay": delay, "state": {"buttons": [{
                    "text": "<untrusted>", "display": "flex", "visibility": "visible",
                    "rect": rect, "rectCount": count, "offsetParent": bool(count),
                }]}} for delay in (0, 5)
            ]} for method, rect, count in (("release", [0, 0], 0), ("pr", [238, 51], 1))
        ],
    }


def test_supplemental_table_keeps_original_score_separate_and_escapes_text():
    for lang in ("zh", "en"):
        soup = BeautifulSoup(_supplemental_diagnostic_html(_diagnostic(), lang), "html.parser")
        assert soup.select_one("details summary")
        assert len(soup.select("tbody tr")) == 4
        assert not soup.select("untrusted")
        assert "238 × 51" in soup.get_text()
    assert _supplemental_diagnostic_html(None, "zh") == ""


@pytest.mark.parametrize("change", ["task", "scope", "mutations", "bytes"])
def test_supplemental_diagnostic_rejects_wrong_scope_and_changed_bytes(tmp_path, change):
    data = _diagnostic()
    if change == "task": data["task_id"] = 547
    if change == "scope": data["scope"] = "formal_replacement_score"
    if change == "mutations": data["mutations_submitted"] = True
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(data))
    reference = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if change == "bytes": path.write_text(json.dumps({**data, "extra": True}))
    with pytest.raises(ValueError):
        _supplemental_diagnostic({"supplemental_diagnostic": reference}, 576)


def test_supplemental_diagnostic_accepts_hash_bound_read_only_probe(tmp_path):
    data = _diagnostic()
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(data))
    reference = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert _supplemental_diagnostic({"supplemental_diagnostic": reference}, 576) == data


def test_wizard_probe_displays_readiness_conjuncts_and_dialog_geometry():
    diagnostic = _diagnostic()
    for method in diagnostic["observations"]:
        for sample in method["observations"]:
            sample["state"]["components"] = [{
                "initialized": True, "labels": ["Color", "<Size>"],
                "wizardMatches": True, "stepMatches": True,
                "selectedMatches": True, "handlerCount": 1,
            }]
            sample["state"]["dialogs"] = [{
                **sample["state"]["buttons"][0], "text": "Create Product Configurations",
            }]
    for lang in ("zh", "en"):
        soup = BeautifulSoup(_supplemental_diagnostic_html(diagnostic, lang), "html.parser")
        assert len(soup.select("table[data-component-readiness] tbody tr")) == 4
        assert len(soup.select("table:not([data-component-readiness]) tbody tr")) == 8
        assert "Color, <Size>" in soup.get_text()
        assert not soup.select("size")
        assert "Create Product Configurations" in soup.get_text()


def test_readiness_has_its_own_population_and_no_empty_geometry_table():
    diagnostic = {
        "scope_note": {"zh": "当前匿名页面 <不是原成绩>", "en": "Current anonymous page <not original score>"},
        "observations": [{"method": "release", "observations": [{"delay": 2, "state": {
            "readiness": {"target": "Add to Cart", "disabled": False,
                          "pendingModules": 83, "formValidationReady": True},
        }}]}],
    }
    for lang in ("zh", "en"):
        html = _supplemental_diagnostic_html(diagnostic, lang)
        soup = BeautifulSoup(html, "html.parser")
        assert len(soup.select("table")) == 1
        assert soup.select_one("table[data-readiness-comparison]")
        assert diagnostic["scope_note"][lang] in soup.get_text()
        assert "&lt;" in html
        assert "83" in soup.get_text()


def test_missing_geometry_fields_are_not_presented_as_observed_null():
    diagnostic = {"observations": [{"method": "pr", "observations": [{"delay": 10, "state": {
        "buttons": [{"text": "Edit status", "rect": [238, 32], "display": None,
                     "visibility": None, "rectCount": None, "offsetParent": None}],
    }}]}]}
    soup = BeautifulSoup(_supplemental_diagnostic_html(diagnostic, "en"), "html.parser")
    cells = [cell.get_text() for cell in soup.select("tbody td")]
    assert cells[-1] == "—"
    assert cells[-2] == "—"
    assert "238 × 32" in cells
    assert "null" not in cells


def test_http_read_table_does_not_invent_geometry_or_latency_ranking():
    diagnostic = {"observations": [{"method": "release", "observations": [{"delay": 2, "state": {
        "httpReads": [{"path": "/f/books/1/<post>", "status": 200, "bytes": 42}],
    }}]}]}
    soup = BeautifulSoup(_supplemental_diagnostic_html(diagnostic, "en"), "html.parser")
    assert len(soup.select("table")) == 1
    assert soup.select_one("table[data-http-read-comparison]")
    assert not soup.select("post")
    assert "/f/books/1/<post>" in soup.get_text()
    assert "42" in soup.get_text()
