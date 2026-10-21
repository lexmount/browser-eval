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


def test_bilingual_compaction_preserves_language_links_and_evidence(tmp_path):
    import pytest
    source = tmp_path / 'source'
    for prefix in ('', 'en/'):
        root = source / prefix
        (root / 'task').mkdir(parents=True)
        (root / 'result').mkdir()
        (root / 'index.html').write_text('<a href="result/browser-1.html">Result</a>')
        (root / 'task/1.html').write_text('<a href="../result/browser-1.html">Result</a>')
        switch = '../../result/browser-1.html' if prefix else '../en/result/browser-1.html'
        asset = '../../assets/report.css' if prefix else '../assets/report.css'
        (root / 'result/browser-1.html').write_text(
            f'<a class="language" href="{switch}">Switch</a><link href="{asset}">'
            '<a href="../task/1.html">Task</a>')
    (source / 'assets').mkdir()
    (source / 'assets/app.js').write_text('window.ready=true')
    (source / 'assets/report.css').write_text('body{color:black}')
    (source / 'data/evidence').mkdir(parents=True)
    (source / 'data/trajectory').mkdir()
    (source / 'data/evidence/browser-1.json').write_text('{"task_id":1,"score":0}')
    (source / 'data/trajectory/browser-1.json').write_text('{"actions":["click"]}')
    (source / 'report-manifest.json').write_text(json.dumps({
        'languages': ['zh', 'en'], 'task_count': 1, 'logical_results': 1}))
    output = tmp_path / 'compact'
    compact_report(source, output)
    validate_compact_report(output, max_files=200, source=source)
    english = json.loads((output / 'en/pages/result-01.json').read_text())['browser-1']
    chinese = json.loads((output / 'pages/result-01.json').read_text())['browser-1']
    assert 'href="../result.html?key=browser-1"' in english
    assert 'href="../assets/report.css"' in english
    assert 'href="en/result.html?key=browser-1"' in chinese
    assert '<html lang="en"' in (output / 'en/evidence-raw.html').read_text()
    evidence = output / 'data/evidence-01.json'
    original = evidence.read_bytes()
    evidence.write_text('{"browser-1":{"score":1}}')
    with pytest.raises(ValueError, match='evidence differs'):
        validate_compact_report(output, max_files=200, source=source)
    evidence.write_bytes(original)
    page = output / 'en/pages/result-01.json'
    page.write_text(json.dumps({'browser-1': '<p>Missing task and evidence</p>'}))
    with pytest.raises(ValueError, match='pages differ'):
        validate_compact_report(output, max_files=200, source=source)
