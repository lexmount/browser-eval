from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_report import (
    _clean,
    _detail_page,
    _metrics_table,
    _public_evidence,
    _shell,
    _task_page,
)
from scripts.check_webarena_replay_report import _check_english_localization


def _method(name: str, pass_rate: float, base: float) -> dict:
    metric = {"mean": base, "p50": base + 1, "p95": base + 2}
    return {
        "id": name,
        "label": name,
        "passed": int(pass_rate * 90),
        "total": 90,
        "pass_rate": pass_rate,
        "duration": metric,
        "action_latency": metric,
        "cpu": metric,
        "memory": metric,
    }


def test_core_metrics_are_registry_driven_and_mark_only_unique_winners() -> None:
    rendered = _metrics_table(
        [
            _method("Browser A", 1.0, 3.0),
            _method("Browser B", 0.8, 2.0),
            _method("Browser C", 0.8, 1.0),
        ],
        "en",
    )
    assert all(name in rendered for name in ("Browser A", "Browser B", "Browser C"))
    assert rendered.count(">Best</span>") == 14
    assert "median" not in rendered.lower()


def test_english_release_gate_rejects_reader_facing_chinese(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><title>Replay</title></head><body><a title="Open">中文</a>'
        '<pre>{"evidence":"原样保留"}</pre></body></html>',
        encoding="utf-8",
    )
    _check_english_localization([page])

    page.write_text(
        '<html><head><title>Replay</title></head><body><p>未翻译正文</p></body></html>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Chinese prose"):
        _check_english_localization([page])

    page.write_text(
        '<html><head><title>Replay</title></head><body><button aria-label="打开">Open</button>'
        '</body></html>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="accessibility text"):
        _check_english_localization([page])


def test_report_shell_localizes_chinese_title_and_footer() -> None:
    rendered = _shell("zh", "index", "浏览器轨迹重放", "<p>正文</p>")
    assert "<title>浏览器轨迹重放</title>" in rendered
    assert "WebArena-Verified 1.2.3 · 浏览器轨迹重放" in rendered
    assert "Browser Trajectory Replay</footer>" not in rendered
    detail = _shell("zh", "results", "结果", "<p>内容</p>", depth=1, current_route="result/moli-29.html")
    assert 'href="../en/result/moli-29.html"' in detail
    english = _shell("en", "results", "Result", "<p>Content</p>", depth=1, current_route="result/moli-29.html")
    assert 'href="../../result/moli-29.html"' in english


def test_replay_task_and_result_have_connected_drilldown() -> None:
    task = {
        "task_id": 29,
        "task": {"intent": "Find the latest post", "sites": ["reddit"]},
        "source": {"kind": "expert_repair_spec"},
        "tool_calls": [{"action": "click", "selector": "#latest"}],
        "evaluation_input": {"task_type": "RETRIEVE", "status": "SUCCESS"},
    }
    peers = [
        {"engine": "chromium", "browser": "Chromium", "task_id": 29, "status": "pass", "completed_actions": 1, "action_count": 1},
        {"engine": "moli", "browser": "Moli", "task_id": 29, "status": "fail", "completed_actions": 0, "action_count": 1},
    ]
    task_html = _task_page("zh", task, peers)
    assert 'href="../result/moli-29.html#diagnosis"' in task_html
    assert 'href="../result/chromium-29.html#trajectory"' in task_html
    assert 'href="../en/task/29.html"' in task_html
    assert "冻结的操作轨迹" in task_html
    assert "信息提取" in task_html

    attempt = {"engine": "moli", "task_id": 29, "status": "fail", "completed_action_count": 0, "action_count": 1, "duration_ms": 1200, "resource": {"cpu_ms": 100, "memory_mean_bytes": 10_000_000}}
    evidence = {
        "programmatic_failure": {"stage": "browser_tool_execution", "step": 1, "message": "Click failed: timed out"},
        "review": {"label_zh": "目标不可操作", "label_en": "Target not actionable", "reason_zh": "目标没有出现", "reason_en": "Target did not appear", "remediation_owner": "Browser", "remediation_status": "open"},
        "trajectory": [{"index": 1, "arguments": {"action": "click", "selector": "#latest"}, "duration_ms": 1000, "status": "error", "result": "Click failed: timed out\nCall log: #latest"}],
        "screenshot": None,
    }
    detail = _detail_page("zh", attempt, task, {"label": "Moli"}, evidence, peers)
    assert 'id="step-1"' in detail
    assert 'href="#step-1"' in detail
    assert 'data-open-step="1"' in detail
    assert 'data-standard-url="../data/trajectory/29.json"' in detail
    assert 'data-evidence-url="../data/evidence/moli-29.json"' in detail
    assert "Call log: #latest" not in detail  # Full logs load only when a step is opened.
    assert "标准轨迹与实际执行" in detail
    assert "目标没有出现" in detail
    assert 'href="../task/29.html"' in detail
    assert 'href="../evidence-viewer.html?file=data/evidence/moli-29.json"' in detail


def test_unexecuted_qualified_step_stays_visible_and_is_not_called_an_action_error() -> None:
    task = {
        "task_id": 29,
        "task": {"intent": "Find the latest post", "sites": ["reddit"]},
        "tool_calls": [
            {"action": "click", "selector": "#latest"},
            {"action": "snapshot"},
        ],
    }
    attempt = {
        "engine": "moli", "task_id": 29, "status": "fail",
        "completed_action_count": 0, "action_count": 2, "duration_ms": 1200,
    }
    evidence = {
        "programmatic_failure": {"stage": "browser_tool_execution", "step": 1, "message": "Timed out"},
        "review": None,
        "trajectory": [{"index": 1, "arguments": {"action": "click"}, "status": "error", "result": "Timed out"}],
        "screenshot": None,
    }
    detail = _detail_page("zh", attempt, task, {"label": "Moli"}, evidence, [])
    assert 'id="step-2"' in detail
    assert 'data-open-step="2"' in detail
    assert "未执行" in detail
    assert detail.count('class="aligned-step failed-step"') == 1


def test_report_removes_macos_and_linux_local_paths() -> None:
    for path in (
        "/Users/example/run/trace.json",
        "/home/runner/work/trace.json",
        "/private/tmp/run/trace.json",
        "/var/lib/docker/trace.json",
        "/opt/hostedtoolcache/trace.json",
    ):
        assert _clean(f"failure at {path}") == "failure at [local path removed]"


def test_missing_submission_is_located_at_trigger_without_calling_it_a_failed_action(
    tmp_path: Path,
) -> None:
    import hashlib
    import json

    trace = [
        {"index": 1, "arguments": {"action": "navigate"}, "status": "success", "result": "Opened"},
        {"index": 2, "arguments": {"action": "evaluate"}, "status": "success", "result": "Save scheduled"},
        {"index": 3, "arguments": {"action": "wait"}, "status": "success", "result": "Wait completed"},
    ]
    evaluation = {
        "status": "failure",
        "evaluators_results": [
            {
                "evaluator_name": "NetworkEventEvaluator",
                "expected": {"http_method": "POST", "url": "/product/save"},
                "assertions": [{"assertion_name": "missing_navigation_event", "assertion_msgs": ["Required POST was not observed"]}],
            }
        ],
    }
    refs = {}
    for name, value in (("trace", trace), ("evaluation", evaluation)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        refs[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    attempt = {
        "engine": "moli", "task_id": 464, "status": "fail", "process_status": "pass",
        "evaluator_status": "fail", "action_count": 3, "completed_action_count": 3,
        "duration_ms": 1000, "evidence": refs,
    }
    public = _public_evidence(attempt, None, tmp_path / "report")
    finding = public["programmatic_failure"]
    assert finding["stage"] == "side_effect_not_observed"
    assert finding["step"] is None
    assert finding["trigger_step"] == 2
    assert finding["message"].endswith("Required POST was not observed")
    assert finding["expected_network"][0]["http_method"] == "POST"
