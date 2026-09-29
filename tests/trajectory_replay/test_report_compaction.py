import json
from pathlib import Path

from harness.trajectory_replay.report_compaction import compact_report, validate_compact_report


def test_compaction_preserves_logical_routes_under_file_limit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "task").mkdir(parents=True)
    (source / "result").mkdir()
    (source / "data/trajectory").mkdir(parents=True)
    (source / "data/evidence").mkdir()
    (source / "assets").mkdir()
    (source / "index.html").write_text('<a href="task/1.html">task</a>')
    (source / "task/1.html").write_text('<a href="../result/browser-1.html">result</a>')
    (source / "result/browser-1.html").write_text(
        '<a href="../task/1.html">task</a>'
        '<a href="../data/evidence/browser-1.json">下载净化 JSON</a>'
        '<script data-src="../assets/app.js"></script>'
    )
    (source / "data/trajectory/1.json").write_text('{"task_id":1}')
    (source / "data/trajectory/browser-1.json").write_text('{"task_id":1}')
    (source / "data/evidence/browser-1.json").write_text("{}")
    (source / "assets/app.js").write_text("window.ready=true")
    (source / "report-manifest.json").write_text(
        json.dumps({"task_count": 1, "logical_results": 1})
    )

    target = tmp_path / "compact"
    summary = compact_report(source, target)

    assert summary == {"files": 70, "task_routes": 1, "result_routes": 1}
    assert 'href="task.html?id=1"' in (target / "index.html").read_text()
    result_bundle = json.loads((target / "pages/result-01.json").read_text())
    assert 'href="task.html?id=1"' in result_bundle["browser-1"]
    assert 'href="evidence-raw.html?key=browser-1"' in result_bundle["browser-1"]
    assert "查看净化 JSON" in result_bundle["browser-1"]
    assert "data/'+route.kind+'-" in (target / "assets/app.js").read_text()
    assert r"match(/(?:^|-)(\d+)$/)" in (target / "result.html").read_text()
    assert r"match(/(?:^|-)(\d+)$/)" in (target / "assets/app.js").read_text()
    assert r"data\/(trajectory|evidence)\/" in (target / "assets/app.js").read_text()
    assert "trajectory|evidence" in (target / "assets/app.js").read_text()
    app = (target / "assets/app.js").read_text()
    assert "event.preventDefault()" in app
    assert "location.href='evidence-raw.html?key='" in app
    raw = (target / "evidence-raw.html").read_text()
    assert "const value=(await response.json())[key]" in raw
    assert "JSON.stringify(value,null,2)" in raw
    assert not (target / "data/evidence/browser-1.json").exists()
    evidence_bundle = json.loads((target / "data/evidence-01.json").read_text())
    assert evidence_bundle == {"browser-1": {}}
    assert validate_compact_report(target, max_files=80) == {
        "files": 70,
        "task_routes": 1,
        "result_routes": 1,
    }
