import json
from pathlib import Path

import pytest
from PIL import Image

from harness.browser_use.publication import sanitize
from harness.fix_log import load_fix_log
from harness.trajectory_replay.webarena_report import (
    LEGACY_REPLAY_VALIDITY_INPUTS,
    _assets,
    _clean,
    _detail_page,
    _historical_replay_validity_notice,
    _measured_validity_identity,
    _metrics_table,
    _outcome_metrics,
    _public_evidence,
    _shell,
    _successful_distribution_table,
    _task_page,
    _unique_winners,
    _write_replay_fix_log,
)
from scripts.check_webarena_replay_report import (
    _check_english_localization,
    _semantic_page_inventory,
)


def _method(name: str, pass_rate: float, base: float) -> dict:
    metric = {"n": 90, "mean": base, "p50": base + 1, "p95": base + 2}
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
    # Pass rate/count and per-action latency only; early exits can reduce
    # all-task duration/resources without doing comparable work.
    assert rendered.count(">Best</span>") == 5
    assert "End-to-end time, all tasks" in rendered
    assert "n=90" in rendered
    assert "median" not in rendered.lower()


def test_outcome_distributions_keep_failures_missing_and_real_zero() -> None:
    rows = [
        {"status": "pass", "duration_ms": 100, "resource": {"cpu_ms": 0, "memory_mean_bytes": 0}},
        {"status": "fail", "duration_ms": 900, "resource": {"cpu_ms": 80}},
        {"status": "fail", "duration_ms": 0, "resource": {"cpu_ms": None}},
        {"status": "pass", "duration_ms": 200},
        {"status": "fail", "duration_ms": None, "resource": {"available": False, "cpu_ms": 0}},
    ]
    all_calls = _outcome_metrics(rows)
    assert all_calls["duration"] == {
        "n": 4,
        "mean": 300,
        "p50": 100,
        "p95": 900,
        "population": 5,
        "missing": 1,
    }
    assert all_calls["cpu"]["n"] == 2
    assert all_calls["cpu"]["mean"] == 40
    assert all_calls["cpu"]["missing"] == 3
    assert all_calls["memory"]["mean"] == 0
    assert all_calls["memory"]["n"] == 1
    assert all_calls["network_rx"]["mean"] is None
    assert all_calls["network_rx"]["n"] == 0
    own_success = _outcome_metrics([row for row in rows if row["status"] == "pass"])
    assert own_success["duration"]["n"] == 2
    assert own_success["duration"]["mean"] == 150


def test_own_success_distributions_do_not_select_shared_pass_tasks() -> None:
    # The browsers have disjoint successes: no shared pass, but both task
    # populations remain in the primary distributions.
    methods = []
    for name, statuses in [("A", ["pass", "fail"]), ("B", ["fail", "pass"])]:
        rows = [
            {"task_id": i, "status": status, "duration_ms": i + 1}
            for i, status in enumerate(statuses)
        ]
        method = _method(name, 0.5, 1)
        method.update(_outcome_metrics(rows))
        method.update(
            passed=1,
            total=2,
            successful=_outcome_metrics([r for r in rows if r["status"] == "pass"]),
        )
        methods.append(method)
        assert method["duration"]["n"] == 2
    assert methods[0]["successful"]["duration"]["mean"] == 1
    assert methods[1]["successful"]["duration"]["mean"] == 2
    for lang in ("zh", "en"):
        rendered = _successful_distribution_table(methods, lang)
        assert "n=1/2" in rendered and "n=0/1" in rendered
        assert 'class="winner"' not in rendered


def test_winners_require_complete_finite_participant_values_and_unique_best() -> None:
    assert _unique_winners([{"id": "a", "x": 0}, {"id": "b", "x": 1}], "x", higher=False) == {"a"}
    for other in (None, float("nan"), float("inf"), False, 0):
        assert not _unique_winners(
            [{"id": "a", "x": 0}, {"id": "b", "x": other}], "x", higher=False
        )
    assert not _unique_winners([{"id": "a", "x": 0}], "x", higher=False)


def test_english_release_gate_rejects_reader_facing_chinese(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><title>Replay</title></head><body><a title="Open">中文</a>'
        '<pre>{"evidence":"原样保留"}</pre></body></html>',
        encoding="utf-8",
    )
    _check_english_localization([page])

    page.write_text(
        "<html><head><title>Replay</title></head><body><p>未翻译正文</p></body></html>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Chinese prose"):
        _check_english_localization([page])

    page.write_text(
        '<html><head><title>Replay</title></head><body><button aria-label="打开">Open</button>'
        "</body></html>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="accessibility text"):
        _check_english_localization([page])


def test_release_gate_includes_fix_and_diagnostic_routes(tmp_path: Path) -> None:
    for relative in (
        "fix/one.html",
        "diagnostic/313.html",
        "en/fix/one.html",
        "en/diagnostic/313.html",
    ):
        page = tmp_path / relative
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<title>Fixture</title><p>Fixture</p>", encoding="utf-8")
    core, chinese_details, english_details = _semantic_page_inventory(
        tmp_path, ("fixes.html", "diagnostics.html")
    )
    assert {path.relative_to(tmp_path).as_posix() for path in core} == {
        "fixes.html",
        "diagnostics.html",
        "en/fixes.html",
        "en/diagnostics.html",
    }
    assert {path.relative_to(tmp_path).as_posix() for path in chinese_details} == {
        "fix/one.html",
        "diagnostic/313.html",
    }
    assert {path.relative_to(tmp_path).as_posix() for path in english_details} == {
        "en/fix/one.html",
        "en/diagnostic/313.html",
    }
    english_details[0].write_text("<title>Fixture</title><p>未翻译</p>", encoding="utf-8")
    with pytest.raises(ValueError, match="Chinese prose"):
        _check_english_localization(english_details)


def test_report_shell_localizes_chinese_title_and_footer() -> None:
    rendered = _shell("zh", "index", "浏览器轨迹重放", "<p>正文</p>")
    assert "<title>浏览器轨迹重放</title>" in rendered
    assert "WebArena-Verified 1.2.3 · 浏览器轨迹重放" in rendered
    assert "Browser Trajectory Replay</footer>" not in rendered
    detail = _shell(
        "zh", "results", "结果", "<p>内容</p>", depth=1, current_route="result/moli-29.html"
    )
    assert 'href="../en/result/moli-29.html"' in detail
    english = _shell(
        "en", "results", "Result", "<p>Content</p>", depth=1, current_route="result/moli-29.html"
    )
    assert 'href="../../result/moli-29.html"' in english


def test_mobile_header_keeps_language_and_theme_controls_visible(tmp_path: Path) -> None:
    _assets(tmp_path)
    css = (tmp_path / "assets/report.css").read_text(encoding="utf-8")

    assert ".language{display:none}" not in css
    assert "grid-template-columns:minmax(0,1fr) auto auto" in css
    assert ".language{grid-column:2;grid-row:1;display:inline}" in css
    assert ".theme{grid-column:3;grid-row:1}" in css
    assert ".top nav{grid-column:1/-1;grid-row:2" in css
    assert "code{overflow-wrap:anywhere;word-break:break-word}" in css


def test_historical_validity_notice_is_hash_bound_and_bilingual() -> None:
    results_sha256, dataset_sha256, profile_sha256 = next(iter(LEGACY_REPLAY_VALIDITY_INPUTS))
    chinese = _historical_replay_validity_notice(
        results_sha256=results_sha256,
        dataset_sha256=dataset_sha256,
        profile_sha256=profile_sha256,
        lang="zh",
    )
    english = _historical_replay_validity_notice(
        results_sha256=results_sha256,
        dataset_sha256=dataset_sha256,
        profile_sha256=profile_sha256,
        lang="en",
    )
    for phrase in (
        "Chromium 90/90、Moli 54/90",
        "[Snapshot error: …]",
        "37 道 RETRIEVE",
        "recorded_response",
        "Chromium 780×437/493、Moli 1920×1080",
        "当前 fail-closed 资格规则",
    ):
        assert phrase in chinese
    for phrase in (
        "Chromium 90/90 and Moli 54/90",
        "[Snapshot error: …]",
        "37 RETRIEVE tasks",
        "recorded_response",
        "Chromium 780×437/493 and Moli 1920×1080",
        "current fail-closed qualification rules",
    ):
        assert phrase in english
    assert "no score is manually changed" in english
    assert (
        _historical_replay_validity_notice(
            results_sha256="0" * 64,
            dataset_sha256=dataset_sha256,
            profile_sha256=profile_sha256,
            lang="en",
        )
        == ""
    )


def test_validity_identity_uses_measured_profile_not_publication_profile(tmp_path: Path) -> None:
    results = {"profile_sha256": "a" * 64}
    results_path = tmp_path / "results.json"
    dataset_path = tmp_path / "dataset.json"
    publication_path = tmp_path / "current-profile.json"
    results_path.write_text(json.dumps(results))
    dataset_path.write_text("{}")
    publication_path.write_text('{"host":"old.example"}')
    before = _measured_validity_identity(results, results_path, dataset_path)
    publication_path.write_text('{"host":"new.example","version":"new"}')
    assert _measured_validity_identity(results, results_path, dataset_path) == before
    assert before["profile_sha256"] == "a" * 64
    changed_run = _measured_validity_identity(
        {"profile_sha256": "b" * 64}, results_path, dataset_path
    )
    assert changed_run != before
    assert _measured_validity_identity({}, results_path, dataset_path)["profile_sha256"] == ""


def test_replay_fix_log_uses_shared_validation_and_native_bilingual_routes(
    tmp_path: Path,
) -> None:
    import hashlib

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    localized = {"zh": "夹具证据说明", "en": "Fixture evidence description"}
    image_path = tmp_path / "capture.png"
    Image.new("RGB", (4, 4), "white").save(image_path)
    artifacts = [
        {
            "id": "capture",
            "path": str(image_path),
            "kind": "image",
            "public_safe": True,
            "sha256": digest(image_path),
            "provenance": {
                "capture_method": "test fixture",
                "run_id": "fixture-run",
                "observation": "last_observed_page",
                "timing_note": localized,
            },
        }
    ]
    record = {
        "id": "fixture-fix",
        "scope": "diagnostic",
        "input_sha256": "1" * 64,
        **{
            key: localized for key in ("title", "symptom", "root_cause", "principle", "limitations")
        },
        "code_paths": [{"repository": "example", "path": "src/runtime.rs", "symbol": "layout"}],
        "tests": [{"command": "fixture-test --exact", "result": "1 passed", "receipt": "test"}],
    }
    for side in ("before", "after"):
        run = {
            "method": "fixture-browser",
            "version": side,
            "source_base": "abcdef1",
            "source_diff_sha256": "2" * 64,
            "binary_sha256": "3" * 64,
            "run_id": side,
        }
        receipt = {
            "schema": "lex_browser_use_fix_comparison_receipt/1",
            **run,
            "input_sha256": record["input_sha256"],
            "measurements": {"width": 4},
        }
        path = tmp_path / f"{side}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        artifacts.append(
            {
                "id": side,
                "path": str(path),
                "kind": "receipt",
                "public_safe": True,
                "sha256": digest(path),
            }
        )
        record[side] = {**run, "receipt": side, "image": "capture", "observation": localized}
    test_path = tmp_path / "test.json"
    test_path.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_fix_test_receipt/1",
                "command": "fixture-test --exact",
                "result": "1 passed",
            }
        ),
        encoding="utf-8",
    )
    artifacts.append(
        {
            "id": "test",
            "path": str(test_path),
            "kind": "receipt",
            "public_safe": True,
            "sha256": digest(test_path),
        }
    )
    source = tmp_path / "fix-log.json"
    source.write_text(
        json.dumps(
            {"schema": "lex_browser_use_fix_log/1", "records": [record], "artifacts": artifacts}
        ),
        encoding="utf-8",
    )
    model = load_fix_log(
        {"fix_log": {"source_path": str(source), "source_sha256": digest(source)}},
        sanitize_value=sanitize,
    )
    output = tmp_path / "report"
    _write_replay_fix_log(output, model)

    chinese = (output / "fix/fixture-fix.html").read_text(encoding="utf-8")
    english = (output / "en/fix/fixture-fix.html").read_text(encoding="utf-8")
    chinese_index = (output / "fixes.html").read_text(encoding="utf-8")
    english_index = (output / "en/fixes.html").read_text(encoding="utf-8")
    assert "夹具证据说明" in chinese and "Fixture evidence description" in english
    assert "夹具证据说明" not in english
    assert '<p class="eyebrow">修复证据</p>' in chinese
    assert '<p class="eyebrow">修复证据</p>' in chinese_index
    assert "Fix evidence" not in chinese and "Fix evidence" not in chinese_index
    assert '<p class="eyebrow">Fix evidence</p>' in english
    assert '<p class="eyebrow">Fix evidence</p>' in english_index
    assert 'href="../fixes.html"' in chinese and 'href="../fixes.html"' in english
    assert 'href="fix/fixture-fix.html"' in chinese_index
    assert 'href="fix/fixture-fix.html"' in english_index
    public = json.loads((output / "data/fix-log.json").read_text(encoding="utf-8"))
    assert str(tmp_path) not in json.dumps(public)
    assert public["records"][0]["code_paths"][0]["path"] == "src/runtime.rs"
    for artifact in public["artifacts"]:
        assert digest(output / artifact["href"]) == artifact["sha256"]

    record.pop("input_sha256")
    record["comparison_basis"] = {
        "kind": "same_task_contract",
        "task_contract_sha256": "4" * 64,
    }
    for index, side in enumerate(("before", "after"), 5):
        run = record[side]
        run.update(
            input_sha256=str(index) * 64,
            trajectory_sha256=str(index + 2) * 64,
            action_count=index + 3,
        )
        artifact = next(item for item in artifacts if item["id"] == run["receipt"])
        path = Path(artifact["path"])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt.update(
            task_contract_sha256=record["comparison_basis"]["task_contract_sha256"],
            input_sha256=run["input_sha256"],
            trajectory_sha256=run["trajectory_sha256"],
            action_count=run["action_count"],
        )
        path.write_text(json.dumps(receipt), encoding="utf-8")
        artifact["sha256"] = digest(path)
    source.write_text(
        json.dumps(
            {"schema": "lex_browser_use_fix_log/1", "records": [record], "artifacts": artifacts}
        ),
        encoding="utf-8",
    )
    revised = load_fix_log(
        {"fix_log": {"source_path": str(source), "source_sha256": digest(source)}},
        sanitize_value=sanitize,
    )
    revised_output = tmp_path / "revised-report"
    _write_replay_fix_log(revised_output, revised)
    revised_chinese = (revised_output / "fix/fixture-fix.html").read_text(encoding="utf-8")
    revised_english = (revised_output / "en/fix/fixture-fix.html").read_text(encoding="utf-8")
    assert "验收合同相同，轨迹已修订" in revised_chinese
    assert "共同输入 SHA-256" not in revised_chinese
    assert "输入 SHA-256" in revised_chinese and "轨迹 SHA-256" in revised_chinese
    assert "The acceptance contract is unchanged; the trajectory was revised" in revised_english
    assert "Shared input SHA-256" not in revised_english
    assert ">8<" in revised_chinese and ">9<" in revised_chinese


def test_replay_task_and_result_have_connected_drilldown() -> None:
    task = {
        "task_id": 29,
        "task": {"intent": "Find the latest post", "sites": ["reddit"]},
        "source": {"kind": "expert_repair_spec"},
        "tool_calls": [{"action": "click", "selector": "#latest"}],
        "evaluation_input": {"task_type": "RETRIEVE", "status": "SUCCESS"},
    }
    peers = [
        {
            "engine": "chromium",
            "participant_id": "chromium",
            "browser": "Chromium",
            "task_id": 29,
            "status": "pass",
            "completed_actions": 1,
            "action_count": 1,
        },
        {
            "engine": "moli",
            "participant_id": "moli",
            "browser": "Moli",
            "task_id": 29,
            "status": "fail",
            "completed_actions": 0,
            "action_count": 1,
        },
    ]
    task_html = _task_page("zh", task, peers)
    assert 'href="../result/moli-29.html#diagnosis"' in task_html
    assert 'href="../result/chromium-29.html#trajectory"' in task_html
    assert 'href="../en/task/29.html"' in task_html
    assert "冻结的操作轨迹" in task_html
    assert "信息提取" in task_html

    attempt = {
        "engine": "moli",
        "participant_id": "moli",
        "task_id": 29,
        "status": "fail",
        "completed_action_count": 0,
        "action_count": 1,
        "duration_ms": 1200,
        "resource": {"cpu_ms": 100, "memory_mean_bytes": 10_000_000},
    }
    evidence = {
        "programmatic_failure": {
            "stage": "browser_tool_execution",
            "step": 1,
            "message": "Click failed: timed out",
        },
        "review": {
            "label_zh": "目标不可操作",
            "label_en": "Target not actionable",
            "reason_zh": "目标没有出现",
            "reason_en": "Target did not appear",
            "remediation_owner": "Browser",
            "remediation_status": "open",
        },
        "trajectory": [
            {
                "index": 1,
                "arguments": {"action": "click", "selector": "#latest"},
                "duration_ms": 1000,
                "status": "error",
                "result": "Click failed: timed out\nCall log: #latest",
            }
        ],
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
        "engine": "moli",
        "participant_id": "moli",
        "task_id": 29,
        "status": "fail",
        "completed_action_count": 0,
        "action_count": 2,
        "duration_ms": 1200,
    }
    evidence = {
        "programmatic_failure": {
            "stage": "browser_tool_execution",
            "step": 1,
            "message": "Timed out",
        },
        "review": None,
        "trajectory": [
            {"index": 1, "arguments": {"action": "click"}, "status": "error", "result": "Timed out"}
        ],
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
        "/root/work/trace.json",
        "/private/tmp/run/trace.json",
        "/var/lib/docker/trace.json",
        "/opt/hostedtoolcache/trace.json",
    ):
        assert _clean(f"failure at {path}") == "failure at [local path removed]"


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_same_task_keeps_two_moli_versions_and_chromium_separate(lang) -> None:
    peers = [
        {
            "participant_id": identity,
            "engine": engine,
            "browser": identity,
            "status": status,
            "task_id": 29,
            "completed_actions": 1,
            "action_count": 1,
        }
        for identity, engine, status in (
            ("chromium-ref", "chromium", "pass"),
            ("moli-old", "moli", "fail"),
            ("moli-fixed", "moli", "pass"),
        )
    ]
    html = _task_page(lang, {"task_id": 29, "task": {"intent": "Find a post"}}, peers)
    for identity in ("chromium-ref", "moli-old", "moli-fixed"):
        assert f"../result/{identity}-29.html" in html
    assert "../result/moli-old-29.html#diagnosis" in html
    assert "../result/moli-fixed-29.html#trajectory" in html


@pytest.mark.parametrize("lang", ["zh", "en"])
@pytest.mark.parametrize("legacy", [True, False])
def test_fixed_input_pages_scope_qualification_to_measured_identity(lang, legacy) -> None:
    values = next(iter(LEGACY_REPLAY_VALIDITY_INPUTS))
    identity = dict(
        zip(("results_sha256", "dataset_sha256", "profile_sha256"), values, strict=True)
    )
    if not legacy:
        identity["results_sha256"] = "f" * 64
    notice = _historical_replay_validity_notice(**identity, lang=lang)
    task = {"task_id": 29, "task": {"intent": "Find a post"}, "tool_calls": []}
    attempt = {
        "engine": "moli",
        "participant_id": "moli",
        "task_id": 29,
        "status": "pass",
        "completed_action_count": 0,
        "action_count": 0,
        "duration_ms": 0,
    }
    evidence = {
        "programmatic_failure": {"stage": "pass", "step": None, "message": ""},
        "review": None,
        "trajectory": [],
        "screenshot": None,
    }
    pages = [
        _task_page(lang, task, [], notice),
        _detail_page(lang, attempt, task, {"label": "Moli"}, evidence, [], notice),
    ]
    for html in pages:
        assert ('class="callout historical-validity"' in html) is legacy
        assert "qualified fixed input" not in html
        assert "已验证固定输入" not in html
        assert "Task contract and qualified trajectory" not in html
        if legacy:
            assert ("旧协议" if lang == "zh" else "legacy protocol") in html


def test_report_authored_copy_does_not_claim_unconditional_current_qualification() -> None:
    import inspect

    from harness.trajectory_replay import webarena_report

    source = inspect.getsource(webarena_report)
    for obsolete in (
        "90 条经过资格验证",
        "90 qualified fixed",
        "每题一条经过资格验证",
        "each with one qualified fixed",
        "资格验证无未决项",
        "No qualification items remain unresolved",
        "条已资格验证",
    ):
        assert obsolete not in source
    for body in ("dataset_body", "results_body", "review_body", "viewer_body"):
        assert f"validity_notice + {body}" in source


def test_task173_corrected_review_reaches_public_json_and_bilingual_detail(tmp_path: Path) -> None:
    review = json.loads(Path("research/trajectory-replay-failure-review.json").read_text())
    group, item = next(
        (group, item)
        for group in review["cause_groups"]
        for item in group["results"]
        if item["task_id"] == 173
    )
    revision = next(
        row for row in review["review_revisions"] if row["id"] == item["review_revision"]
    )
    attempt = {
        "engine": "moli",
        "participant_id": "moli",
        "task_id": 173,
        "status": "fail",
        "process_status": "fail",
        "error": "RuntimeError: step 4 failed: target not visible",
        "completed_action_count": 3,
        "action_count": 4,
        "duration_ms": 1000,
    }
    evidence = _public_evidence(
        attempt, {**item, "cause_group": group, "revision_evidence": revision}, tmp_path
    )
    assert evidence["review"]["responsibility"] == "benchmark"
    prior = evidence["review"]["revision_evidence"]["superseded_review"]
    assert prior["responsibility"] == "browser"
    assert evidence["result"]["status"] == "fail"
    task = {"task_id": 173, "task": {"intent": "Search"}, "tool_calls": []}
    for lang, phrase in (("zh", "共享基准设置干扰"), ("en", "shared benchmark settings")):
        html = _detail_page(lang, attempt, task, {"label": "Moli"}, evidence, [])
        assert phrase in html


def test_missing_submission_is_located_at_trigger_without_calling_it_a_failed_action(
    tmp_path: Path,
) -> None:
    import hashlib
    import json

    trace = [
        {"index": 1, "arguments": {"action": "navigate"}, "status": "success", "result": "Opened"},
        {
            "index": 2,
            "arguments": {"action": "evaluate"},
            "status": "success",
            "result": "Save scheduled",
        },
        {
            "index": 3,
            "arguments": {"action": "wait"},
            "status": "success",
            "result": "Wait completed",
        },
    ]
    evaluation = {
        "status": "failure",
        "evaluators_results": [
            {
                "evaluator_name": "NetworkEventEvaluator",
                "expected": {"http_method": "POST", "url": "/product/save"},
                "assertions": [
                    {
                        "assertion_name": "missing_navigation_event",
                        "assertion_msgs": ["Required POST was not observed"],
                    }
                ],
            }
        ],
    }
    refs = {}
    for name, value in (("trace", trace), ("evaluation", evaluation)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        refs[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    attempt = {
        "engine": "moli",
        "participant_id": "moli",
        "task_id": 464,
        "status": "fail",
        "process_status": "pass",
        "evaluator_status": "fail",
        "action_count": 3,
        "completed_action_count": 3,
        "duration_ms": 1000,
        "evidence": refs,
    }
    public = _public_evidence(attempt, None, tmp_path / "report")
    finding = public["programmatic_failure"]
    assert finding["stage"] == "side_effect_not_observed"
    assert finding["step"] is None
    assert finding["trigger_step"] == 2
    assert finding["message"].endswith("Required POST was not observed")
    assert finding["expected_network"][0]["http_method"] == "POST"
