import importlib.util
import http.server
import json
import threading
import urllib.error
import urllib.request
import pytest
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts/check_webarena_replay_visual.py"
_SPEC = importlib.util.spec_from_file_location("check_webarena_replay_visual", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_routes = _MODULE._routes
_first_failed_key = _MODULE._first_failed_key
_first_fix_id = _MODULE._first_fix_id


def test_private_preview_fixture_denies_assets_without_version(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head><body>report</body></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/report.css").write_text("body { color: black; }")
    handler = lambda *args, **kwargs: _MODULE._QuietHandler(
        *args, directory=str(tmp_path), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.preview_errors = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}/api/preview/"
    try:
        with urllib.request.urlopen(root + "replay/index.html") as response:
            assert b'<base href="/api/preview/replay~fixture/">' in response.read()
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(root + "replay/assets/report.css")
        assert denied.value.code == 404
        assert server.preview_errors == [(404, "/api/preview/replay/assets/report.css")]
        with urllib.request.urlopen(root + "replay~fixture/assets/report.css") as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "route,changed_field,wrong_value",
    [
        ("index.html", "base", "https://example.test/api/preview/replay/"),
        ("index.html", "css", "https://example.test/api/preview/replay/assets/report.css"),
        ("result/moli-1.html", "base", "https://example.test/api/preview/replay~fixture/"),
        ("result/moli-1.html", "css", "https://example.test/api/preview/assets/report.css"),
        ("result/moli-1.html", "script", "https://example.test/api/preview/assets/app.js"),
        ("result/moli-1.html", "background", "rgba(0, 0, 0, 0)"),
    ],
)
def test_private_preview_gate_rejects_lost_credentials_and_nested_assets(
    route, changed_field, wrong_value
):
    origin = "https://example.test"
    root = origin + "/api/preview/replay~fixture/"
    folder = route.rpartition("/")[0]
    state = {
        "base": root + (folder + "/" if folder else ""),
        "css": root + "assets/report.css",
        "script": root + "assets/app.js",
        "background": "rgb(246, 248, 251)",
    }
    _MODULE._validate_hosted_resource_state(state, origin, route)
    state[changed_field] = wrong_value
    with pytest.raises(ValueError, match="hosted"):
        _MODULE._validate_hosted_resource_state(state, origin, route)


def test_header_contrast_rejects_light_panel_with_dark_theme_ink():
    contrast = _MODULE._contrast_ratio
    assert contrast("rgb(238, 245, 251)", "rgb(238, 243, 248)") < 1.1
    assert contrast("rgb(238, 245, 251)", "rgb(23, 43, 58)") > 4.5
    assert contrast("rgb(0, 0, 0)", "rgb(255, 255, 255)") == 21
    with pytest.raises(ValueError):
        contrast("rgb(0, 0, 0)", "rgba(0, 0, 0, 0)")


def _write_results(tmp_path, results):
    data = tmp_path / "data"
    data.mkdir()
    (data / "results.json").write_text(json.dumps(results), encoding="utf-8")
    (tmp_path / "report-manifest.json").write_text(json.dumps({"interpretation": False}))


def test_routes_include_integrated_and_offline_article_in_both_languages(tmp_path):
    _write_results(tmp_path, [{"key": "chromium-1", "status": "pass"}])
    (tmp_path / "report-manifest.json").write_text(json.dumps({"interpretation": True}))
    routes = _routes(tmp_path)
    assert {"interpretation.html", "article.html", "en/interpretation.html", "en/article.html"} <= set(routes)


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
    assert "fixes.html" not in routes and "en/fixes.html" not in routes
    assert len(routes) == 14


def test_routes_support_an_all_pass_report(tmp_path) -> None:
    _write_results(tmp_path, [{"key": "chromium-1", "status": "pass"}])

    routes = _routes(tmp_path)

    assert all("result/" not in route and "evidence-viewer" not in route for route in routes)
    assert _first_failed_key(tmp_path) is None
    assert len(routes) == 10


def test_current_routes_exclude_legacy_fix_detail(tmp_path) -> None:
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
    assert "fix/fixture-fix.html" not in routes
    assert "en/fix/fixture-fix.html" not in routes
    assert len(routes) == 10


def test_current_routes_exclude_legacy_diagnostic_overview(tmp_path) -> None:
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
    assert "diagnostics.html" not in routes and "en/diagnostics.html" not in routes
    for task_id in (108, 29):
        assert f"diagnostic/{task_id}.html" not in routes
        assert f"en/diagnostic/{task_id}.html" not in routes
