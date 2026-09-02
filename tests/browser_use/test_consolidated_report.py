import json
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

import pytest

from harness.browser_use.consolidated_report import ENGINES, build_report
from harness.browser_use.report import METRICS, REPORT_STEM, _detail_filename


def make_run(path, version, *, challenge=False, moli_failure=False):
    path.mkdir()
    manifest = {
        "run_id": path.name,
        "created_at": "2026-09-02T12:00:00Z",
        "suite_sha256": "same",
        "suite": [{"task_id": tid, "title": tid, "calls": []} for tid in ("pypi", "other")],
        "protocol": {"sample_interval_ms": 100},
        "environment": {
            "docker_architecture": "arm64",
            "docker_cpus": 4,
            "docker_memory_bytes": 6000,
            "images": {"chromium": {"id": "same"}},
            "browser_versions": {"moli": f"moli {version}"},
        },
    }
    rows = []
    for task in ("pypi", "other"):
        for engine in ("chromium", "moli"):
            failure = task == "pypi" and (
                (engine == "chromium" and challenge) or (engine == "moli" and moli_failure)
            )
            row = {
                "run_id": path.name,
                "task_id": task,
                "engine": engine,
                "attempt": 1,
                "is_retry": False,
                "status": "fail" if failure else "pass",
                "duration_ms": 100,
                "error": "Timeout" if failure else "",
                "trace": [
                    {
                        "action": "navigate",
                        "status": "pass",
                        "output": "Client Challenge" if failure else "Package title",
                    }
                ],
                "resource": {key: 10 for _, metrics in METRICS for key, *_ in metrics},
            }
            rows.append(row)
            if failure:
                rows.append({**row, "attempt": 2, "is_retry": True})
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / "attempts.jsonl").write_text("\n".join(json.dumps(row) for row in rows))


@pytest.fixture
def runs(tmp_path):
    paths = [tmp_path / name for name in ("historical", "old", "new")]
    make_run(paths[0], "1.0.6")
    make_run(paths[1], "1.0.6", challenge=True, moli_failure=True)
    make_run(paths[2], "1.1.1", challenge=True)
    return paths


def test_one_report_retains_run_bound_evidence_and_truthful_counts(tmp_path, runs):
    before = {
        (p, name): (p / name).read_bytes()
        for p in runs
        for name in ("manifest.json", "attempts.jsonl")
    }
    out = tmp_path / "report"
    build_report(*runs, out)
    audit = json.loads((out / f"{REPORT_STEM}-data.json").read_text())
    assert [audit["functional"][engine]["result_final_passes"] for engine in ENGINES] == [1, 1, 2]
    assert audit["comparison"]["common_task_ids"] == ["other"]
    home = (out / "index.html").read_text()
    assert home == (out / f"{REPORT_STEM}.html").read_text()
    assert "Chromium 历史 2/2，本次 1/2" in home
    assert "Client Challenge" in home
    assert "30/30" not in home
    assert "完整评测 →" not in home
    analysis = (out / f"{REPORT_STEM}-analysis.html").read_text()
    assert analysis.count('class="execution-card"') == 6
    assert "无失败" not in analysis
    for engine, source in zip(ENGINES, ("new", "old", "new"), strict=True):
        assert f'<option value="{engine}">{engine}</option>' in analysis
        detail = (out / _detail_filename("pypi", engine)).read_text()
        assert f"运行 {source}</span>" in detail
        assert "任务 ID <code>pypi</code>" in detail
        assert audit["providers"][engine]["run_id"] == source
    assert (
        "运行 historical</span>" in (out / _detail_filename("pypi", "chromium-history")).read_text()
    )
    assert all((p / name).read_bytes() == content for (p, name), content in before.items())
    for (p, name), content in before.items():
        assert (out / "data" / p.name / name).read_bytes() == content

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                url = urlsplit(value or "")
                if key in ("href", "src") and url.path and not url.scheme and not url.netloc:
                    assert (out / unquote(url.path)).is_file(), value

    for page in out.glob("*.html"):
        Links().feed(page.read_text())


def test_rejects_wrong_execution_run_and_preserves_sources(tmp_path, runs):
    path = runs[1] / "attempts.jsonl"
    path.write_text(path.read_text().replace('"run_id": "old"', '"run_id": "wrong"'))
    with pytest.raises(ValueError, match="run ID"):
        build_report(*runs, tmp_path / "report")


def test_rejects_output_overwriting_source(runs):
    with pytest.raises(ValueError, match="overwrite"):
        build_report(*runs, runs[0])
