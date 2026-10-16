"""Small on-disk report contract for acceptance and installed-package tests."""

import hashlib
import json
from pathlib import Path

from harness.report_languages import write_language_contract
from harness.web_fetch.reliability import assess_reliability


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def create_report(base: Path, *, english=False, headless=False):
    root, authority = base / "report", base / "authority"
    root.mkdir(parents=True)
    digest = hashlib.sha256(b"fixture").hexdigest()
    case = {
        "id": "one",
        "name": "Sample task",
        "track": "web",
        "ground_truth_main_content": "fixture",
        "source_verification": {"reference_sha256": digest},
    }
    dataset = {"id": "synthetic-report", "cases": [case], "coverage_plan": {"web": ["alpha"]}}
    if headless:
        profile = {
            "dataset_id": dataset["id"],
            "case_count": 1,
            "providers": ["alpha"],
            "case_ids_sha256": hashlib.sha256(b'["one"]').hexdigest(),
            "comparison_kind": "headless_markdown",
            "timeout_seconds": 10,
            "runs_per_case": 1,
            "conversion": "native",
            "concurrency": 1,
            "reference_binding_mode": "frozen",
            "publication_state": "local",
            "unverified_reference_cases": [],
            "require_independent_result_review": False,
        }
        dataset["comparison"] = {
            key: value
            for key, value in profile.items()
            if key
            not in ("dataset_id", "case_count", "providers", "case_ids_sha256", "comparison_kind")
        }
        dataset["comparison"]["kind"] = profile["comparison_kind"]
        write_json(authority / "evaluation/custom/current-report.json", profile)
        write_json(authority / "evaluation/custom/dataset.json", dataset)
        for name in ("sources", "list-references"):
            (authority / "evaluation/custom" / name).mkdir()
        write_json(authority / "configs/providers.headless.json", {"providers": {"alpha": {}}})
        write_json(
            authority / "evaluation/current-report.json", {"comparison_kind": "other-report"}
        )
    row = {
        "provider": "alpha",
        "case_id": "one",
        "attempt_id": "alpha__one",
        "artifact_path": "artifacts/one.txt",
        "ok": True,
        "metrics": {"content_sha256": digest, "content_pass": True},
    }
    write_json(root / "dataset.json", dataset)
    (root / "results.jsonl").write_text(json.dumps(row) + "\n")
    write_json(root / "reliability.json", assess_reliability(dataset, [row]))
    write_json(root / "execution.json", {"batches": [{"attempt_ids": ["alpha__one"]}]})
    write_json(
        authority / "configs/evaluation-inventory.json",
        {"web": {"case_ids": ["one"], "providers": ["alpha"]}},
    )
    for relative in ("artifacts/one.txt", "references/one.txt"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    pages = {
        "index.html": (
            '<h2>综合通过率总榜</h2><div class="home-overview">Overview</div>'
            "<h2>1 类任务概览</h2><p>本类最高： 判分完整性</p><h2>1 类分榜</h2>",
            '<h2>Overall ranking</h2><div class="home-overview">Overview</div>'
            "<h2>1 task family</h2><p>Highest score. Scoring completeness.</p><h2>1 task family ranking</h2>",
        ),
        "methodology/index.html": (
            "<p>计入耗时 不计入耗时 供应商上报的服务端耗时只作附加证据 "
            "形成可送入统一评分器的最终答案</p>",
            "<p>Included time. Excluded time. Provider timing is supplemental. "
            "Produce the final answer for the shared scorer.</p>",
        ),
        "attempts/alpha__one.html": (
            "<h3>完整标准答案</h3><h3>实际返回</h3>",
            "<h3>Complete reference answer</h3><h3>Actual output</h3>",
        ),
    }
    for route, variants in pages.items():
        for language, content in zip(("zh", "en"), variants, strict=True):
            if language == "en" and not english:
                continue
            path = root / ("en" if language == "en" else "") / route
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("<html><body>" + content + "</body></html>")
    write_language_contract(root, include_english=english)
    return root, authority


def create_revision_report(base: Path):
    from harness.web_fetch.report_interfaces import SPECS
    from harness.web_fetch.report_methods import PROFILES

    root, authority = create_report(base)
    home = root / "index.html"
    home.write_text(
        home.read_text()
        + """
    <div id="home-rankings"><div role="tabpanel">
    <div class="track-guide">Guide</div><div class="track-meaning">Meaning</div>
    <ul class="track-flow"><li>Input</li><li>Fetch</li><li>Score</li><li>Result</li></ul>
    <div class="pass-contract">Acceptance</div><details class="track-more"></details>
    <details class="ranking-metric-help"><dl><dt>Metric</dt></dl></details>
    <div><table class="ranking" data-col="1"><thead><tr>
    <th>序号</th><th>任务通过率</th></tr></thead>
    <tbody><tr><td>1</td><td>100%</td></tr></tbody></table></div></div></div>"""
    )
    panels = "".join(
        f'<details class="interface-panel" id="interface-{name}"'
        + (" open" if index == 0 else "")
        + "><pre>Request</pre><pre>Response</pre></details>"
        for index, name in enumerate(SPECS)
    )
    (root / "adoption").mkdir()
    (root / "adoption/index.html").write_text("<html><body>" + panels + "</body></html>")
    write_json(
        root / "adoption/counts.json",
        {
            "methods": [
                {
                    "provider": name,
                    "repository_count": 1,
                    "file_count": 1,
                    "repositories": [
                        {"repo": "example/project", "matches": [{"path": "sample.py"}]}
                    ],
                }
                for name in sorted(set(PROFILES) | {"alpha"})
            ]
        },
    )
    (root / "analysis").mkdir()
    (root / "analysis/index.html").write_text("""<select data-filter="result">
    <option value="pass">任务通过</option><option value="fail" selected>任务失败</option>
    <option value="all">全部</option></select><table id="filtered-table"><thead><tr>
    <th>序号</th><th>失败分类</th></tr></thead><tbody><tr><td>1</td><td>请求失败</td></tr></tbody></table>""")
    return root, authority
