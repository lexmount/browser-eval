import json
from pathlib import Path

from harness.web_fetch.config import load_dataset
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.report_build import build_report
from harness.web_fetch.runner import run_evaluation

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/risk-control/dataset.json"


def test_full_risk_control_matrix_builds_existing_interactive_report(tmp_path, capsys):
    dataset, cases = load_dataset(DATASET)
    measured = {
        **dataset,
        "coverage_plan": {"risk_control": ["fixture-risk"]},
        "comparison": {
            "kind": "risk_control_100_report_smoke",
            "timeout_seconds": 5,
            "runs_per_case": 1,
            "concurrency": 1,
            "max_failure_retries": 0,
            "conversion": "offline deterministic fixture",
            "require_independent_result_review": False,
        },
    }
    evidence = tmp_path / "evidence"
    provider_config = tmp_path / "provider.json"
    provider_config.write_text(json.dumps({"fixture-risk": {"type": "fixture"}}))
    records, _ = run_evaluation(
        output_dir=evidence,
        dataset_meta=measured,
        dataset_path=DATASET,
        cases=cases,
        providers=[FixtureProvider("fixture-risk", {"type": "fixture"})],
        provider_config_path=provider_config,
        runs=1,
        timeout=5,
        delay=0,
        provider_concurrency=1,
        max_failure_retries=0,
    )
    capsys.readouterr()
    report = tmp_path / "report"
    index = build_report(evidence, report)
    detail = (report / "attempts/fixture-risk__risk-diverse-192__r001.html").read_text()
    case_page = (report / "datasets/risk-diverse-192.html").read_text()
    style = (report / "assets/style.css").read_text()
    home = index.read_text()

    assert len(records) == 100
    assert index.is_file()
    assert "历史访问受限网址" in home
    assert "风控网站内容" not in home
    assert "资格只解释样本来源" in home
    assert "组合规则结果" in detail
    assert "homepage-news" in detail
    assert "同条目字段" in detail
    assert "运行日志与证据" in detail
    assert "查看本次真实日志" in detail
    assert "diagnostic-log" in detail
    assert "至少 3 条" in case_page
    assert "/archive/" in case_page
    assert ".table-wrap{overflow:auto" in style
    assert "pre{white-space:pre-wrap;overflow-wrap:anywhere" in style
    assert "max-height:70vh;overflow:auto" in style
