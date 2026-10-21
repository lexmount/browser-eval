"""Visual route and publication-cache acceptance scenarios."""

import json
from pathlib import Path

import pytest

from harness.browser_use import report_package as packager
from harness.browser_use import report_visual as visual


def report_fixture(root: Path) -> None:
    for name in (
        "index",
        "datasets",
        "methodology",
        "analysis",
        "review",
        "changes",
        "adoption",
        "reproduce",
        "accounts",
    ):
        route = root / ("index.html" if name == "index" else f"{name}/index.html")
        route.parent.mkdir(parents=True, exist_ok=True)
        route.write_text("<html><head></head><body>fixture</body></html>")
    (root / "results").mkdir()
    (root / "results/a-fail.html").write_text('<div id="diagnosis">failure</div>')
    (root / "results/z-pass.html").write_text("<main>success</main>")
    for result in ("a-fail", "z-pass"):
        path = root / f"results/{result}.html"
        path.write_text(
            path.read_text() + '<a href="evidence-viewer.html?src='
            f'artifacts/{result}/executions/1/execution-evidence.json">Evidence</a>'
        )
    (root / "evidence-viewer.html").write_text("<html>viewer</html>")
    (root / "en").mkdir()
    (root / "en/index.html").write_text("<h1>英文报告未生成</h1>")
    (root / "report-languages.json").write_text(json.dumps({"languages": ["zh"]}))


def test_routes_cover_only_top_level_navigation(tmp_path):
    report_fixture(tmp_path)
    routes = set(visual._routes(tmp_path))
    assert routes == {
        "index.html", "datasets/index.html", "methodology/index.html",
        "analysis/index.html", "review/index.html", "changes/index.html",
        "adoption/index.html", "reproduce/index.html", "accounts/index.html",
    }


@pytest.mark.parametrize("count", [1, True, 999999])
def test_partial_receipt_cannot_skip_browser_gate(tmp_path, monkeypatch, count):
    report_fixture(tmp_path)
    receipt = tmp_path.parent / (tmp_path.name + "-receipt.json")
    receipt.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_visual_qa/1",
                "report_tree_sha256": packager.tree_sha256(tmp_path),
                "states_checked": count,
                "failures": [],
            }
        )
    )
    calls = []
    monkeypatch.setattr(packager, "check", lambda *args, **kwargs: None)
    monkeypatch.setattr(packager, "check_links", lambda *args: None)
    monkeypatch.setattr(packager, "check_visual", lambda *args: calls.append("rendered"))
    packager.validate_release(tmp_path, [], receipt)
    assert calls == ["rendered"]


def complete_receipt(report, receipt, binary):
    from harness.reporting.visual import checker_sources, file_sha256

    routes = [
        "index.html",
        "datasets/index.html",
        "methodology/index.html",
        "analysis/index.html",
        "review/index.html",
        "changes/index.html",
        "adoption/index.html",
        "reproduce/index.html",
        "accounts/index.html",
    ]
    value = {
        "schema": "lex_browser_use_visual_qa/3",
        "report_tree_sha256": packager.tree_sha256(report),
        "checker_sources": checker_sources(Path(visual.__file__)),
        "browser_executable": str(binary),
        "browser_sha256": file_sha256(binary),
        "route_classes": len(routes),
        "states_checked": len(routes),
        "states": [
            {
                "route": route,
                "width": width,
                "height": height,
                "theme": theme,
                "overflow": 0,
                "clipped": [],
                "vertical": [],
                "textOverflow": [],
            }
            for route in routes
            for width, height in ((1440, 1000),)
            for theme in ("light",)
        ],
        "failures": [],
    }
    receipt.write_text(json.dumps(value))
    return value


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "report",
        "missing-state",
        "duplicate-state",
        "height",
        "bool-width",
        "missing-field",
        "overflow",
        "source",
        "browser",
        "old-schema",
    ],
)
def test_cache_requires_complete_current_rendering(tmp_path, monkeypatch, damage):
    report = tmp_path / "report"
    report_fixture(report)
    binary = tmp_path / "browser"
    binary.write_text("Synthetic browser identity")
    monkeypatch.setenv("BROWSER_USE_VISUAL_BROWSER", str(binary))
    receipt = tmp_path / "visual.json"
    value = complete_receipt(report, receipt, binary)
    if damage == "report":
        (report / "index.html").write_text("Changed content")
    elif damage == "missing-state":
        value["states"].pop()
    elif damage == "duplicate-state":
        value["states"][-1] = value["states"][0]
    elif damage == "height":
        value["states"][0]["height"] = 999
    elif damage == "bool-width":
        value["states"][0]["width"] = True
    elif damage == "missing-field":
        del value["states"][0]["textOverflow"]
    elif damage == "overflow":
        value["states"][0]["overflow"] = 3
    elif damage == "source":
        value["checker_sources"]["visual.py"] = "0" * 64
    elif damage == "browser":
        binary.write_text("Replaced browser binary")
    elif damage == "old-schema":
        value["schema"] = "lex_browser_use_visual_qa/2"
    receipt.write_text(json.dumps(value))
    calls = []
    monkeypatch.setattr(packager, "check", lambda *args, **kwargs: calls.append("evidence"))
    monkeypatch.setattr(packager, "check_links", lambda *args: calls.append("links"))
    monkeypatch.setattr(packager, "check_visual", lambda *args: calls.append("rendered"))
    packager.validate_release(report, [], receipt)
    assert calls == (
        ["evidence", "links"] if damage == "none" else ["evidence", "links", "rendered"]
    )
