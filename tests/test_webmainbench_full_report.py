"""Reader-facing controls for the complete-cohort report renderer."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_fetch.report import generate_webmainbench_full
from harness.web_fetch.report_build import public_webmainbench_scoring_projection
from harness.web_fetch.webmainbench_report_check import (
    check_diagnosis_inventory,
    check_interaction_script,
    check_result_payload,
    check_table_interaction,
)


def test_public_report_omits_private_predecessor_scores_but_keeps_certified_replacement() -> None:
    predecessor = {'text_edit': {'score': 0.1}}
    rows = [
        {'attempt_id': 'ordinary', 'pre_scoring_correction_metrics': predecessor},
        {'attempt_id': 'replacement', 'supersedes_attempt_id': 'old',
         'pre_scoring_correction_metrics': predecessor},
    ]
    projected = public_webmainbench_scoring_projection(rows)
    assert 'pre_scoring_correction_metrics' not in projected[0]
    assert projected[1]['pre_scoring_correction_metrics'] == predecessor
    assert 'pre_scoring_correction_metrics' in rows[0]


def render_full_report(tmp_path, binding_name, extra_methods=0, *, call_counts=None):
    call_counts = call_counts or {}
    providers = ["moli", "lightpanda", "obscura", "chromium", "kitesurf",
                 "moli-pr785-fixed", "moli-content-fixed"]
    providers.extend(f"browser-{index}" for index in range(extra_methods))
    case_id = "case-1"
    metrics = ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS")
    (tmp_path / "artifacts").mkdir()
    rows = []
    physical = []
    summaries = {}
    for index, provider in enumerate(providers):
        text = ("" if provider == "moli" else
                "# 无法访问此网站\nERR_ACCESS_DENIED" if provider == "chromium" else
                f"response {provider}")
        artifact = f"artifacts/{provider}.md"
        (tmp_path / artifact).write_text(text, encoding="utf-8")
        output_sha = hashlib.sha256(text.encode()).hexdigest()
        calls = call_counts.get(provider, 1)
        for number in range(1, calls + 1):
            body = text if number == calls else 'Previous request failed'
            physical_path = f"artifacts/{provider}-p{number:02d}.txt"
            (tmp_path / physical_path).write_text(body, encoding="utf-8")
            physical.append({"provider": provider, "track_id": case_id,
                             "physical_artifact_path": physical_path,
                             "output_sha256": hashlib.sha256(body.encode()).hexdigest(),
                             "physical_attempt": number, "ok": number == calls and provider != 'moli',
                             "elapsed_ms": 100 + index})
        value = (index + 1) / 10
        scores = {metric: value for metric in metrics}
        scores["overall"] = value
        summaries[provider] = {
            "official_aggregate": {"scores": scores, "denominators": {metric: 1 for metric in metrics}},
            "request_successes": 0 if provider == "moli" else 1,
        }
        rows.append({
            "attempt_id": provider + "__case-1__r001", "case_id": case_id, "provider": provider,
            "original_url": "https://example.test/page", "ok": provider != "moli",
            "output_chars": len(text), "artifact_path": artifact, "output_sha256": output_sha,
            "elapsed_ms": 100 + index, "total_elapsed_ms": (100 + index) * calls,
            "physical_attempt_count": calls, "provider_metadata": {"stderr": "fixture error"},
            "error_type": "fixture_error" if provider == "moli" else None,
            "error_message": "fixture error" if provider == "moli" else None,
            "metrics": {metric: {"score": value, "success": True} for metric in metrics},
        })
    dataset = {"cases": [{"id": case_id, "url": "https://example.test/page",
                          "meta": {"language": "en", "level": "easy"},
                          "input_html_sha256": hashlib.sha256(b"<p>source</p>").hexdigest(),
                          "reference_sha256": hashlib.sha256(b"answer").hexdigest()}]}
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources/case-1.html.txt").write_text("<p>source</p>")
    (tmp_path / "references").mkdir()
    (tmp_path / "references/case-1.md").write_text("answer")
    profile = {"providers": providers, "versions": {provider: "1.0" for provider in providers},
               "variants": [
                   {"provider": "moli-pr785-fixed", "profile": "moli-pr785-fixed-report.json"},
                   {"provider": "moli-content-fixed", "profile": "moli-content-fixed-report.json"},
               ],
               "variant_labels": {
                   "moli-pr785-fixed": "Moli 1.1.9 · PR785 修复版",
                   "moli-content-fixed": "Moli 1.1.11 · PR919 721bd2e 实测版",
               },
               "source_dataset_sha256": "a" * 64, "official_scorer_commit": "b" * 40}
    profile["method_binding_file"] = binding_name
    profile["versions"]["moli"] = "1.0'</script><script>bad()"
    profile["versions"]["moli-content-fixed"] = "1.1'</script><script>bad()"
    content_keys = ("home_title", "home_lead", "score_note", "latency_note", "all_latency_note", "diagnostic_note", "infographic_caption",
                    "dataset_lead", "analysis_lead", "method_lead", "method_input",
                    "method_scoring", "method_correction", "guide_lead", "guide_scope",
                    "guide_outputs", "guide_diagnosis", "guide_example", "reproduce_lead",
                    "guide_method_scope", "guide_conclusion", "audit_scope", "audit_findings",
                    "audit_resolution", "audit_limit")
    (tmp_path / "report-content.json").write_text(json.dumps({key: "说明" for key in content_keys}), encoding="utf-8")
    content = json.loads((tmp_path / "report-content.json").read_text())
    content["interfaces"] = {provider: "调用接口" for provider in providers}
    content["adoption_lead"] = "采用证据"
    content["adoption_limit"] = "采用限制"
    content["review_scope_failure"] = "失败已复核"
    content["review_scope_pass"] = "成功未复核"
    content["kitesurf_version_note"] = "后端版本未披露"
    content["fixed_quality"] = "成功结果仍需核对质量。"
    content["fixed_mechanism"] = "修复解析器状态。"
    content["method_error_profiles"] = {
        provider: {"runtime": "运行错误", "quality": "质量缺口", "reading": "处理优先级"}
        for provider in providers
    }
    content["moli_fix_profiles"] = [
        {"image": image, "title": "修复类型", "symptom": "现象", "root": "根因",
         "fix": "修复", "verification": "验证"}
        for image in (
            "moli-fix-01-visibility-disclosure.png",
            "moli-fix-02-fallback-fragments.png",
            "moli-fix-03-live-form-state.png",
            "moli-fix-04-media-candidates.png",
            "moli-fix-05-semantic-boundaries.png",
            "moli-fix-06-parser-resume-ownership.png",
        )
    ]
    for provider in providers:
        content["guide_method_" + provider] = "逐项观察"
    (tmp_path / "report-content.json").write_text(json.dumps(content), encoding="utf-8")
    (tmp_path / "provider-adoption.json").write_text(json.dumps({"included": [], "cutoff": "2026-08-26"}), encoding="utf-8")
    (tmp_path / "failure-analysis.json").write_text(json.dumps({"reviewed_failures": [
        {"provider": "moli", "case_id": case_id, "category": "browser_exit", "category_label": "Moli 解析器崩溃",
         "cause": "Fixture failed", "treatment": "Investigate", "status": "open"}]}), encoding="utf-8")
    (tmp_path / "physical-attempts.jsonl").write_text("".join(json.dumps(row) + "\n" for row in physical), encoding="utf-8")
    (tmp_path / "moli-content-fixed-report.json").write_text(json.dumps({"source_commit": "683b297e1b99b5bbe27284dc8b5e657eaacf8996"}), encoding="utf-8")
    generate_webmainbench_full(tmp_path, dataset, rows, summaries, profile)
    return providers, rows


@pytest.mark.parametrize('calls,recovered,failure_calls', [
    ({}, 0, 1), ({'lightpanda': 2, 'moli': 2}, 1, 2),
    ({'lightpanda': 3, 'moli': 4}, 1, 4), ({'moli': 4}, 0, 4),
])
def test_retry_summary_uses_recorded_call_counts(tmp_path, calls, recovered, failure_calls):
    providers, _ = render_full_report(tmp_path, 'method-binding.json', call_counts=calls)
    page = BeautifulSoup((tmp_path/'methodology/index.html').read_text(), 'html.parser')
    text = page.find('h2', string='失败重试').find_parent('section').get_text()
    assert f'共 {sum(calls.get(name, 1) for name in providers)} 次调用' in text
    assert f'{recovered} 条请求在重试后成功' in text
    assert f'1 条调用 {failure_calls} 次' in text
    assert '均调用两次' not in text


@pytest.mark.parametrize("binding_name", ["seven-method-binding.json", "method-binding.json"])
@pytest.mark.parametrize("extra_methods", [0, 1])
def test_full_report_renders_registered_details_and_failure_evidence(tmp_path: Path, binding_name: str, extra_methods: int) -> None:
    providers, rows = render_full_report(tmp_path, binding_name, extra_methods)
    subprocess.run(["node", "--check", str(tmp_path / "assets/report.js")], check=True)
    report_js = (tmp_path / "assets/report.js").read_text(encoding="utf-8")
    check_interaction_script(report_js)
    with pytest.raises(ValueError, match="focus restoration"):
        check_interaction_script(report_js.replace("dialog.addEventListener('close',()=>diagnosisFocus?.focus())", "", 1))
    assert "</script><script>bad()" not in report_js
    assert "\\u003c/script\\u003e\\u003cscript\\u003ebad()" in report_js
    home = check_presentation((tmp_path / "index.html").read_text(encoding="utf-8"))
    assert '?v=' in home.select_one('link[rel="stylesheet"]')['href']
    assert home.select_one('link[rel="stylesheet"]')['href'].split('?v=', 1)[1] == home.select_one('script[src]')['src'].split('?v=', 1)[1]
    assert home.find('a', href='https://github.com/lexmount/WebMainBench/tree/lex-main') is not None
    assert len(home.select('td[data-metric-direction="max"]')) == 6 * len(providers)
    assert len(home.select('td[data-metric-direction="max"] .comparison-winner')) == 6
    assert home.select_one('td[data-target-body-provider="chromium"]').get_text(strip=True) == "0/545"
    assert home.select_one('td[data-target-body-provider="moli-content-fixed"]').get_text(strip=True) == "1/545"
    assert "成功生成正文" in home.get_text()
    assert "各方法的质量风险定位" in home.get_text()
    core = home.select_one('table#core-results')
    assert len(core.select('tbody > tr')) == len(providers)
    assert len(core.select('th[aria-sort="none"] > button[data-table-sort]')) == 8
    assert "实测提交 683b297e1；Moli 1.1'</script><script>bad()" in core.get_text(' ', strip=True)
    assert core.find('script') is None
    quality_matrix = home.select_one('table#quality-issue-matrix')
    assert quality_matrix is not None
    assert len(quality_matrix.select('tbody > tr')) == len(providers)
    assert len(quality_matrix.select('th[aria-sort="none"] > button[data-table-sort]')) == 7
    assert quality_matrix.find(
        'a', href='analysis/index.html?provider=moli&metric=delivery&kind=all'
    ).get_text(strip=True) == '1'
    guide = BeautifulSoup((tmp_path / "guide/index.html").read_text(encoding="utf-8"), "html.parser")
    guide_quality = guide.select_one('#error-landscape table#quality-issue-matrix')
    assert guide_quality is not None
    assert "各方法错在哪、差在哪" in guide.select_one('#error-landscape').get_text()
    assert len(guide.select('table#method-error-profiles tbody > tr')) == len(providers)
    assert guide.select_one('img[src="assets/seven-method-error-taxonomy.png"]') is None
    assert len(guide.select('[data-moli-fix-slide]')) == 6
    assert all(len(slide.select('.slide-facts > div')) == 4
               for slide in guide.select('[data-moli-fix-slide]'))
    assert len(guide_quality.select('tbody > tr')) == len(providers)
    assert len(guide_quality.select('th[aria-sort="none"] > button[data-table-sort]')) == 7
    lineage = guide.select_one("#moli-repair-lineage")
    assert lineage is not None
    assert lineage.find("a", href=binding_name) is not None
    assert lineage.find("a", href="https://github.com/lexmount/moli/pull/814").get_text(strip=True) == "PR814（历史前身）"
    assert lineage.find("a", href="https://github.com/lexmount/moli/pull/919").get_text(strip=True) == "PR919（本次实测来源）"
    assert "绑定 PR919 提交 683b297e1" in lineage.get_text(" ", strip=True)
    assert "在最新 main 上完成 release 编译和 545 页认证运行" in lineage.get_text(" ", strip=True)
    detail = check_presentation((tmp_path / "results/index.html").read_text(encoding="utf-8"))
    assert detail.select_one('[data-failure-diagnosis]') is not None
    comparison = BeautifulSoup((tmp_path / "cases/index.html").read_text(encoding="utf-8"), "html.parser")
    assert comparison.select_one("#case-results") is not None
    assert comparison.select_one("#case-comparison") is not None
    assert comparison.select_one("#case-reference") is not None
    assert comparison.h1.get_text() == "人工答案与各方法的完整输出"
    analysis = BeautifulSoup((tmp_path / "analysis/index.html").read_text(encoding="utf-8"), "html.parser")
    check_diagnosis_inventory(analysis)
    assert analysis.select_one('#diagnosis-case').get_text() == '同页各方法对照'
    assert len(analysis.select("tr[data-result]")) == len(providers)
    assert analysis.select_one('tr[data-provider="moli-content-fixed"]') is not None
    assert analysis.select_one("#cause") is not None
    assert analysis.select_one("#metric") is not None
    assert analysis.select_one('tr[data-cause="Moli 解析器崩溃"]') is not None
    assert analysis.select_one('tr[data-provider="moli"][data-quality~="delivery"]') is not None
    assert len(analysis.select('table#result-inventory button[data-table-sort]')) == 5
    assert all(cell.get('data-sort') is not None for cell in analysis.select('tr[data-result] td:nth-of-type(4), tr[data-result] td:nth-of-type(5)'))
    dataset_page = BeautifulSoup((tmp_path / 'dataset/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert dataset_page.select_one('#dataset-q') is not None
    assert dataset_page.select_one('#dataset-language') is not None
    assert dataset_page.select_one('#dataset-level') is not None
    assert len(dataset_page.select('table#dataset-inventory button[data-table-sort]')) == 4
    check_table_interaction(tmp_path, provider_count=len(providers), case_count=1)
    with pytest.raises(ValueError, match="Core comparison"):
        check_table_interaction(tmp_path, provider_count=len(providers) + 1, case_count=1)
    with pytest.raises(ValueError, match="Dataset inventory"):
        check_table_interaction(tmp_path, provider_count=len(providers), case_count=2)
    missing_dialog = BeautifulSoup(str(analysis).replace('id="diagnosis-error"', 'id="missing-error"'), 'html.parser')
    with pytest.raises(ValueError, match='diagnosis dialog'):
        check_diagnosis_inventory(missing_dialog)
    missing_button = BeautifulSoup(str(analysis).replace('data-diagnose=""', 'data-missing=""', 1), 'html.parser')
    with pytest.raises(ValueError, match='cannot open'):
        check_diagnosis_inventory(missing_button)
    missing_handler = BeautifulSoup(str(analysis).replace(' onclick="openDiagnosis(this)"', '', 1), 'html.parser')
    with pytest.raises(ValueError, match='cannot open'):
        check_diagnosis_inventory(missing_handler)
    assert json.loads((tmp_path / "report-manifest.json").read_text())["languages"] == ["zh"]
    home_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="extremum"):
        check_presentation(home_html.replace("winner comparison-winner", "winner", 1))
    detail_html = (tmp_path / "results/index.html").read_text(encoding="utf-8")
    index = json.loads((tmp_path / "result-shard-index.json").read_text())
    artifact_index = json.loads((tmp_path / "artifact-shard-index.json").read_text())
    shards = {path: json.loads((tmp_path / path).read_text()) for path in set(index.values())}
    check_result_payload(rows[0], index, artifact_index, shards)
    with pytest.raises(ValueError, match="Evidence target"):
        check_result_payload(rows[0], index, {**artifact_index, rows[0]["artifact_path"]: "shards/missing.json"}, shards)
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / "sources").exists()
    assert not (tmp_path / "references").exists()
    assert len(list((tmp_path / "shards").glob("*.json"))) == len(providers) + 1
    with pytest.raises(ValueError, match="Out-of-cohort"):
        check_presentation(home_html.replace('data-metric-direction="neutral"',
                                            'data-metric-direction="neutral"><strong class="comparison-winner">bad</strong><span', 1))
    with pytest.raises(ValueError, match="exact error"):
        check_presentation(detail_html.replace('data-report-detail="pending"', 'data-report-detail="failed"').replace('data-kind="error"', 'data-kind="other"'))
    with pytest.raises(ValueError, match="exactly one summary"):
        check_presentation(detail_html.replace('data-result-summary', 'data-no-summary'))
