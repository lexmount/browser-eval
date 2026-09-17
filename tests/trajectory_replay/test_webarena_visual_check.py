import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts/check_webarena_replay_visual.py"
_SPEC = importlib.util.spec_from_file_location("check_webarena_replay_visual", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_routes = _MODULE._routes
_first_failed_key = _MODULE._first_failed_key
_first_fix_id = _MODULE._first_fix_id


def _write_results(tmp_path, results):
    data = tmp_path / "data"
    data.mkdir()
    (data / "results.json").write_text(json.dumps(results), encoding="utf-8")


def test_routes_include_failure_detail_and_evidence(tmp_path) -> None:
    _write_results(
        tmp_path,
        [
            {"key": "chromium-1", "status": "pass"},
            {"key": "moli-1", "status": "fail"},
        ],
    )

    routes = _routes(tmp_path)

    assert "result/moli-1.html" in routes
    assert _first_failed_key(tmp_path) == "moli-1"
    assert "evidence-viewer.html?file=data/evidence/moli-1.json" in routes
    assert "fixes.html" in routes and "en/fixes.html" in routes
    assert len(routes) == 16


def test_routes_support_an_all_pass_report(tmp_path) -> None:
    _write_results(tmp_path, [{"key": "chromium-1", "status": "pass"}])

    routes = _routes(tmp_path)

    assert all("result/" not in route and "evidence-viewer" not in route for route in routes)
    assert _first_failed_key(tmp_path) is None
    assert len(routes) == 12


def test_routes_include_a_bound_fix_detail_in_both_languages(tmp_path) -> None:
    _write_results(tmp_path, [{"key": "chromium-1", "status": "pass"}])
    (tmp_path / "fix").mkdir()
    (tmp_path / "en/fix").mkdir(parents=True)
    (tmp_path / "fix/fixture-fix.html").write_text("fixture", encoding="utf-8")
    (tmp_path / "en/fix/fixture-fix.html").write_text("fixture", encoding="utf-8")
    (tmp_path / "data/fix-log.json").write_text(
        json.dumps({"records": [{"id": "fixture-fix"}]}),
        encoding="utf-8",
    )

    routes = _routes(tmp_path)

    assert _first_fix_id(tmp_path) == "fixture-fix"
    assert "fix/fixture-fix.html" in routes
    assert "en/fix/fixture-fix.html" in routes
    assert len(routes) == 14


def test_routes_include_diagnostic_overview_and_both_evidence_groups(tmp_path) -> None:
    _write_results(tmp_path, [{"key": "moli-1", "status": "fail"}])
    for task_id in (108, 29):
        (tmp_path / "diagnostic").mkdir(exist_ok=True)
        (tmp_path / "en/diagnostic").mkdir(parents=True, exist_ok=True)
        (tmp_path / "diagnostic" / f"{task_id}.html").write_text("fixture", encoding="utf-8")
        (tmp_path / "en/diagnostic" / f"{task_id}.html").write_text("fixture", encoding="utf-8")
    (tmp_path / "diagnostics.html").write_text("fixture", encoding="utf-8")
    (tmp_path / "en/diagnostics.html").write_text("fixture", encoding="utf-8")
    (tmp_path / "data/diagnostic-closure.json").write_text(
        json.dumps({"entries": [
            {"task_id": 108, "generation": "current_b96"},
            {"task_id": 29, "generation": "inherited_mixed_build"},
        ]}),
        encoding="utf-8",
    )
    routes = _routes(tmp_path)
    assert "diagnostics.html" in routes and "en/diagnostics.html" in routes
    for task_id in (108, 29):
        assert f"diagnostic/{task_id}.html" in routes
        assert f"en/diagnostic/{task_id}.html" in routes
