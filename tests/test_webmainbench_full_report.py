"""Reader-facing controls for the complete-cohort report renderer."""
import json
import hashlib
import subprocess
from pathlib import Path
from bs4 import BeautifulSoup
import pytest

from harness.web_fetch.report import generate_webmainbench_full
from scripts.report_presentation_contract import check_presentation
from scripts.check_webmainbench_full_report import check_diagnosis_inventory, check_result_payload


def test_full_report_renders_six_way_details_and_failure_evidence(tmp_path: Path) -> None:
    providers = ["moli", "lightpanda", "obscura", "chromium", "kitesurf", "moli-fixed"]
    case_id = "case-1"
    metrics = ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS")
    (tmp_path / "artifacts").mkdir()
    rows = []
    physical = []
    summaries = {}
    for index, provider in enumerate(providers):
        text = "" if provider == "moli" else f"response {provider}"
        artifact = f"artifacts/{provider}.md"
        (tmp_path / artifact).write_text(text, encoding="utf-8")
        physical_path = f"artifacts/{provider}-p01.txt"
        (tmp_path / physical_path).write_text(text, encoding="utf-8")
        output_sha = hashlib.sha256(text.encode()).hexdigest()
        physical.append({"provider": provider, "track_id": case_id,
                         "physical_artifact_path": physical_path, "output_sha256": output_sha,
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
            "elapsed_ms": 100 + index, "total_elapsed_ms": 100 + index,
            "physical_attempt_count": 1, "provider_metadata": {"stderr": "fixture error"},
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
               "source_dataset_sha256": "a" * 64, "official_scorer_commit": "b" * 40}
    content_keys = ("home_title", "home_lead", "score_note", "latency_note", "all_latency_note", "diagnostic_note", "infographic_caption",
                    "dataset_lead", "analysis_lead", "method_lead", "method_input",
                    "method_scoring", "method_correction", "guide_lead", "guide_scope",
                    "guide_outputs", "guide_diagnosis", "guide_example", "reproduce_lead",
                    "guide_method_scope", "guide_conclusion")
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
    for provider in providers:
        content["guide_method_" + provider] = "逐项观察"
    (tmp_path / "report-content.json").write_text(json.dumps(content), encoding="utf-8")
    (tmp_path / "provider-adoption.json").write_text(json.dumps({"included": [], "cutoff": "2026-08-26"}), encoding="utf-8")
    (tmp_path / "failure-analysis.json").write_text(json.dumps({"reviewed_failures": [
        {"provider": "moli", "case_id": case_id, "category": "browser_exit", "category_label": "Moli 解析器崩溃",
         "cause": "Fixture failed", "treatment": "Investigate", "status": "open"}]}), encoding="utf-8")
    (tmp_path / "physical-attempts.jsonl").write_text("".join(json.dumps(row) + "\n" for row in physical), encoding="utf-8")
    generate_webmainbench_full(tmp_path, dataset, rows, summaries, profile)
    subprocess.run(["node", "--check", str(tmp_path / "assets/report.js")], check=True)
    home = check_presentation((tmp_path / "index.html").read_text(encoding="utf-8"))
    assert len(home.select('td[data-metric-direction="max"]')) == 36
    assert len(home.select('td[data-metric-direction="max"] .comparison-winner')) == 6
    detail = check_presentation((tmp_path / "results/index.html").read_text(encoding="utf-8"))
    assert detail.select_one('[data-failure-diagnosis]') is not None
    comparison = BeautifulSoup((tmp_path / "cases/index.html").read_text(encoding="utf-8"), "html.parser")
    assert comparison.select_one("#case-results") is not None
    assert comparison.select_one("#case-comparison") is not None
    assert comparison.select_one("#case-reference") is not None
    analysis = BeautifulSoup((tmp_path / "analysis/index.html").read_text(encoding="utf-8"), "html.parser")
    check_diagnosis_inventory(analysis)
    assert len(analysis.select("tr[data-result]")) == 6
    assert analysis.select_one('tr[data-provider="moli-fixed"]') is not None
    assert analysis.select_one("#cause") is not None
    assert analysis.select_one('tr[data-cause="Moli 解析器崩溃"]') is not None
    missing_dialog = BeautifulSoup(str(analysis).replace('id="diagnosis-error"', 'id="missing-error"'), 'html.parser')
    with pytest.raises(ValueError, match='diagnosis dialog'):
        check_diagnosis_inventory(missing_dialog)
    missing_button = BeautifulSoup(str(analysis).replace('data-diagnose=""', 'data-missing=""', 1), 'html.parser')
    with pytest.raises(ValueError, match='cannot open'):
        check_diagnosis_inventory(missing_button)
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
    assert len(list((tmp_path / "shards").glob("*.json"))) == 7
    with pytest.raises(ValueError, match="Out-of-cohort"):
        check_presentation(home_html.replace('data-metric-direction="neutral"',
                                            'data-metric-direction="neutral"><strong class="comparison-winner">bad</strong><span', 1))
    with pytest.raises(ValueError, match="exact error"):
        check_presentation(detail_html.replace('data-report-detail="pending"', 'data-report-detail="failed"').replace('data-kind="error"', 'data-kind="other"'))
    with pytest.raises(ValueError, match="exactly one summary"):
        check_presentation(detail_html.replace('data-result-summary', 'data-no-summary'))
