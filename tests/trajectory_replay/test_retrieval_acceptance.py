import copy
import hashlib
import json

import pytest

from harness.trajectory_replay.retrieval_acceptance import (
    PROTOCOL,
    PROTOCOL_V2,
    PROTOCOL_V3,
    build_actual_response,
    digest,
    load_contracts,
    pointer,
    project,
    require_current_retrieval,
    text_digest,
)
from harness.trajectory_replay.retrieval_acceptance import (
    verify_attempt as _verify_attempt,
)


def verify_attempt(attempt, contract, calls):
    return _verify_attempt(attempt, contract, calls, expected_data_checksum="test-data")


def fixture():
    calls = [
        {
            "action": "evaluate",
            "script": "return JSON.parse(document.querySelector('pre').textContent)",
        }
    ]
    contract = {
        "schema": PROTOCOL,
        "task_id": 1,
        "canonical_calls_sha256": digest(calls),
        "sources": [
            {"name": "read", "step": 1, "action_sha256": digest(calls[0]), "decode": "json"}
        ],
        "projection": {"array": [{"source": "read", "pointer": "/count"}]},
    }
    rows = [
        {
            "index": 1,
            "arguments": calls[0],
            "status": "success",
            "tool_status": "success",
            "result": '{"count": 7}',
        }
    ]
    return calls, contract, rows


def build(calls, contract, rows):
    return build_actual_response(
        contract=contract,
        task_id=1,
        calls=calls,
        records=rows,
        run_id="run-current",
        attempt_id="attempt-current",
    )


def reviewed_span_fixture(output="selected_spans"):
    calls = [
        {
            "action": "evaluate",
            "script": "return JSON.stringify(window.currentPosts)",
        }
    ]
    posts = [
        {"post_id": "post-a", "title": "x📚Café!", "body": "I recommend this book."},
        {"post_id": "post-b", "title": "Two books appear here", "body": "A and B."},
        {"post_id": "post-c", "title": "No recommendation", "body": "News only."},
    ]
    annotations = [
        {
            "identity_sha256": digest("post-a"),
            "semantic_text_sha256": text_digest(posts[0]["body"]),
            "output_text_sha256": text_digest(posts[0]["title"]),
            "recommendation_count": 1,
            "spans": [{"start": 1, "end": 6}],
        },
        {
            "identity_sha256": digest("post-b"),
            "semantic_text_sha256": text_digest(posts[1]["body"]),
            "output_text_sha256": text_digest(posts[1]["title"]),
            "recommendation_count": 2,
            "spans": [],
        },
        {
            "identity_sha256": digest("post-c"),
            "semantic_text_sha256": text_digest(posts[2]["body"]),
            "output_text_sha256": text_digest(posts[2]["title"]),
            "recommendation_count": 0,
            "spans": [],
        },
    ]
    contract = {
        "schema": PROTOCOL_V2,
        "task_id": 1,
        "canonical_calls_sha256": digest(calls),
        "sources": [
            {"name": "read", "step": 1, "action_sha256": digest(calls[0]), "decode": "json"}
        ],
        "semantic_reference": {
            "kind": "human_reviewed_span_annotation",
            "browser_semantic_inference": False,
        },
        "reviewed_span_projection": {
            "kind": "reviewed_ordered_spans/1",
            "source": "read",
            "collection_pointer": "/posts",
            "identity_pointer": "/post_id",
            "semantic_text_pointer": "/body",
            "output_text_pointer": "/title",
            "output": output,
            "span_unit": "unicode_code_points",
            "annotation": {
                "schema": "lex_replay_reviewed_span_annotation/1",
                "semantic": "exactly_one_book_recommendation",
                "items": annotations,
            },
        },
        "requirements": {
            "scope": {"source": "read", "pointer": "/scope"},
            "completeness": {"source": "read", "pointer": "/complete"},
        },
    }
    rows = [
        {
            "index": 1,
            "arguments": calls[0],
            "status": "success",
            "tool_status": "success",
            "result": json.dumps({"scope": True, "complete": True, "posts": posts}),
        }
    ]
    return calls, contract, rows


def test_reviewed_span_uses_current_unicode_text_and_discloses_human_reference():
    calls, contract, rows = reviewed_span_fixture()
    result = build(calls, contract, rows)
    assert result["schema"] == PROTOCOL_V2
    assert result["status"] == "supported"
    assert result["actual_response"]["retrieved_data"] == ["📚Café"]
    assert result["semantic_reference"] == {
        "kind": "human_reviewed_span_annotation",
        "browser_semantic_inference": False,
        "annotation_sha256": digest(contract["reviewed_span_projection"]["annotation"]),
    }


def test_reviewed_item_mode_returns_current_full_source_text():
    calls, contract, rows = reviewed_span_fixture("selected_item_text")
    result = build(calls, contract, rows)
    assert result["status"] == "supported"
    assert result["actual_response"]["retrieved_data"] == ["x📚Café!"]


@pytest.mark.parametrize(
    "mutation", ["output_content", "semantic_content", "order", "missing", "hash"]
)
def test_reviewed_span_rejects_incomplete_or_changed_observation(mutation):
    calls, contract, rows = reviewed_span_fixture()
    observed = json.loads(rows[0]["result"])
    if mutation == "output_content":
        observed["posts"][0]["title"] += " changed"
    elif mutation == "semantic_content":
        observed["posts"][0]["body"] += " changed"
    elif mutation == "order":
        observed["posts"][0], observed["posts"][1] = (
            observed["posts"][1],
            observed["posts"][0],
        )
    elif mutation == "missing":
        observed["posts"].pop()
    else:
        contract["reviewed_span_projection"]["annotation"]["items"][0]["output_text_sha256"] = (
            "0" * 64
        )
    rows[0]["result"] = json.dumps(observed)
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize("field", ["scope", "complete"])
def test_reviewed_span_requires_current_scope_and_completeness(field):
    calls, contract, rows = reviewed_span_fixture()
    observed = json.loads(rows[0]["result"])
    observed[field] = False
    rows[0]["result"] = json.dumps(observed)
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_reviewed_span_rejects_answer_constants_and_invalid_hashes():
    calls, contract, rows = reviewed_span_fixture()
    item = contract["reviewed_span_projection"]["annotation"]["items"][0]
    item["answer"] = "📚Café"
    assert build(calls, contract, rows)["status"] == "insufficient"
    del item["answer"]
    item["identity_sha256"] = "not-a-hash"
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize(
    "span", [{"start": -1, "end": 2}, {"start": 2, "end": 2}, {"start": 1, "end": 99}]
)
def test_reviewed_span_rejects_invalid_or_out_of_range_boundaries(span):
    calls, contract, rows = reviewed_span_fixture()
    contract["reviewed_span_projection"]["annotation"]["items"][0]["spans"] = [span]
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_reviewed_span_binds_run_action_and_full_contract_digest():
    calls, contract, rows = reviewed_span_fixture()
    original = build(calls, contract, rows)
    assert original["status"] == "supported"
    changed = copy.deepcopy(contract)
    changed["reviewed_span_projection"]["annotation"]["items"][1]["recommendation_count"] = 1
    changed["reviewed_span_projection"]["annotation"]["items"][1]["spans"] = [
        {"start": 0, "end": 3}
    ]
    assert digest(changed) != original["contract_sha256"]
    wrong_action = copy.deepcopy(rows)
    wrong_action[0]["arguments"] = {"action": "evaluate", "script": "different"}
    assert build(calls, contract, wrong_action)["status"] == "insufficient"
    assert (
        build_actual_response(
            contract=contract,
            task_id=1,
            calls=calls,
            records=rows,
            run_id="",
            attempt_id="attempt-current",
        )["status"]
        == "insufficient"
    )


@pytest.mark.parametrize("value", [0, False, 7, "seven"])
def test_current_observation_is_the_answer_not_frozen_expected(value):
    calls, contract, rows = fixture()
    rows[0]["result"] = json.dumps({"count": value})
    result = build(calls, contract, rows)
    assert result["status"] == "supported"
    assert result["actual_response"]["retrieved_data"] == [value]
    assert type(result["actual_response"]["retrieved_data"][0]) is type(value)


@pytest.mark.parametrize(
    "text",
    [
        '{"wrong":7}',
        '{"count":7,"count":8}',
        '{"count":NaN}',
        '{"count":1e999}',
        "7 trailing",
        "[Snapshot error: read failed]",
        "... [snapshot truncated]",
    ],
)
def test_missing_ambiguous_or_unstructured_observation_cannot_reuse_expected(text):
    calls, contract, rows = fixture()
    rows[0]["result"] = text
    result = build(calls, contract, rows)
    assert result["status"] == "insufficient"
    assert result["actual_response"] is None


def test_no_contract_is_unsupported_not_browser_failure():
    calls, _, rows = fixture()
    assert build(calls, None, rows)["status"] == "unsupported"


@pytest.mark.parametrize(
    "change",
    [
        {"status": "error"},
        {"observation_status": "error"},
        {"index": 2},
        {"arguments": {"action": "snapshot"}},
    ],
)
def test_observation_must_bind_to_successful_current_action(change):
    calls, contract, rows = fixture()
    rows[0].update(change)
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_absence_is_not_inferred_without_completeness_contract():
    calls, contract, rows = fixture()
    contract["projection"] = {"source": "read", "pointer": ""}
    for text in ["null", "[]", "{}", '""']:
        rows[0]["result"] = text
        assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize(
    "complete,scope,supported",
    [(True, True, True), (False, True, False), (True, False, False), (1, True, False)],
)
def test_not_found_requires_observed_complete_empty_collection_and_correct_scope(
    complete, scope, supported
):
    calls, contract, rows = fixture()
    contract["projection"] = {"source": "read", "pointer": "/items"}
    contract["requirements"] = {
        "completeness": {"source": "read", "pointer": "/complete"},
        "scope": {"source": "read", "pointer": "/scope"},
    }
    contract["empty_result"] = "NOT_FOUND_ERROR"
    rows[0]["result"] = json.dumps({"items": [], "complete": complete, "scope": scope})
    result = build(calls, contract, rows)
    assert (result["status"] == "supported") is supported
    if supported:
        assert result["actual_response"]["status"] == "NOT_FOUND_ERROR"
        assert result["actual_response"]["retrieved_data"] is None


def test_absence_cannot_reuse_one_boolean_as_both_scope_and_completeness():
    calls, contract, rows = fixture()
    contract.update(
        projection={"source": "read", "pointer": "/items"},
        empty_result="NOT_FOUND_ERROR",
        requirements={
            "scope": {"source": "read", "pointer": "/ok"},
            "completeness": {"source": "read", "pointer": "/ok"},
        },
    )
    rows[0]["result"] = '{"items":[],"ok":true}'
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_observation_alias_cannot_disguise_shared_scope_completeness_boolean():
    calls, contract, rows = fixture()
    contract["sources"].append({**contract["sources"][0], "name": "alias"})
    contract.update(
        projection={"source": "read", "pointer": "/items"},
        empty_result="NOT_FOUND_ERROR",
        requirements={
            "scope": {"source": "read", "pointer": "/ok"},
            "completeness": {"source": "alias", "pointer": "/ok"},
        },
    )
    rows[0]["result"] = '{"items":[],"ok":true}'
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_complete_nonempty_collection_is_success_not_not_found():
    calls, contract, rows = fixture()
    contract["projection"] = {"source": "read", "pointer": "/items"}
    contract["requirements"] = {
        "completeness": {"source": "read", "pointer": "/complete"},
        "scope": {"source": "read", "pointer": "/scope"},
    }
    contract["empty_result"] = "NOT_FOUND_ERROR"
    rows[0]["result"] = json.dumps({"items": ["observed-sku"], "complete": True, "scope": True})
    answer = build(calls, contract, rows)["actual_response"]
    assert answer["status"] == "SUCCESS"
    assert answer["retrieved_data"] == ["observed-sku"]


def test_projection_preserves_row_relationships_and_has_no_literal_answers():
    expression = {
        "map": {
            "input": {"source": "read", "pointer": ""},
            "projection": {
                "object": {
                    "name": {"source": "$item", "pointer": "/name"},
                    "count": {"source": "$item", "pointer": "/count"},
                }
            },
        }
    }
    rows = [{"name": "A", "count": 2}, {"name": "B", "count": 7}]
    assert project(expression, {"read": rows}) == rows
    with pytest.raises(ValueError, match="constants"):
        project({"constant": [7]}, {})


def test_map_cannot_copy_first_global_row_over_every_item():
    expression = {
        "map": {
            "input": {"source": "read", "pointer": ""},
            "projection": {"source": "read", "pointer": "/0/count"},
        }
    }
    with pytest.raises(ValueError, match="unknown projection source"):
        project(expression, {"read": [{"count": 2}, {"count": 7}]})


@pytest.mark.parametrize("missing", [None, [], {}, "", [None], {"nested": []}])
def test_wrapping_a_missing_value_does_not_create_a_successful_answer(missing):
    calls, contract, rows = fixture()
    rows[0]["result"] = json.dumps({"count": missing})
    result = build(calls, contract, rows)
    assert result["status"] == "insufficient"
    assert result["actual_response"] is None


def test_promotion_requires_all_retrieval_contracts_not_common_pass_subset():
    with pytest.raises(ValueError, match=r"\[2\]"):
        require_current_retrieval({"retrieval_task_ids": [1, 2], "retrieval_contracts": {1: {}}})


def test_unsupported_contract_does_not_retry_the_browser():
    from harness.trajectory_replay.webarena_replay import _attempt_needs_retry

    assert not _attempt_needs_retry({"status": "unsupported"})


def test_pointer_rejects_invalid_escape_and_preserves_escaped_keys():
    assert pointer({"a/b": {"~": 0}}, "/a~1b/~0") == 0
    with pytest.raises(ValueError, match="escape"):
        pointer({"x~": 7}, "/x~")


def test_saved_answer_is_recomputed_and_cross_run_replay_rejected(tmp_path, monkeypatch):
    from harness.trajectory_replay import webarena_dataset

    monkeypatch.setattr(
        webarena_dataset, "_official_evaluator_names", lambda: {1: ("AgentResponseEvaluator",)}
    )
    monkeypatch.setattr(webarena_dataset, "_effective_correction_bindings", lambda: {})
    calls, contract, rows = fixture()
    binding = build(calls, contract, rows)
    attempt = {
        "task_id": 1,
        "retrieval_protocol": PROTOCOL,
        "run_id": "run-current",
        "attempt_id": "attempt-current",
        "evaluation_input_sha256": binding["actual_response_sha256"],
        "evidence": {},
        "status": "pass",
        "process_status": "pass",
        "evaluator_status": "pass",
    }
    evaluation = {
        "webarena_verified_version": webarena_dataset.version("webarena-verified"),
        "webarena_verified_evaluator_checksum": webarena_dataset.compute_evaluator_checksum(),
        "webarena_verified_data_checksum": "test-data",
        "task_id": 1,
        "score": 1,
        "status": "success",
        "evaluators_results": [
            {
                "evaluator_name": "AgentResponseEvaluator",
                "actual": binding["actual_response"],
                "score": 1,
                "status": "success",
            }
        ],
    }
    for name, value in [
        ("trace", rows),
        ("retrieval_acceptance", binding),
        ("actual_response", binding["actual_response"]),
        ("evaluation", evaluation),
    ]:
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        attempt["evidence"][name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    verify_attempt(attempt, contract, calls)
    original_trace_path = attempt["evidence"]["trace"]["path"]
    for unsafe_path in ("trace.json", "~/trace.json"):
        attempt["evidence"]["trace"]["path"] = unsafe_path
        with pytest.raises(ValueError, match="unsafe current-run retrieval evidence"):
            verify_attempt(attempt, contract, calls)
    attempt["evidence"]["trace"]["path"] = original_trace_path
    for field in (
        "webarena_verified_version",
        "webarena_verified_evaluator_checksum",
        "webarena_verified_data_checksum",
    ):
        original = evaluation[field]
        evaluation[field] = "stale"
        path = tmp_path / "evaluation.json"
        path.write_text(json.dumps(evaluation))
        attempt["evidence"]["evaluation"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="provenance"):
            verify_attempt(attempt, contract, calls)
        evaluation[field] = original
    # The same observed answer can legitimately fail the frozen expectation.
    evaluation["score"] = 0
    evaluation["status"] = "fail"
    evaluation["evaluators_results"][0].update(score=0, status="fail")
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(evaluation))
    attempt["evidence"]["evaluation"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="contradicts"):
        verify_attempt(attempt, contract, calls)
    attempt.update(status="fail", evaluator_status="fail")
    verify_attempt(attempt, contract, calls)
    evaluation["task_id"] = 999
    path.write_text(json.dumps(evaluation))
    attempt["evidence"]["evaluation"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="task differs"):
        verify_attempt(attempt, contract, calls)
    evaluation["task_id"] = 1
    path.write_text(json.dumps(evaluation))
    attempt["evidence"]["evaluation"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    wrong_run = copy.deepcopy(attempt)
    wrong_run["run_id"] = "other-run"
    with pytest.raises(ValueError, match="reproduced"):
        verify_attempt(wrong_run, contract, calls)
    attempt["evaluation_input_sha256"] = digest({"retrieved_data": [999]})
    with pytest.raises(ValueError, match="did not consume"):
        verify_attempt(attempt, contract, calls)


def test_evaluation_must_itself_bind_the_declared_task_correction(monkeypatch):
    from harness.trajectory_replay import webarena_dataset as dataset

    correction = ("correction", "source-hash", "task-hash", ("AgentResponseEvaluator",))
    monkeypatch.setattr(dataset, "_effective_correction_bindings", lambda: {1: correction})
    monkeypatch.setattr(dataset, "_official_evaluator_names", lambda: {1: correction[3]})
    attempt = {
        "task_id": 1,
        "contract_correction_id": correction[0],
        "contract_correction_source_sha256": correction[1],
        "corrected_task_sha256": correction[2],
    }
    evaluation = {
        "task_id": 1,
        "webarena_verified_version": dataset.version("webarena-verified"),
        "webarena_verified_evaluator_checksum": dataset.compute_evaluator_checksum(),
        "webarena_verified_data_checksum": "data",
        "evaluators_results": [{"evaluator_name": "AgentResponseEvaluator"}],
    }
    with pytest.raises(ValueError, match="evidence task correction"):
        dataset._validate_evaluation_provenance(evaluation, attempt, "data")
    evaluation["replay_contract_correction"] = {
        "correction_id": correction[0],
        "source_sha256": correction[1],
        "corrected_task_sha256": correction[2],
    }
    dataset._validate_evaluation_provenance(evaluation, attempt, "data")
    evaluation["replay_contract_correction"]["corrected_task_sha256"] = "old"
    with pytest.raises(ValueError, match="evidence task correction"):
        dataset._validate_evaluation_provenance(evaluation, attempt, "data")


def test_catalog_hash_and_symlink_are_enforced(tmp_path):
    calls, contract, _ = fixture()
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"schema": PROTOCOL, "contracts": [contract]}))
    binding = {
        "protocol": PROTOCOL,
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    assert load_contracts(binding, tmp_path, {1: {"tool_calls": calls}}) == {1: contract}
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="unsafe"):
        load_contracts({**binding, "path": link.name}, tmp_path, {1: {"tool_calls": calls}})
    with pytest.raises(ValueError, match="hash"):
        load_contracts({**binding, "sha256": "wrong"}, tmp_path, {1: {"tool_calls": calls}})


def test_v2_catalog_preserves_per_task_v1_and_v2_contract_protocols(tmp_path):
    v1_calls, v1_contract, _ = fixture()
    v2_calls, v2_contract, _ = reviewed_span_fixture()
    v2_contract["task_id"] = 2
    path = tmp_path / "mixed-contracts.json"
    path.write_text(json.dumps({"schema": PROTOCOL_V2, "contracts": [v1_contract, v2_contract]}))
    binding = {
        "protocol": PROTOCOL_V2,
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    loaded = load_contracts(
        binding,
        tmp_path,
        {1: {"tool_calls": v1_calls}, 2: {"tool_calls": v2_calls}},
    )
    assert loaded[1]["schema"] == PROTOCOL
    assert loaded[2]["schema"] == PROTOCOL_V2


@pytest.mark.parametrize("observed,expected_score", [(5, 1.0), (999, 0.0)])
def test_official_evaluator_uses_current_observation_not_the_frozen_answer(
    tmp_path, observed, expected_score
):
    from importlib.resources import files

    from webarena_verified.core.evaluation.evaluators.agent_response_evaluator import (
        AgentResponseEvaluator,
    )
    from webarena_verified.types.config import WebArenaVerifiedConfig
    from webarena_verified.types.eval import TaskEvalContext
    from webarena_verified.types.task import WebArenaVerifiedTask
    from webarena_verified.types.tracing import NetworkTrace

    tasks = json.loads(
        files("webarena_verified").joinpath("assets/dataset/webarena-verified.json").read_text()
    )
    task = WebArenaVerifiedTask.model_validate(next(row for row in tasks if row["task_id"] == 77))
    calls, contract, rows = fixture()
    rows[0]["result"] = json.dumps({"count": observed})
    answer = build(calls, contract, rows)["actual_response"]
    context = TaskEvalContext(
        task=task,
        agent_response_raw=answer,
        network_trace=NetworkTrace(
            is_playwright=False, src_file=tmp_path / "unused.har", events=()
        ),
        config=WebArenaVerifiedConfig(),
    )
    result = AgentResponseEvaluator().evaluate(context=context, config=task.eval[0])
    assert result.score == expected_score
    assert result.actual["retrieved_data"] == [observed]


def v3_review(contract, scope="distinct title values", reviewer_kind="agent"):
    contract["schema"] = PROTOCOL_V3
    reviewer = {"kind": reviewer_kind, "identity": "synthetic reviewer"}
    source_review = {"reviewer": reviewer, "scope": scope, "decision": "approved"}
    source_review.update(
        task_id=contract["task_id"],
        sources_sha256=digest(contract["sources"]),
        projection_sha256=digest(
            contract.get("reviewed_span_projection", contract.get("projection"))
        ),
    )
    evidence = {
        "schema": "lex_replay_review_evidence/1",
        "reviewer": reviewer,
        "scope": scope,
        "task_id": contract["task_id"],
        "sources_sha256": digest(contract["sources"]),
        "projection_sha256": digest(
            contract.get("reviewed_span_projection", contract.get("projection"))
        ),
        "source_review": source_review,
        "source_review_sha256": digest(source_review),
        "source_review_artifact": {
            "utf8": json.dumps(source_review),
            "sha256": text_digest(json.dumps(source_review)),
            "content": copy.deepcopy(source_review),
        },
    }
    contract["review_evidence"] = evidence
    contract["semantic_reference"] = {
        "kind": "reviewed_span_annotation"
        if "reviewed_span_projection" in contract
        else "reviewed_value_set",
        "browser_semantic_inference": False,
        "reviewer": reviewer,
        "scope": scope,
        "evidence_sha256": digest(evidence),
    }
    return contract


def distinct_fixture(values):
    calls, contract, rows = fixture()
    contract["projection"] = {"distinct_exact_strings": {"source": "read", "pointer": "/values"}}
    contract["requirements"] = {
        "scope": {"source": "read", "pointer": "/scope"},
        "completeness": {"source": "read", "pointer": "/complete"},
    }
    rows[0]["result"] = json.dumps({"values": values, "scope": True, "complete": True})
    return calls, v3_review(contract), rows


@pytest.mark.parametrize(
    "values,expected",
    [(["A", "A"], ["A"]), (["A", "B", "A"], ["A", "B"]), (["A", "a", " A"], ["A", "a", " A"])],
)
def test_v3_exact_strings(values, expected):
    result = build(*distinct_fixture(values))
    assert result["status"] == "supported"
    assert result["actual_response"]["retrieved_data"] == expected


@pytest.mark.parametrize("values", [[None], [1], [{"title": "A"}], [], [""]])
def test_v3_invalid_or_unproven_empty(values):
    assert build(*distinct_fixture(values))["status"] == "insufficient"


def test_v3_empty_policy_unchanged():
    calls, contract, rows = distinct_fixture([])
    contract["empty_result"] = "NOT_FOUND_ERROR"
    assert build(calls, contract, rows)["actual_response"]["status"] == "NOT_FOUND_ERROR"


@pytest.mark.parametrize(
    "field",
    ["reviewer", "scope", "sources_sha256", "projection_sha256", "source_review_sha256", "task_id"],
)
def test_v3_review_evidence_binding(field):
    calls, contract, rows = distinct_fixture(["A"])
    contract["review_evidence"][field] = "incorrect"
    contract["semantic_reference"]["evidence_sha256"] = digest(contract["review_evidence"])
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize("reviewer_kind", ["agent", "human"])
def test_v3_reviewed_spans_truthful_provenance(reviewer_kind):
    calls, contract, rows = reviewed_span_fixture()
    v3_review(contract, "exactly one book recommendation", reviewer_kind)
    result = build(calls, contract, rows)
    assert result["status"] == "supported"
    assert result["semantic_reference"]["reviewer"]["kind"] == reviewer_kind
    contract["reviewed_span_projection"]["annotation"]["items"][0]["recommendation_count"] = 0
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_v3_cannot_relabel_agent_receipt_human():
    calls, contract, rows = distinct_fixture(["A"])
    contract["semantic_reference"]["reviewer"] = {"kind": "human", "identity": "synthetic reviewer"}
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize("field", ["scope", "complete"])
def test_v3_requires_current_complete_scope(field):
    calls, contract, rows = distinct_fixture(["A"])
    value = json.loads(rows[0]["result"])
    value[field] = False
    rows[0]["result"] = json.dumps(value)
    assert build(calls, contract, rows)["status"] == "insufficient"


def test_v1_map_keeps_multiplicity_and_rejects_v3_operator():
    calls, contract, rows = fixture()
    contract["projection"] = {
        "map": {
            "input": {"source": "read", "pointer": ""},
            "projection": {"source": "$item", "pointer": ""},
        }
    }
    rows[0]["result"] = '["A","A"]'
    assert build(calls, contract, rows)["actual_response"]["retrieved_data"] == ["A", "A"]
    contract["projection"] = {"distinct_exact_strings": {"source": "read", "pointer": ""}}
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize(
    "values,score",
    [
        (["Memory Card Came Defective"] * 2, 1.0),
        (["Wrong title"] * 2, 0.0),
        (["Memory Card Came Defective", "Extra"], 0.0),
    ],
)
def test_v3_full_official_evaluator(tmp_path, values, score):
    from importlib.resources import files

    from webarena_verified.core.evaluation.evaluators.agent_response_evaluator import (
        AgentResponseEvaluator,
    )
    from webarena_verified.types.config import WebArenaVerifiedConfig
    from webarena_verified.types.eval import TaskEvalContext
    from webarena_verified.types.task import WebArenaVerifiedTask
    from webarena_verified.types.tracing import NetworkTrace

    tasks = json.loads(
        files("webarena_verified").joinpath("assets/dataset/webarena-verified.json").read_text()
    )
    task = WebArenaVerifiedTask.model_validate(next(row for row in tasks if row["task_id"] == 163))
    answer = build(*distinct_fixture(values))["actual_response"]
    context = TaskEvalContext(
        task=task,
        agent_response_raw=answer,
        network_trace=NetworkTrace(
            is_playwright=False, src_file=tmp_path / "unused.har", events=()
        ),
        config=WebArenaVerifiedConfig(),
    )
    assert AgentResponseEvaluator().evaluate(context=context, config=task.eval[0]).score == score


@pytest.mark.parametrize("expected,score", [(["📚Café"], 1.0), (["Different"], 0.0)])
def test_v3_span_full_evaluator(tmp_path, expected, score):
    from importlib.resources import files

    from webarena_verified.core.evaluation.evaluators.agent_response_evaluator import (
        AgentResponseEvaluator,
    )
    from webarena_verified.types.config import WebArenaVerifiedConfig
    from webarena_verified.types.eval import TaskEvalContext
    from webarena_verified.types.task import WebArenaVerifiedTask
    from webarena_verified.types.tracing import NetworkTrace

    tasks = json.loads(
        files("webarena_verified").joinpath("assets/dataset/webarena-verified.json").read_text()
    )
    raw_task = copy.deepcopy(next(row for row in tasks if row["task_id"] == 163))
    raw_task["eval"][0]["expected"]["retrieved_data"] = expected
    task = WebArenaVerifiedTask.model_validate(raw_task)
    calls, contract, rows = reviewed_span_fixture()
    v3_review(contract, "synthetic ordered spans")
    answer = build(calls, contract, rows)["actual_response"]
    context = TaskEvalContext(
        task=task,
        agent_response_raw=answer,
        network_trace=NetworkTrace(
            is_playwright=False, src_file=tmp_path / "unused.har", events=()
        ),
        config=WebArenaVerifiedConfig(),
    )
    assert AgentResponseEvaluator().evaluate(context=context, config=task.eval[0]).score == score


@pytest.mark.parametrize("field", ["reviewer", "scope", "sources_sha256", "projection_sha256"])
def test_v3_rehashed_disagreeing_source_review_rejected(field):
    calls, contract, rows = distinct_fixture(["A"])
    evidence = contract["review_evidence"]
    evidence["source_review"][field] = "wrong"
    evidence["source_review_sha256"] = digest(evidence["source_review"])
    contract["semantic_reference"]["evidence_sha256"] = digest(evidence)
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize("field", ["sha256", "utf8", "content"])
def test_v3_original_artifact_bytes_fail_closed(field):
    calls, contract, rows = distinct_fixture(["A"])
    evidence = contract["review_evidence"]
    evidence["source_review_artifact"][field] = "wrong"
    contract["semantic_reference"]["evidence_sha256"] = digest(evidence)
    assert build(calls, contract, rows)["status"] == "insufficient"


@pytest.mark.parametrize("mutation", [None, "hash", "body", "reviewer"])
def test_v3_predecessor_artifact_binding(mutation):
    calls, contract, rows = distinct_fixture(["A"])
    evidence = contract["review_evidence"]
    raw = '{ "reviewer": "synthetic reviewer" }\n'
    artifact = {
        "utf8": raw,
        "sha256": text_digest(raw),
        "content": json.loads(raw),
        "bindings": [
            {
                "artifact_pointer": "/reviewer",
                "contract_pointer": "/semantic_reference/reviewer/identity",
            }
        ],
    }
    evidence["source_review"]["predecessor_artifacts"] = [artifact]
    if mutation == "hash":
        artifact["sha256"] = "0" * 64
    if mutation == "body":
        artifact["utf8"] += " "
    if mutation == "reviewer":
        reviewer = {"kind": "human", "identity": "different reviewer"}
        contract["semantic_reference"]["reviewer"] = reviewer
        evidence["reviewer"] = reviewer
        evidence["source_review"]["reviewer"] = reviewer
    raw_review = json.dumps(evidence["source_review"])
    evidence["source_review_sha256"] = digest(evidence["source_review"])
    evidence["source_review_artifact"] = {
        "utf8": raw_review,
        "sha256": text_digest(raw_review),
        "content": copy.deepcopy(evidence["source_review"]),
    }
    contract["semantic_reference"]["evidence_sha256"] = digest(evidence)
    assert build(calls, contract, rows)["status"] == (
        "supported" if mutation is None else "insufficient"
    )


def test_v3_annotation_rebound_above_immutable_review_rejected():
    calls, contract, rows = reviewed_span_fixture()
    v3_review(contract, "synthetic spans")
    contract["reviewed_span_projection"]["annotation"]["items"][0]["spans"][0]["start"] = 0
    evidence = contract["review_evidence"]
    evidence["projection_sha256"] = digest(contract["reviewed_span_projection"])
    evidence["source_review"]["projection_sha256"] = evidence["projection_sha256"]
    evidence["source_review_sha256"] = digest(evidence["source_review"])
    contract["semantic_reference"]["evidence_sha256"] = digest(evidence)
    assert build(calls, contract, rows)["status"] == "insufficient"
