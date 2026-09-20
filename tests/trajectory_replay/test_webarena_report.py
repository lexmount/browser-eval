import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from harness.browser_use.publication import sanitize
from harness.fix_log import load_fix_log
from harness.trajectory_replay.webarena_report import (
    LEGACY_REPLAY_VALIDITY_INPUTS,
    _assets,
    build_webarena_replay_report,
    _clean,
    _copy_failure_screenshot,
    _detail_page,
    _historical_replay_validity_notice,
    _json_sha256,
    _legacy_retrieval_provenance,
    _load_artifact,
    _measured_validity_identity,
    _metrics_table,
    _outcome_metrics,
    _planned_actions_digest,
    _public_evidence,
    _reference_validity_by_task,
    require_all_reference_validity,
    _shell,
    _successful_distribution_table,
    _task_page,
    _unique_winners,
    _validated_planned_actions,
    _verdict_label,
    _write_replay_fix_log,
)
from scripts.check_webarena_replay_report import (
    check as check_report,
    _check_affected36_comparison,
    _check_core_comparison,
    _check_fixed36_output_scope,
    _check_formal36_output_scope,
    _check_legacy_retrieval_audit,
    _check_result_detail_semantics,
    _check_english_localization,
    _semantic_page_inventory,
)
from scripts.build_webarena_replay_report import _bound_dataset_path


def test_report_build_requires_hash_bound_corrected_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "corrected.json"
    dataset.write_text('{"trajectories": []}', encoding="utf-8")
    profile = {"dataset": {"path": str(dataset), "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest()}}
    assert _bound_dataset_path(profile) == dataset
    with pytest.raises(ValueError, match="changed"):
        _bound_dataset_path({"dataset": {**profile["dataset"], "sha256": "0" * 64}})
    with pytest.raises(ValueError, match="requires"):
        _bound_dataset_path({})
    link = tmp_path / "link.json"
    link.symlink_to(dataset)
    with pytest.raises(ValueError, match="symlinked"):
        _bound_dataset_path({"dataset": {"path": str(link), "sha256": profile["dataset"]["sha256"]}})


def test_complete_three_method_report_builds_one_unified_matrix(tmp_path: Path, monkeypatch) -> None:
    methods = [
        {"id": "chromium", "engine": "chromium", "label": "Chromium"},
        {"id": "moli-old", "engine": "moli", "label": "Moli 1.1.3"},
        {"id": "moli-fixed", "engine": "moli", "label": "Moli 1.1.7"},
    ]
    profile = {"methods": methods, "qualification": {"reference_engine": "chromium"},
               "execution": {"method_parallelism": 1, "task_parallelism": 1,
                             "reset_before_every_task": False,
                                 "reset_before_every_method": True,
                                 "teardown_between_methods": True,
                                 "retry_failed_task": False,
                                 "site_stack": "self-hosted Rainbond WebArena app"}}
    actions = [{"action": "snapshot"}]
    attempts = [
        {"participant_id": method["id"], "engine": method["engine"],
         "task_id": 29, "status": "pass", "process_status": "pass",
         "evaluator_status": "pass", "action_count": 1,
         "completed_action_count": 1, "duration_ms": 100,
         "planned_actions": actions, "planned_actions_sha256": _planned_actions_digest(actions),
         "reference_validity": {"status": "valid"},
         "resource": {}, "evidence": {}}
        for method in methods
    ]
    data = {"attempts": attempts, "physical_executions": attempts}
    monkeypatch.setattr(
        "harness.trajectory_replay.webarena_report.load_validated_failure_review",
        lambda *args, **kwargs: {"results": data, "by_result": {}},
    )
    files = {}
    for name, value in (
        ("profile.json", profile),
        ("dataset.json", {"trajectories": [{"task_id": 29,
            "task": {"intent": "Find a post", "sites": ["reddit"]},
            "tool_calls": actions,
            "source": {"kind": "authoritative_successful_browser_use_execution"},
            "evaluation_input": {"task_type": "NAVIGATE"}}]}),
        ("results.json", data),
        ("review.json", {}),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        files[name] = path
    data["dataset_sha256"] = hashlib.sha256(files["dataset.json"].read_bytes()).hexdigest()
    frozen_task = json.loads(files["dataset.json"].read_text())["trajectories"][0]
    contract_sha256 = _json_sha256({
        "task": frozen_task["task"], "evaluation_input": frozen_task["evaluation_input"],
    })
    for attempt in attempts:
        attempt["task_contract_sha256"] = contract_sha256
    files["results.json"].write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "report"
    manifest = build_webarena_replay_report(
        results_path=files["results.json"], dataset_path=files["dataset.json"],
        review_path=files["review.json"], profile_path=files["profile.json"],
        output_dir=output, include_english=True,
    )
    assert manifest["participants"] == [item["id"] for item in methods]
    assert manifest["logical_results"] == 3
    assert manifest["routes"] == 20
    assert "36/36" not in (output / "index.html").read_text()
    assert "fixes.html" not in (output / "index.html").read_text()
    assert "BrowserTool 与浏览器的组合" in (output / "index.html").read_text()
    assert "BrowserTool–browser combinations" in (output / "en/index.html").read_text()
    assert "参考答案均已核验" not in (output / "index.html").read_text()
    assert "with verified references" not in (output / "en/index.html").read_text()
    assert "独立复核范围见复核说明" in (output / "index.html").read_text()
    assert "independent review scope" in (output / "en/index.html").read_text()
    for route in ("index.html", "results.html", "dataset.html", "environment.html"):
        assert "历史 90 题" not in (output / route).read_text()
        assert "historical 90-task" not in (output / "en" / route).read_text()
    assert "可核验题通过" not in (output / "index.html").read_text()
    assert "Passes on verifiable tasks" not in (output / "en/index.html").read_text()
    assert "每个方法开始前恢复站点基线" in (output / "environment.html").read_text()
    assert "Colima" not in (output / "environment.html").read_text()
    for route in ("environment.html", "en/environment.html"):
        environment = (output / route).read_text()
        assert 'data-topology-fallback' in environment and 'class="mermaid"' in environment
        assert 'Rainbond WebArena' in environment
    assert not (output / "diagnostics.html").exists()
    assert check_report(output)["html_pages"] == 20
    manifest = build_webarena_replay_report(
        results_path=files["results.json"], dataset_path=files["dataset.json"],
        review_path=files["review.json"], profile_path=files["profile.json"],
        output_dir=output,
    )
    assert manifest["languages"] == ["zh"]
    assert manifest["english_status"] == "not_generated"
    assert manifest["routes"] == 11
    assert '英文报告未生成' in (output / "en/index.html").read_text()
    assert not (output / "en/results.html").exists()
    assert 'class="language" href="../en/index.html"' in (output / "result/chromium-29.html").read_text()
    assert check_report(output)["html_pages"] == 11
    stale = output / "en/results.html"
    stale.write_text("stale English report")
    with pytest.raises(ValueError, match="stale English"):
        check_report(output)
    stale.unlink()
    data["attempts"] = attempts[:-1]
    with pytest.raises(ValueError, match="complete participant-task matrix"):
        build_webarena_replay_report(
            results_path=files["results.json"], dataset_path=files["dataset.json"],
            review_path=files["review.json"], profile_path=files["profile.json"],
            output_dir=output,
        )
    assert (output / "index.html").is_file()


def test_method_specific_planned_actions_are_hash_bound() -> None:
    actions = [{"action": "click", "selector": "#submit"}]
    attempt = {
        "participant_id": "moli-fixed", "task_id": 29, "action_count": 1,
        "planned_actions": actions,
        "planned_actions_sha256": _planned_actions_digest(actions),
    }
    assert _validated_planned_actions(attempt) == actions
    with pytest.raises(ValueError, match="planned actions"):
        _validated_planned_actions({**attempt, "planned_actions": [{"action": "snapshot"}]})
    with pytest.raises(ValueError, match="planned actions"):
        _validated_planned_actions({**attempt, "action_count": 2})
    with pytest.raises(ValueError, match="planned actions"):
        _validated_planned_actions({**attempt, "planned_actions_sha256": "0" * 64})


def test_reference_validity_must_be_explicit_and_common_to_all_methods() -> None:
    rows = [
        {"participant_id": "chromium", "task_id": 243,
         "reference_validity": {"status": "unverifiable"}},
        {"participant_id": "moli", "task_id": 243,
         "reference_validity": {"status": "unverifiable"}},
    ]
    assert _reference_validity_by_task(rows) == {243: "unverifiable"}
    with pytest.raises(ValueError, match="missing"):
        _reference_validity_by_task([rows[0], {"participant_id": "moli", "task_id": 243}])
    with pytest.raises(ValueError, match="disagree"):
        _reference_validity_by_task([rows[0], {**rows[1], "reference_validity": {"status": "valid"}}])
    assert _verdict_label("fail", "unverified", "zh") == "官方失败；答案不可核验"


def test_formal_reference_validity_gate_requires_all_selected_tasks_for_all_methods() -> None:
    methods = [{"id": name, "engine": "chromium" if name == "chromium" else "moli"}
               for name in ("chromium", "moli-old", "moli-fixed")]
    profile = {"methods": methods, "task_selection": {"task_count": 90},
               "scoring": {"require_all_reference_validity": True}}
    dataset = {"trajectories": [{"task_id": task_id} for task_id in range(90)]}
    attempts = [
        {"participant_id": method["id"], "engine": method["engine"],
         "task_id": task_id, "reference_validity": {"status": "valid"}}
        for method in methods for task_id in range(90)
    ]
    results = {"attempts": attempts}
    require_all_reference_validity(profile, results, dataset)
    attempts[0]["reference_validity"]["status"] = "unverifiable"
    with pytest.raises(ValueError, match="disagree"):
        require_all_reference_validity(profile, results, dataset)
    for method_index in (1, 2):
        attempts[method_index * 90]["reference_validity"]["status"] = "unverifiable"
    with pytest.raises(ValueError, match="unverifiable tasks: \\[0\\]"):
        require_all_reference_validity(profile, results, dataset)
    diagnostic = {"methods": methods}
    require_all_reference_validity(diagnostic, results, dataset)
    attempts.pop()
    with pytest.raises(ValueError, match="every method-task result"):
        require_all_reference_validity(profile, results, dataset)


def test_core_three_method_comparison_rejects_wrong_score_and_tie_highlight(tmp_path: Path) -> None:
    participants = ["chromium", "moli-old", "moli-fixed"]
    results = [
        {"participant_id": method, "task_id": task, "status": "pass", "semantic_status": "valid"}
        for method in participants for task in range(2)
    ]
    methods = [
        {"participant_id": method, "label": method,
         "valid_reference_passed": 2, "valid_reference_total": 2}
        for method in participants
    ]
    row = lambda method, count=2, marker="": (
        f'<tr><td>{method}</td><td data-metric="core-pass" '
        f'data-metric-direction="higher" data-sort="{count}">{count}/2{marker}</td>'
        '<td>100%</td></tr>'
    )
    for directory in (tmp_path, tmp_path / "en"):
        directory.mkdir(exist_ok=True)
        (directory / "index.html").write_text("".join(row(method) for method in participants))
    _check_core_comparison(tmp_path, {"methods": methods}, results, participants)
    (tmp_path / "en/index.html").write_text(
        row(participants[0], marker='<strong class="winner">Best</strong>')
        + "".join(row(method) for method in participants[1:])
    )
    with pytest.raises(ValueError, match="highlight"):
        _check_core_comparison(tmp_path, {"methods": methods}, results, participants)
    (tmp_path / "en/index.html").write_text(
        row(participants[0], count=1) + "".join(row(method) for method in participants[1:])
    )
    with pytest.raises(ValueError, match="core score"):
        _check_core_comparison(tmp_path, {"methods": methods}, results, participants)
    (tmp_path / "en/index.html").write_text(
        row(participants[0]).replace('</tr>',
                                    '<td data-metric="verifiable-pass">2/2</td></tr>')
        + "".join(row(method) for method in participants[1:])
    )
    with pytest.raises(ValueError, match="one core score"):
        _check_core_comparison(tmp_path, {"methods": methods}, results, participants)


def test_result_detail_gate_rejects_duplicate_summary_and_missing_failure_log() -> None:
    html = '<div class="verdict-line fail"></div><section id="diagnosis">Exact timeout</section><section id="evidence"></section>'
    evidence = {"programmatic_failure": {"stage": "browser_tool_execution", "message": "Exact timeout"},
                "trajectory": [{"index": 1, "result": "Exact timeout"}]}
    _check_result_detail_semantics(html, evidence, "fail")
    with pytest.raises(ValueError, match="duplicates or omits"):
        _check_result_detail_semantics(html + '<div class="verdict-line fail"></div>', evidence, "fail")
    with pytest.raises(ValueError, match="exact error"):
        _check_result_detail_semantics(html.replace("Exact timeout", "generic error"), evidence, "fail")
    with pytest.raises(ValueError, match="evaluation logs"):
        _check_result_detail_semantics(
            html, {"programmatic_failure": {"stage": "official_evaluation", "message": "Exact timeout"}}, "fail",
        )


def test_affected36_comparison_rejects_wrong_score_and_tied_highlight(tmp_path: Path) -> None:
    compare = [
        {"id": "old", "label": "Old", "passed": 36, "total": 36},
        {"id": "new", "label": "New", "passed": 36, "total": 36},
    ]
    results = [
        {"participant_id": method, "task_id": task_id, "status": "pass"}
        for method in ("old", "new") for task_id in range(36)
    ]
    row = lambda item, marker="": (f'<tr><td><a href="results.html?participant={item["id"]}&status=">{item["label"]}</a></td>'
                                    f'<td data-metric="affected36-pass">36/36{marker}</td></tr>')
    for directory, heading in ((tmp_path, "36 题"), (tmp_path / "en", "36 failures")):
        directory.mkdir(exist_ok=True)
        (directory / "index.html").write_text(heading + "".join(row(item) for item in compare))
    _check_affected36_comparison(tmp_path, compare, results, set(range(36)), ["old", "new"])
    (tmp_path / "en/index.html").write_text("36 failures" + row(compare[0], '<span class="winner">Best</span>') + row(compare[1]))
    with pytest.raises(ValueError, match="highlight"):
        _check_affected36_comparison(tmp_path, compare, results, set(range(36)), ["old", "new"])
    compare[1]["passed"] = 35
    with pytest.raises(ValueError, match="denominator or score"):
        _check_affected36_comparison(tmp_path, compare, results, set(range(36)), ["old", "new"])


def test_formal36_report_gate_rejects_missing_detail_wrong_score_and_visual(
    tmp_path: Path,
) -> None:
    rows = [
        {"task_id": task_id, "status": "pass", "official_score": 1.0,
         "completed_actions": 2, "action_count": 2,
         "evidence_sha256": {"evaluation": "a" * 64}}
        for task_id in range(1, 37)
    ]
    asset = tmp_path / "assets/diagnostic/formal-1-before.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"reviewed image")
    formal = {
        "status": "complete", "pass_count": 36, "denominator": 36,
        "reference_unchanged": True, "rows": rows,
        "visuals": {"1-before": {"asset": "assets/diagnostic/formal-1-before.png",
                                 "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}},
    }
    data = tmp_path / "data"
    data.mkdir()
    (data / "diagnostic-closure.json").write_text(json.dumps({"formal36": formal}))
    for lang in ("zh", "en"):
        parent = tmp_path if lang == "zh" else tmp_path / "en"
        (parent / "diagnostic").mkdir(parents=True)
        (parent / "diagnostics.html").write_text("36/36；历史 90 题" if lang == "zh" else "36/36; historical 90-task")
        for row in rows:
            label = "新版 Moli 受影响题目复测：通过" if lang == "zh" else "New Moli affected-task cohort: passed"
            (parent / "diagnostic" / f'{row["task_id"]}.html').write_text(
                f'{label} 2/2 {"a" * 64}'
            )
    _check_formal36_output_scope(tmp_path)
    rows[0]["reference_validity"] = {"status": "unverifiable"}
    (data / "diagnostic-closure.json").write_text(json.dumps({"formal36": formal}))
    with pytest.raises(ValueError, match="unverifiable reference"):
        _check_formal36_output_scope(tmp_path)
    (tmp_path / "diagnostics.html").write_text("36/36；历史 90 题；不能宣称 36 题均已真实解决")
    (tmp_path / "en/diagnostics.html").write_text("36/36; historical 90-task; not a claim that all 36 tasks were truly solved")
    with pytest.raises(ValueError, match="detail is missing"):
        _check_formal36_output_scope(tmp_path)
    rows[0].pop("reference_validity")
    (data / "diagnostic-closure.json").write_text(json.dumps({"formal36": formal}))
    rows[0]["official_score"] = 0.0
    (data / "diagnostic-closure.json").write_text(json.dumps({"formal36": formal}))
    with pytest.raises(ValueError, match="incomplete"):
        _check_formal36_output_scope(tmp_path)
    rows[0]["official_score"] = 1.0
    (data / "diagnostic-closure.json").write_text(json.dumps({"formal36": formal}))
    (tmp_path / "diagnostic/1.html").write_text("missing")
    with pytest.raises(ValueError, match="detail is missing"):
        _check_formal36_output_scope(tmp_path)
    (tmp_path / "diagnostic/1.html").write_text(f'新版 Moli 受影响题目复测：通过 2/2 {"a" * 64}')
    asset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="visual is missing or changed"):
        _check_formal36_output_scope(tmp_path)


def test_fixed36_diagnostic_summary_separates_actual_and_synthetic_answers(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "diagnostic-closure.json").write_text(json.dumps({
        "fixed36": {
            "official_rule_pass_tasks": 36,
            "source_derived_official_pass_tasks": 1,
        }
    }))
    (tmp_path / "diagnostics.html").write_text(
        "<p>35/36 条轨迹实际输出通过官方规则；另有 1 条用另行生成的现场数据回答</p>"
    )
    english = tmp_path / "en/diagnostics.html"
    english.parent.mkdir()
    english.write_text(
        "<p>35/36 actual trajectory outputs passed the official rule; "
        "1 separate source-derived answer</p>"
    )
    _check_fixed36_output_scope(tmp_path)
    (tmp_path / "diagnostics.html").write_text(
        "<p>36/36 条通过官方规则；其中 1 条用另行生成的现场数据回答</p>"
    )
    with pytest.raises(ValueError, match="blur actual and source-derived"):
        _check_fixed36_output_scope(tmp_path)


def _method(name: str, pass_rate: float, base: float) -> dict:
    metric = {"n": 90, "mean": base, "p50": base + 1, "p95": base + 2}
    return {
        "id": name,
        "label": name,
        "passed": int(pass_rate * 90),
        "valid_reference_passed": int(pass_rate * 88),
        "valid_reference_total": 88,
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
    assert rendered.count(">Best</span>") == 6
    assert "End-to-end time, all tasks" in rendered
    assert "n=90" in rendered
    assert "median" not in rendered.lower()


def test_legacy_retrieval_provenance_binds_every_browser_to_saved_answer(tmp_path) -> None:
    answer = {"task_type": "RETRIEVE", "status": "SUCCESS", "retrieved_data": ["value"]}
    seed = {"tasks": [{"task_id": 243, "canonical_candidate_id": "candidate",
                       "candidates": [{"id": "candidate", "recorded_response": answer}]}]}
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    results = {
        "source_seed_manifest": str(seed_path),
        "source_seed_manifest_sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        "engines": ["chromium", "moli"],
        "attempts": [
            {"task_id": 243, "engine": browser,
             "evaluation_input_sha256": _json_sha256(answer)}
            for browser in ("chromium", "moli")
        ],
    }
    dataset = {"trajectories": [
        {"task_id": 243, "evaluation_input": {"task_type": "RETRIEVE"}}
    ]}
    audit = _legacy_retrieval_provenance(results, dataset)
    assert audit["retrieval_tasks"] == 1
    assert audit["retrieval_results"] == audit["frozen_answer_matches"] == 2
    assert {row["browser"] for row in audit["results"]} == {"chromium", "moli"}

    results["attempts"][1]["evaluation_input_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs from frozen Agent answer"):
        _legacy_retrieval_provenance(results, dataset)
    results["attempts"][1]["evaluation_input_sha256"] = _json_sha256(answer)
    results["attempts"].pop()
    with pytest.raises(ValueError, match="full browser-task matrix"):
        _legacy_retrieval_provenance(results, dataset)


def test_legacy_retrieval_provenance_rejects_changed_seed(tmp_path) -> None:
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text('{"tasks": []}', encoding="utf-8")
    results = {"source_seed_manifest": str(seed_path),
               "source_seed_manifest_sha256": "0" * 64, "engines": [], "attempts": []}
    with pytest.raises(ValueError, match="hash mismatch"):
        _legacy_retrieval_provenance(results, {"trajectories": []})


def test_legacy_retrieval_release_gate_rejects_missing_or_false_audit(tmp_path) -> None:
    zh = '<section class="callout historical-validity"></section>' \
         '<a href="data/legacy-retrieval-provenance.json">audit</a>'
    en = '<a href="../data/legacy-retrieval-provenance.json">audit</a>'
    with pytest.raises(ValueError, match="missing"):
        _check_legacy_retrieval_audit(tmp_path, zh, en)
    target = tmp_path / "data/legacy-retrieval-provenance.json"
    target.parent.mkdir()
    audit = {
        "schema": "trajectory_replay_legacy_retrieval_provenance/1",
        "retrieval_tasks": 37, "retrieval_results": 74, "frozen_answer_matches": 74,
        "results": [
            {"task_id": task_id, "browser": browser,
             "source": "frozen_agent_response_not_current_page_observation"}
            for task_id in range(1, 38)
            for browser in ("chromium", "moli")
        ],
    }
    target.write_text(json.dumps(audit), encoding="utf-8")
    _check_legacy_retrieval_audit(tmp_path, zh, en)
    audit["frozen_answer_matches"] = 73
    target.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        _check_legacy_retrieval_audit(tmp_path, zh, en)
    audit["frozen_answer_matches"] = 74
    target.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="not linked"):
        _check_legacy_retrieval_audit(tmp_path, zh, "<p>no audit link</p>")


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
        {
            "engine": "moli",
            "participant_id": "moli-fixed",
            "browser": "Moli fixed",
            "task_id": 29,
            "status": "pass",
            "completed_actions": 2,
            "action_count": 2,
            "detail_url": "diagnostic/29.html",
        },
    ]
    task_html = _task_page("zh", task, peers)
    assert 'href="../result/moli-29.html#diagnosis"' in task_html
    assert 'href="../result/chromium-29.html#trajectory"' in task_html
    assert 'href="../diagnostic/29.html#trajectory"' in task_html
    assert 'href="../en/task/29.html"' in task_html
    assert "评测集基准轨迹" in task_html
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
        "planned_actions": task["tool_calls"],
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
            "remediation_owner_zh": "浏览器实现",
            "remediation_owner_en": "Browser implementation",
            "remediation_status_zh": "保留本轮结果",
            "remediation_status_en": "Results retained",
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
    assert 'data-standard-url="../data/trajectory/moli-29.json"' in detail
    assert 'data-evidence-url="../data/evidence/moli-29.json"' in detail
    assert "Call log: #latest" not in detail  # Full logs load only when a step is opened.
    assert "本方法轨迹与实际执行" in detail
    assert 'href="../diagnostic/29.html"' in detail
    assert "目标没有出现" in detail
    assert "浏览器实现" in detail and "保留本轮结果" in detail
    english = _detail_page("en", attempt, task, {"label": "Moli"}, evidence, peers)
    assert "Browser implementation" in english and "Results retained" in english
    assert "浏览器实现" not in english
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
        "planned_actions": task["tool_calls"],
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


def test_report_rejects_symlinked_evidence_parent(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    ref = {
        "path": str(linked_parent / evidence.name),
        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="missing or changed"):
        _load_artifact(ref)
    with pytest.raises(ValueError, match="missing or changed"):
        _copy_failure_screenshot({"evidence": {"failure_screenshot": ref}}, tmp_path, "fixture")


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
        "planned_actions": [],
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
    review = json.loads(Path("research/trajectory-replay-reviews/20260917-original-36.json").read_text())
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
        "planned_actions": [{"action": "snapshot"}] * 4,
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


def test_site_reset_and_readiness_are_available_in_public_result_evidence(
    tmp_path: Path,
) -> None:
    refs = {}
    for name, value in (
        ("site_reset", {"task_id": 29, "sites": {"reddit": {"old_pod": "old", "new_pod": "new"}}}),
        ("readiness", {"site": "reddit", "stable_http_status": 200}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        refs[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    attempt = {
        "engine": "chromium",
        "participant_id": "chromium",
        "task_id": 29,
        "status": "pass",
        "process_status": "pass",
        "evaluator_status": "pass",
        "action_count": 1,
        "completed_action_count": 1,
        "duration_ms": 100,
        "evidence": refs,
    }
    public = _public_evidence(attempt, None, tmp_path / "report")
    assert public["site_reset"]["sites"]["reddit"] == {
        "old_pod": "old",
        "new_pod": "new",
    }
    assert public["readiness"]["stable_http_status"] == 200
    stored = json.loads((tmp_path / "report/data/evidence/chromium-29.json").read_text())
    assert stored["site_reset"] == public["site_reset"]


def test_step_dialog_exposes_secondary_failure_state_without_replacing_error(tmp_path: Path) -> None:
    _assets(tmp_path)
    script = (tmp_path / "assets/app.js").read_text()
    assert "actual?.failure_ui_state||actual?.failure_ui_state_error" in script
    assert "observation_error:actual.failure_ui_state_error" in script
    assert "zh?'remediation_owner_zh':'remediation_owner_en'" in script
    assert "zh?'remediation_status_zh':'remediation_status_en'" in script
    assert "textContent+='\\n\\n'+label+'\\n'+JSON.stringify(observation,null,2)" in script
    assert "补充诊断，不替代上方原始错误" in script
    assert "supplementary diagnosis, not the original error above" in script


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
    attempt["planned_actions"] = [step["arguments"] for step in trace]
    task = {"task_id": 464, "task": {"intent": "Save product", "sites": ["shopping_admin"]},
            "tool_calls": attempt["planned_actions"]}
    zh = _detail_page("zh", attempt, task, {"label": "Moli"}, public, [])
    en = _detail_page("en", attempt, task, {"label": "Moli"}, public, [])
    assert "网络验收条件未满足" in zh and "要求匹配的网络条件" in zh
    assert "提交请求未出现" not in zh and "未出现的请求" not in zh
    assert "Network acceptance not satisfied" in en and "Required network match" in en
    assert "request absent" not in en and "Missing required request" not in en
