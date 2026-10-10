import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.remote_runtime import (
    freeze_original_failure_inputs,
    validate_candidate_revision,
)
from harness.trajectory_replay.retrieval_acceptance import validate_contract


def _fixture(tmp_path: Path) -> tuple[dict, Path, dict]:
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps([{"index": 1, "arguments": {"action": "snapshot"}, "status": "success"}]),
        encoding="utf-8",
    )
    trace_sha = hashlib.sha256(trace.read_bytes()).hexdigest()
    site_map = tmp_path / "sites.json"
    site_map.write_text(json.dumps({
        "schema": "lex_webarena_remote_sites/1",
        "sites": {
            "shopping": "https://shopping.example.test/",
            "shopping_admin": "https://admin.example.test/admin",
            "reddit": "https://reddit.example.test/",
            "gitlab": "https://gitlab.example.test/",
        },
    }), encoding="utf-8")
    task_ids = [*range(1, 36), 243]
    tasks = [{
        "task_id": task_id,
        "action_evidence": {"path": str(trace), "sha256": trace_sha},
        "qualified_action_count": 1,
        "evaluation_mode": "actual_trajectory_output",
        "semantic_verification": {"status": "verified"},
    } for task_id in task_ids]
    index = {
        "schema": "original36_latest_moli_diagnostic_index/7",
        "original_failed_task_ids": task_ids,
        "moli_binary_sha256": "a" * 64,
        "tasks": tasks,
    }
    roles = {"schema": "webarena_qualified_cohort_step_roles/1", "exceptions": [],
             "candidate_overrides": []}
    return index, site_map, roles


def test_freeze_includes_all_36_without_historical_answers(tmp_path: Path) -> None:
    index, site_map, roles = _fixture(tmp_path)

    cohort = freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)

    assert len(cohort["candidates"]) == 36
    assert cohort["quarantined"] == []
    assert cohort["candidate_task_ids"][-1] == 243
    assert cohort["input_contract"]["historical_scores_and_answers_included"] is False
    assert cohort["candidates"][0]["actions"] == [{"action": "snapshot"}]


def test_formal_freeze_keeps_the_same_declared_36_scope(tmp_path: Path) -> None:
    index, site_map, roles = _fixture(tmp_path)
    diagnostic = freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)
    formal = freeze_original_failure_inputs(
        index, site_map_path=site_map, step_roles=roles, formal=True,
    )

    assert formal["schema"] == "original36_affected_cohort_inputs/1"
    assert formal["promotable"] is True
    assert formal["candidates"] == diagnostic["candidates"]
    assert formal["quarantined"] == diagnostic["quarantined"]


def test_old_task243_synthetic_email_join_cannot_be_promoted(tmp_path: Path) -> None:
    index, site_map, roles = _fixture(tmp_path)
    index["tasks"][-1]["reference_validity"] = {"status": "unverifiable"}
    index["tasks"][-1]["evaluation_mode"] = "source_derived_synthetic"
    with pytest.raises(ValueError, match="unverifiable reference"):
        freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles, formal=True)


def test_freeze_binds_site_readiness_paths_to_the_active_site_map(tmp_path: Path) -> None:
    index, site_map, roles = _fixture(tmp_path)
    roles["site_readiness"] = [{
        "site": "reddit", "path": "/f/DIY/post", "max_stable_ms": 5000,
    }]

    cohort = freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)

    assert cohort["site_readiness"] == [{
        "site": "reddit",
        "path": "/f/DIY/post",
        "url": "https://reddit.example.test/f/DIY/post",
        "max_stable_ms": 5000,
    }]
    roles["site_readiness"].append({
        "site": "reddit", "path": "/f/DIY/post", "max_stable_ms": 5000,
    })
    with pytest.raises(ValueError, match="duplicate site readiness"):
        freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)


def test_freeze_fails_on_changed_trace_or_unclassified_step(tmp_path: Path) -> None:
    index, site_map, roles = _fixture(tmp_path)
    index["tasks"][0]["action_evidence"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="missing or changed"):
        freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)

    index, site_map, roles = _fixture(tmp_path)
    trace_path = Path(index["tasks"][0]["action_evidence"]["path"])
    trace_path.write_text(json.dumps([
        {"index": 1, "arguments": {"action": "snapshot"}, "status": "success"},
        {"index": 2, "arguments": {"action": "evaluate", "script": "1"}, "status": "success"},
    ]), encoding="utf-8")
    changed_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    for task in index["tasks"][:-1]:
        task["action_evidence"]["sha256"] = changed_sha
    with pytest.raises(ValueError, match="unclassified supplemental step"):
        freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)


def test_freeze_excludes_a_trailing_diagnostic_observer_from_actions(tmp_path: Path) -> None:
    index, site_map, roles = _fixture(tmp_path)
    trace_path = tmp_path / "trace-with-observer.json"
    trace_path.write_text(json.dumps([
        {"index": 1, "arguments": {"action": "click", "selector": "button"}, "status": "success"},
        {
            "index": 2,
            "arguments": {"action": "evaluate", "script": "document.body.textContent"},
            "status": "success",
        },
    ]), encoding="utf-8")
    index["tasks"][0]["action_evidence"] = {
        "path": str(trace_path),
        "sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
    }
    index["tasks"][0]["qualified_action_count"] = 2
    roles["exceptions"].append({
        "task_id": 1,
        "trace_index": 2,
        "role": "diagnostic_post_observer",
    })

    cohort = freeze_original_failure_inputs(index, site_map_path=site_map, step_roles=roles)

    first = cohort["candidates"][0]
    assert first["actions"] == [{"action": "click", "selector": "button"}]
    assert first["omitted_diagnostic_observers"] == [{
        "trace_index": 2,
        "historical_status": "success",
        "excluded_from_replay": True,
    }]


def test_task163_review_endpoint_is_bound_to_active_origin() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-163.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    script = candidate["tool_calls"][-1]["script"]
    assert "configured.origin !== initial.origin" in script
    assert "siteHosts" not in script
    assert "initial.port !== '7770'" not in script
    validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])


def test_task243_uses_review_nickname_without_customer_join() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-243.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
    assert len(candidate["tool_calls"]) == 9
    for index in (3,):
        selector = candidate["tool_calls"][index]["selector"]
        assert selector.startswith('a[href$="/admin/')
        assert "http:" not in selector and "https:" not in selector
    script = candidate["tool_calls"][-1]["script"]
    assert "lowest_rating_not_unique" in script
    assert "nickname" in script
    assert "customer/index" not in json.dumps(candidate["tool_calls"])
    assert "hannah.lim@gmail.com" not in script


def test_task404_waits_for_page_load_before_vote_dispatch() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/trajectory-candidates/task-404.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    validate_candidate_revision(candidate)
    assert candidate["tool_calls"][7]["selector"] == 'a[href="/f/books/new"]:visible'
    assert candidate["tool_calls"][8] == {
        "action": "wait",
        "selector": 'button[aria-label="Sort by: New"]:visible',
        "timeout": 30000,
    }
    readiness = candidate["tool_calls"][9]
    assert readiness["action"] == "evaluate"
    assert "document.readyState !== 'complete'" in readiness["script"]
    assert "target_vote_controller_missing" in readiness["script"]
    assert "fetch(" not in readiness["script"]


def test_task108_commits_dates_and_checks_applied_grid_before_enumerating() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/"
        "task-108-stabilized.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
    commit = candidate["tool_calls"][11]
    readiness = candidate["tool_calls"][14]
    assert commit["action"] == readiness["action"] == "evaluate"
    assert "new Event('change'" in commit["script"]
    assert "input.blur()" in commit["script"]
    assert "applied_order_grid_filters_not_ready" in readiness["script"]
    assert "filters[created_at][from]" in readiness["script"]
    assert "filters[created_at][to]" in readiness["script"]
    assert "fetch(" not in readiness["script"]


def test_task464_awaits_visible_save_completion() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/trajectory-candidates/task-464.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    validate_candidate_revision(candidate)
    assert candidate["tool_calls"][5]["action"] == "click"
    assert candidate["tool_calls"][6] == {
        "action": "wait", "selector": "div.message-success:visible", "timeout": 30000,
    }


@pytest.mark.parametrize("task_id,post_id,direction", [
    (404, 124260, "up"),
    (714, 19459, "down"),
])
def test_vote_trajectory_revisions_bind_the_post_not_button_position(
    task_id: int, post_id: int, direction: str,
) -> None:
    path = Path(__file__).resolve().parents[2] / (
        f"evaluation/trajectory-replay/webarena-verified/trajectory-candidates/task-{task_id}.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    validate_candidate_revision(candidate)
    vote = candidate["tool_calls"][-2]
    assert vote["action"] == "click"
    assert f"/{post_id}/" in vote["selector"]
    assert f'img[alt="{direction}"]' in vote["selector"]
    assert ":nth-match(button" not in vote["selector"]
    assert candidate["tool_calls"][-1] == {"action": "wait", "seconds": 2}

    candidate["tool_calls"][-1]["seconds"] = 1
    with pytest.raises(ValueError, match="trajectory revision is unbound"):
        validate_candidate_revision(candidate)
    candidate["tool_calls"][-1]["seconds"] = 2
    candidate["unexpected"] = True
    with pytest.raises(ValueError, match="trajectory revision is unbound"):
        validate_candidate_revision(candidate)


@pytest.mark.parametrize("repair", [None, [], {}, {"cause": "", "change": "wait"},
                                    {"cause": "race", "change": 1}])
def test_trajectory_revision_rejects_malformed_repair_metadata(repair: object) -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/trajectory-candidates/task-404.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    candidate["repair"] = repair
    with pytest.raises(ValueError, match="trajectory revision is unbound"):
        validate_candidate_revision(candidate)


def test_product_save_revision_uses_the_visible_form_control() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "evaluation/trajectory-replay/webarena-verified/trajectory-candidates/task-464.json"
    )
    candidate = json.loads(path.read_text(encoding="utf-8"))
    validate_candidate_revision(candidate)
    assert len(candidate["tool_calls"]) == 7
    prepare, submit = candidate["tool_calls"][4:6]
    assert prepare["action"] == "evaluate"
    assert "source.set('data.product.short_description',value)" in prepare["script"]
    assert "source.client._save" not in prepare["script"]
    assert "setTimeout" not in prepare["script"]
    assert submit == {
        "action": "click",
        "selector": 'button:has-text("Save"):not(:has-text("Save options")):visible',
        "replay_mode": "snapshot_ref_semantics",
    }


def _lineage_fixture(tmp_path):
    import copy

    from harness.trajectory_replay.remote_runtime import input_digest

    index, site_map, roles = _fixture(tmp_path)
    parent = {
        "schema": "webarena_qualified_trajectory_revision/1", "task_id": 1,
        "canonical_calls_sha256": "", "tool_calls": [
            {"action": "press", "key": "Tab"}, {"action": "snapshot"},
        ],
    }
    parent["canonical_calls_sha256"] = input_digest(parent["tool_calls"])
    parent_sha = hashlib.sha256(
        (json.dumps(parent, ensure_ascii=False, indent=2) + "\n").encode()
    ).hexdigest()
    candidate = copy.deepcopy(parent)
    candidate["tool_calls"][0] = {"action": "evaluate", "script": "document.title"}
    candidate["canonical_calls_sha256"] = input_digest(candidate["tool_calls"])
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    override = {
        "task_id": 1, "path": "candidate.json",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "reason": "Bound stabilization", "action_count": 2,
        "stabilization_parent_sha256": parent_sha,
        "stabilization_reversions": [{
            "action_index": 1, "current_action": "evaluate",
            "parent_call": {"action": "press", "key": "Tab"},
        }],
        "paired_qualification": {
            "chromium_receipt_sha256": "b" * 64, "moli_receipt_sha256": "c" * 64,
        },
    }
    roles["candidate_overrides"] = [override]
    matrix_path = tmp_path / (
        "evaluation/trajectory-replay/webarena-verified/"
        "retrieval-candidates/qualification-matrix.json"
    )
    matrix_path.parent.mkdir(parents=True)
    matrix_path.write_text(json.dumps({"rows": [{
        "task_id": 1, "affected_task_qualification": {
            "candidate_sha256": parent_sha, "action_count_per_browser": 2,
            "retrieval_status": "supported", "chromium_official_score": 1.0,
            "moli_official_score": 1.0, **override["paired_qualification"],
        },
    }]}), encoding="utf-8")
    return index, site_map, roles, candidate


def test_declared_parent_proof_works_for_any_task_and_reference_root(tmp_path, monkeypatch):
    index, site_map, roles, _ = _lineage_fixture(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    result = freeze_original_failure_inputs(
        index, site_map_path=site_map, step_roles=roles, reference_root=tmp_path,
    )
    assert len(result["candidates"]) == 36
    assert result["candidates"][0]["actions"][0] == {
        "action": "evaluate", "script": "document.title",
    }
    assert result["candidates"][0]["source_revision"]["action_count"] == 2


def test_undeclared_candidate_change_cannot_borrow_parent_qualification(tmp_path):
    from harness.trajectory_replay.remote_runtime import input_digest

    index, site_map, roles, candidate = _lineage_fixture(tmp_path)
    candidate["tool_calls"][1] = {"action": "press", "key": "Enter"}
    candidate["canonical_calls_sha256"] = input_digest(candidate["tool_calls"])
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    roles["candidate_overrides"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="differs from the qualified parent"):
        freeze_original_failure_inputs(
            index, site_map_path=site_map, step_roles=roles, reference_root=tmp_path,
        )


@pytest.mark.parametrize("mutation", ["missing", "bool", "duplicate", "wrong_action", "hash"])
def test_invalid_parent_proof_is_rejected(tmp_path, mutation):
    from harness.trajectory_replay.remote_runtime import validate_candidate_lineage

    _, _, roles, candidate = _lineage_fixture(tmp_path)
    override = roles["candidate_overrides"][0]
    if mutation == "missing":
        del override["stabilization_reversions"]
    elif mutation == "bool":
        override["stabilization_reversions"][0]["action_index"] = True
    elif mutation == "duplicate":
        override["stabilization_reversions"] *= 2
    elif mutation == "wrong_action":
        override["stabilization_reversions"][0]["current_action"] = "click"
    else:
        override["stabilization_parent_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="lineage"):
        validate_candidate_lineage(candidate, override)


def test_current_stabilization_retains_exact_qualified_parent():
    from harness.trajectory_replay.remote_runtime import validate_candidate_lineage

    root = Path(__file__).resolve().parents[2]
    roles = json.loads((root / (
        "evaluation/trajectory-replay/webarena-verified/cohort-step-roles.json"
    )).read_text())
    override = next(row for row in roles["candidate_overrides"] if row["task_id"] == 108)
    path = root / override["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == override["sha256"]
    assert validate_candidate_lineage(json.loads(path.read_text()), override) == (
        override["stabilization_parent_sha256"]
    )


@pytest.mark.parametrize("linked", [False, True])
def test_candidate_reference_cannot_escape_declared_root(tmp_path, linked):
    index, site_map, roles, _ = _lineage_fixture(tmp_path)
    if linked:
        (tmp_path / "alias.json").symlink_to(tmp_path / "candidate.json")
        roles["candidate_overrides"][0]["path"] = "alias.json"
    else:
        roles["candidate_overrides"][0]["path"] = "../candidate.json"
    with pytest.raises(ValueError, match="relative|Symlink"):
        freeze_original_failure_inputs(
            index, site_map_path=site_map, step_roles=roles, reference_root=tmp_path,
        )


def test_freeze_command_preserves_umask_and_existing_output(tmp_path):
    import os

    from harness.trajectory_replay.remote_runtime import main_freeze_original_failures

    index, site_map, roles = _fixture(tmp_path)
    index_path, roles_path = tmp_path / "index.json", tmp_path / "roles.json"
    index_path.write_text(json.dumps(index))
    roles_path.write_text(json.dumps(roles))
    output = tmp_path / "inputs.json"
    args = ["--index", str(index_path), "--roles", str(roles_path),
            "--site-map", str(site_map), "--output", str(output)]
    previous = os.umask(0o022)
    try:
        main_freeze_original_failures(args)
        observed = os.umask(0o022)
        assert observed == 0o022
        assert output.stat().st_mode & 0o777 == 0o600
        original = output.read_bytes()
        with pytest.raises(ValueError, match="already exists"):
            main_freeze_original_failures(args)
        assert output.read_bytes() == original
    finally:
        os.umask(previous)


def test_scope_change_requires_both_named_qualification_receipts(tmp_path):
    index, site_map, roles, _ = _lineage_fixture(tmp_path)
    roles["candidate_overrides"][0]["paired_qualification"] = {}
    matrix_path = tmp_path / (
        "evaluation/trajectory-replay/webarena-verified/"
        "retrieval-candidates/qualification-matrix.json"
    )
    matrix = json.loads(matrix_path.read_text())
    qualification = matrix["rows"][0]["affected_task_qualification"]
    del qualification["chromium_receipt_sha256"]
    del qualification["moli_receipt_sha256"]
    matrix_path.write_text(json.dumps(matrix))
    with pytest.raises(ValueError, match="paired proof"):
        freeze_original_failure_inputs(
            index, site_map_path=site_map, step_roles=roles, reference_root=tmp_path,
        )


def test_freeze_command_rejects_input_drift_before_writing(tmp_path, monkeypatch):
    from harness.trajectory_replay import remote_runtime

    index, site_map, roles = _fixture(tmp_path)
    index_path, roles_path = tmp_path / "index.json", tmp_path / "roles.json"
    index_path.write_text(json.dumps(index))
    roles_path.write_text(json.dumps(roles))
    output = tmp_path / "inputs.json"
    original = remote_runtime.freeze_original_failure_inputs

    def changing_input(*args, **kwargs):
        result = original(*args, **kwargs)
        roles_path.write_text(roles_path.read_text() + "\n")
        return result

    monkeypatch.setattr(remote_runtime, "freeze_original_failure_inputs", changing_input)
    with pytest.raises(ValueError, match="changed during compilation"):
        remote_runtime.main_freeze_original_failures([
            "--index", str(index_path), "--roles", str(roles_path),
            "--site-map", str(site_map), "--output", str(output),
        ])
    assert not output.exists()
