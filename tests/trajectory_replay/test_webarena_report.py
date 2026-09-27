import hashlib
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from harness.browser_use.publication import sanitize
from harness.fix_log import load_fix_log
from harness.trajectory_replay.webarena_report import (
    LEGACY_REPLAY_VALIDITY_INPUTS,
    _assets,
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
    _shell,
    _successful_distribution_table,
    _task_page,
    _unique_winners,
    _validated_planned_actions,
    _verdict_label,
    build_webarena_replay_report,
    require_all_reference_validity,
)
from scripts.build_webarena_replay_report import _bound_dataset_path
from scripts.check_webarena_replay_report import (
    _check_affected36_comparison,
    _check_core_comparison,
    _check_english_localization,
    _check_formal_execution_scope,
    _check_legacy_retrieval_audit,
    _check_result_detail_semantics,
    _contains_local_path_outside_web_url,
    _semantic_page_inventory,
)
from scripts.check_webarena_replay_report import (
    check as check_report,
)


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


@pytest.mark.parametrize("extra_methods", [0, 2])
def test_complete_three_method_report_builds_one_unified_matrix(tmp_path: Path, monkeypatch, extra_methods) -> None:
    methods = [
        {"id": "chromium", "engine": "chromium", "label": "Chromium"},
        {"id": "moli-old", "engine": "moli", "label": "Moli 1.1.3"},
        {"id": "moli-fixed", "engine": "moli", "label": "Moli 1.1.7"},
    ]
    methods.extend({"id": f"moli-new-{i}", "engine": "moli", "label": f"Moli new {i}"}
                   for i in range(extra_methods))
    profile = {"methods": methods, "qualification": {"reference_engine": "chromium"},
               "measurement_notes": {"zh": "分批追加的耗时不用于推断源码提速。", "en": "Separate batches do not establish code-induced speedup."},
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
    assert manifest["logical_results"] == len(methods)
    assert manifest["routes"] == 20 + 2 * extra_methods
    assert "36/36" not in (output / "index.html").read_text()
    assert "fixes.html" not in (output / "index.html").read_text()
    assert "BrowserTool 与浏览器的组合" in (output / "index.html").read_text()
    assert "BrowserTool–browser combinations" in (output / "en/index.html").read_text()
    assert "参考答案均已核验" not in (output / "index.html").read_text()
    assert "with verified references" not in (output / "en/index.html").read_text()
    assert "独立复核范围见复核说明" in (output / "index.html").read_text()
    assert "independent review scope" in (output / "en/index.html").read_text()
    for lang, prefix in (("zh", ""), ("en", "en/")):
        for name in ("index.html", "environment.html"):
            assert profile["measurement_notes"][lang] in (output / (prefix + name)).read_text()
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
    assert check_report(output)["html_pages"] == 20 + 2 * extra_methods
    home = output / "index.html"
    original_home = home.read_text()
    home.write_text(original_home.replace(profile["measurement_notes"]["zh"], ""))
    with pytest.raises(ValueError, match="measurement context"):
        check_report(output)
    home.write_text(original_home)
    manifest = build_webarena_replay_report(
        results_path=files["results.json"], dataset_path=files["dataset.json"],
        review_path=files["review.json"], profile_path=files["profile.json"],
        output_dir=output,
    )
    assert manifest["languages"] == ["zh"]
    assert manifest["english_status"] == "not_generated"
    assert manifest["routes"] == 11 + extra_methods
    english_notice = (output / "en/index.html").read_text()
    assert '英文报告未生成' in english_notice
    assert 'new URL("en/",b.href)' in english_notice
    assert "location.origin+location.pathname" not in english_notice
    assert not (output / "en/results.html").exists()
    assert 'class="language" href="../en/index.html"' in (output / "result/chromium-29.html").read_text()
    assert check_report(output)["html_pages"] == 11 + extra_methods
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


@pytest.mark.parametrize("extra_methods", [0, 2])
def test_formal_reference_validity_gate_requires_all_selected_tasks_for_all_methods(extra_methods) -> None:
    methods = [{"id": name, "engine": "chromium" if name == "chromium" else "moli"}
               for name in ("chromium", "moli-old", "moli-fixed")]
    methods.extend({"id": f"moli-new-{i}", "engine": "moli"} for i in range(extra_methods))
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
    for method_index in range(1, len(methods)):
        attempts[method_index * 90]["reference_validity"]["status"] = "unverifiable"
    with pytest.raises(ValueError, match="unverifiable tasks: \\[0\\]"):
        require_all_reference_validity(profile, results, dataset)
    diagnostic = {"methods": methods}
    require_all_reference_validity(diagnostic, results, dataset)
    attempts.pop()
    with pytest.raises(ValueError, match="every method-task result"):
        require_all_reference_validity(profile, results, dataset)


def test_formal_release_scope_accepts_any_complete_declared_split_size() -> None:
    _check_formal_execution_scope(
        {
            "formal_reference_validity": True,
            "task_count": 812,
            "site_reset_between_tasks": False,
        }
    )
    with pytest.raises(ValueError, match="method-reset task cohort"):
        _check_formal_execution_scope(
            {
                "formal_reference_validity": True,
                "task_count": 812,
                "site_reset_between_tasks": True,
            }
        )


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
    _check_result_detail_semantics(
        html,
        {
            "programmatic_failure": {
                "stage": "browser_tool_execution",
                "step": None,
                "message": "Exact timeout",
            },
            "trajectory": [],
        },
        "fail",
    )
    with pytest.raises(ValueError, match="evaluation logs"):
        _check_result_detail_semantics(
            html,
            {
                "programmatic_failure": {
                    "stage": "browser_tool_execution",
                    "step": None,
                    "message": "Exact timeout",
                }
            },
            "fail",
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


@pytest.mark.parametrize("path_kind", ["absolute", "relative", "file_link", "parent_link"])
def test_legacy_seed_requires_direct_absolute_evidence(tmp_path, monkeypatch, path_kind):
    root = tmp_path.resolve()
    directory = root / "evidence"
    directory.mkdir()
    source = directory / "seed.json"
    source.write_text('{"tasks": []}')
    selected = source
    if path_kind == "relative":
        monkeypatch.chdir(root)
        selected = Path("evidence/seed.json")
    elif path_kind == "file_link":
        selected = root / "seed-link.json"
        selected.symlink_to(source)
    elif path_kind == "parent_link":
        (root / "linked").symlink_to(directory, target_is_directory=True)
        selected = root / "linked/seed.json"
    results = {"source_seed_manifest": str(selected),
               "source_seed_manifest_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "engines": [], "attempts": []}
    if path_kind == "absolute":
        assert _legacy_retrieval_provenance(results, {"trajectories": []})["retrieval_results"] == 0
    else:
        with pytest.raises(ValueError, match="absolute|symlink"):
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


def test_report_shell_preserves_private_preview_resource_base() -> None:
    for depth, route, css, app in (
        (0, "index.html", "assets/report.css", "assets/app.js"),
        (1, "result/moli-29.html", "../assets/report.css", "../assets/app.js"),
    ):
        rendered = _shell("zh", "results", "结果", "<p>证据</p>", depth=depth, current_route=route)
        page = BeautifulSoup(rendered, "html.parser")
        assert page.select_one('link[rel="stylesheet"]')["data-src"] == css
        assert page.select_one("script[data-report-app]")["data-src"] == app
        assert page.select_one('link[rel="stylesheet"]').get("href") is None
        assert page.select_one("script[data-report-app]").get("src") is None
        assert page.select_one("base") is None
        bootstraps = page.select("head script:not([src])")
        assert len(bootstraps) == 1
        bootstrap = bootstraps[0].get_text()
        assert "new URL(folders.join('/')+'/',base.href)" in bootstrap
        assert "css.href=css.dataset.src" in bootstrap
        assert "location.origin+'/api/preview/'" not in bootstrap
        assert "img.src=img.dataset.src" in page.select("body script")[-1].get_text()


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


def test_detail_page_preserves_task_timeout_with_unknown_active_action() -> None:
    task = {
        "task_id": 90,
        "task": {"intent": "Open the route", "sites": ["map"]},
        "tool_calls": [{"action": "navigate", "url": "https://example.test"}],
    }
    attempt = {
        "engine": "obscura",
        "participant_id": "obscura-0.2.3",
        "task_id": 90,
        "status": "fail",
        "completed_action_count": 0,
        "action_count": 1,
        "duration_ms": 590_000,
        "resource": {},
        "planned_actions": task["tool_calls"],
    }
    evidence = {
        "programmatic_failure": {
            "stage": "browser_tool_execution",
            "step": None,
            "trigger_step": None,
            "message": "browser stopped responding",
        },
        "review": {
            "label_zh": "浏览器运行时无响应",
            "label_en": "Browser runtime became unresponsive",
            "reason_zh": "活动动作边界未记录",
            "reason_en": "The active action boundary was not recorded",
            "remediation_owner": "Obscura",
            "remediation_status": "recorded",
        },
        "trajectory": [
            {
                "index": None,
                "action": "task_timeout",
                "arguments": {},
                "status": "fail",
                "duration_ms": 590_000,
                "error": "browser stopped responding",
            }
        ],
        "screenshot": None,
    }

    detail = _detail_page("zh", attempt, task, {"label": "Obscura 0.2.3"}, evidence, [])

    assert "活动动作未记录" in detail
    assert "查看已保存轨迹证据" in detail
    assert 'id="step-1"' in detail


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


def test_public_evidence_return_value_is_the_same_sanitized_value_written_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace.json"
    trace.write_text('[{"index": 1, "status": "fail"}]')
    attempt = {
        "participant_id": "browser",
        "engine": "browser",
        "task_id": 1,
        "status": "fail",
        "action_count": 1,
        "completed_action_count": 0,
        "duration_ms": 1,
        "error": "failure at /Users/example/private/run.py",
        "evidence": {
            "trace": {
                "path": str(trace),
                "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
            }
        },
    }
    monkeypatch.setattr(
        "harness.trajectory_replay.webarena_report.programmatic_failure",
        lambda _: {
            "stage": "browser_tool_execution",
            "step": 1,
            "message": "failure at /Users/example/private/run.py",
        },
    )

    public = _public_evidence(attempt, None, tmp_path)
    written = json.loads(
        (tmp_path / "data/evidence/browser-1.json").read_text(encoding="utf-8")
    )

    assert public == written
    assert public["programmatic_failure"]["message"] == "failure at [local path removed]"
    assert public["programmatic_failure"]["step"] == 1


def test_report_preserves_web_routes_that_resemble_linux_paths() -> None:
    actions = [
        {
            "action": "navigate",
            "url": "https://gitlab.example.test/root/project/-/issues",
        }
    ]
    attempt = {
        "participant_id": "moli",
        "task_id": 317,
        "action_count": 1,
        "planned_actions": actions,
        "planned_actions_sha256": _planned_actions_digest(actions),
    }

    assert _clean(actions) == actions
    assert _validated_planned_actions(attempt) == actions


def test_release_path_gate_ignores_web_routes_but_detects_machine_paths() -> None:
    assert not _contains_local_path_outside_web_url(
        "https://gitlab.example.test/root/project/-/issues"
    )
    assert _contains_local_path_outside_web_url(
        "opened /root/private/result.json from https://example.test/root/public"
    )


def test_report_does_not_redact_secret_like_substrings_inside_words() -> None:
    product = "https://shop.example.test/task-chairhome-office-chair.html"

    assert _clean(product) == product
    assert _clean("token=sk-secret_value_123456") == "token=[secret removed]"


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
