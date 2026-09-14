import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).parents[2] / "scripts/check_webarena_replay_visual.py"
_SPEC = importlib.util.spec_from_file_location("check_webarena_replay_visual", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_routes = _MODULE._routes


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
    assert "evidence-viewer.html?file=data/evidence/moli-1.json" in routes
    assert len(routes) == 14


def test_routes_support_an_all_pass_report(tmp_path) -> None:
    _write_results(tmp_path, [{"key": "chromium-1", "status": "pass"}])

    routes = _routes(tmp_path)

    assert all("result/" not in route and "evidence-viewer" not in route for route in routes)
    assert len(routes) == 10
