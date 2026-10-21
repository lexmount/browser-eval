import http.server
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from harness.trajectory_replay import webarena_report_visual as _MODULE

_routes = _MODULE._routes


def test_private_preview_fixture_denies_assets_without_version(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head><body>report</body></html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/report.css").write_text("body { color: black; }")
    def handler(*args, **kwargs):
        return _MODULE._QuietHandler(
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
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(root + "replay~fixture/assets/missing.css")
        assert missing.value.code == 404
        assert server.preview_errors[-1] == (404, "/api/preview/replay~fixture/assets/missing.css")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_hosted_preview_errors_identify_missing_file_and_version():
    message = _MODULE._hosted_preview_error_message(
        [
            (404, "/api/preview/replay/assets/report.css"),
            (404, "/api/preview/replay~fixture/assets/missing.css"),
        ]
    )
    assert "asset requested without preview version" in message
    assert "missing file on versioned preview" in message
    assert "/api/preview/replay~fixture/assets/missing.css" in message


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
    assert {
        "interpretation.html",
        "article.html",
        "en/interpretation.html",
        "en/article.html",
    } <= set(routes)


def test_routes_leave_result_and_evidence_details_to_static_checks(tmp_path) -> None:
    _write_results(
        tmp_path,
        [
            {"key": "chromium-1", "status": "pass"},
            {"key": "moli-1", "status": "fail"},
        ],
    )

    routes = _routes(tmp_path)

    assert "result/moli-1.html" not in routes
    assert "evidence-viewer.html?file=data/evidence/moli-1.json" not in routes
    assert "fixes.html" not in routes and "en/fixes.html" not in routes
    assert len(routes) == 10


def test_routes_support_an_all_pass_report(tmp_path) -> None:
    _write_results(tmp_path, [{"key": "chromium-1", "status": "pass"}])

    routes = _routes(tmp_path)

    assert all("result/" not in route and "evidence-viewer" not in route for route in routes)
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
        json.dumps(
            {
                "entries": [
                    {"task_id": 108, "generation": "current_b96"},
                    {"task_id": 29, "generation": "inherited_mixed_build"},
                ]
            }
        ),
        encoding="utf-8",
    )
    routes = _routes(tmp_path)
    assert "diagnostics.html" not in routes and "en/diagnostics.html" not in routes
    for task_id in (108, 29):
        assert f"diagnostic/{task_id}.html" not in routes
        assert f"en/diagnostic/{task_id}.html" not in routes


def test_chinese_visual_review_skips_english_notice(tmp_path):
    _write_results(tmp_path, [])
    (tmp_path / "report-manifest.json").write_text('{"languages":["zh"]}')
    assert "en/index.html" not in _routes(tmp_path)


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "missing-state",
        "wrong-theme",
        "missing-height",
        "viewer-error",
        "missing-topology-field",
        "low-contrast",
        "source",
        "browser",
    ],
)
def test_current_replay_receipt_binds_full_visual_scope(tmp_path, monkeypatch, damage):
    from harness.reporting.visual import checker_sources, file_sha256

    report = tmp_path / "report"
    report.mkdir()
    _write_results(report, [])
    (report / "report-manifest.json").write_text('{"languages":["zh"]}')
    routes = [
        "index.html",
        "dataset.html",
        "results.html",
        "environment.html",
        "review.html",
    ]
    for route in routes:
        path = report / route
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html>fixture</html>")
    binary = tmp_path / "browser"
    binary.write_text("synthetic binary")
    monkeypatch.setenv("BROWSER_USE_VISUAL_BROWSER", str(binary))
    receipt = tmp_path / "visual.json"
    value = {
        "schema": "lex_webarena_replay_visual_qa/5",
        "report_tree_sha256": _MODULE._tree_sha256(report),
        "checker_sources": checker_sources(Path(_MODULE.__file__)),
        "browser_executable": str(binary),
        "browser_sha256": file_sha256(binary),
        "route_classes": 5,
        "states_checked": 5,
        "failures": [],
        "states": [
            {
                "route": route,
                "width": width,
                "height": height,
                "theme": theme,
                "overflow": 0,
                "clipped": [],
                "tiny": [],
                "low_contrast_headers": [],
                "viewerError": False,
                "topologyMissing": False,
                "rawMermaid": False,
            }
            for route in routes
            for width, height in ((1440, 1000),)
            for theme in ("light",)
        ],
    }
    if damage == "missing-state":
        value["states"].pop()
    elif damage == "wrong-theme":
        value["states"][0]["theme"] = "unmeasured"
    elif damage == "missing-height":
        del value["states"][0]["height"]
    elif damage == "viewer-error":
        value["states"][0]["viewerError"] = True
    elif damage == "missing-topology-field":
        del value["states"][0]["topologyMissing"]
    elif damage == "low-contrast":
        value["states"][0]["low_contrast_headers"] = [["white", "white"]]
    elif damage == "source":
        value["checker_sources"]["visual.py"] = "0" * 64
    elif damage == "browser":
        binary.write_text("changed binary")
    receipt.write_text(json.dumps(value))
    assert _MODULE.receipt_is_current(report, receipt) is (damage == "none")
