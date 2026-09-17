"""Opt-in v3 downstream scope review through the production public API."""

import copy
import json

import pytest

from harness.trajectory_replay.retrieval_acceptance import (
    PROTOCOL_V1,
    PROTOCOL_V3,
    build_actual_response,
    digest,
    text_digest,
)


def ref(path):
    return {"source": "read", "pointer": path}


def materialize(contract, rows, decision="accept"):
    reviewer = {"kind": "agent", "identity": "synthetic scope reviewer"}
    scope = "Complete synthetic reviews and customer table; unique-name inference disclosed"
    review = {
        "reviewer": reviewer,
        "scope": scope,
        "task_id": contract["task_id"],
        "sources_sha256": digest(contract["sources"]),
        "projection_sha256": digest(contract["projection"]),
        "scope_review": {
            "decision": decision,
            "rationale": "Explicit synthetic semantic review, never an answer literal",
            "scope_contract_sha256": digest(
                {
                    "reviewed_scope": contract["reviewed_scope"],
                    "requirements": contract["requirements"],
                }
            ),
            "sources": [
                {
                    "source": "read",
                    "output_sha256": digest(rows[0]["result"]),
                    "value_sha256": digest(json.loads(rows[0]["result"])),
                }
            ],
        },
    }
    bind_review(contract, review)


def bind_review(contract, review):
    utf8 = json.dumps(review)
    evidence = {
        "schema": "lex_replay_review_evidence/1",
        "reviewer": review["reviewer"],
        "scope": review["scope"],
        "task_id": contract["task_id"],
        "sources_sha256": digest(contract["sources"]),
        "projection_sha256": digest(contract["projection"]),
        "source_review": review,
        "source_review_sha256": digest(review),
        "source_review_artifact": {
            "utf8": utf8,
            "sha256": text_digest(utf8),
            "content": copy.deepcopy(review),
        },
    }
    contract["review_evidence"] = evidence
    contract["semantic_reference"] = {
        "kind": "reviewed_value_set",
        "browser_semantic_inference": False,
        "reviewer": review["reviewer"],
        "scope": review["scope"],
        "evidence_sha256": digest(evidence),
    }


def fixture():
    calls = [{"action": "evaluate", "script": "return currentObservation"}]
    value = {
        "scope": False,
        "complete": True,
        "reviews": [
            {
                "id": 11,
                "text": "Poor quality, would not recommend",
                "nickname": "Alex Doe",
                "customer_id": None,
                "linked_name": None,
                "linked_email": None,
            },
            {
                "id": 12,
                "text": "Very happy with it",
                "nickname": "Robin Doe",
                "customer_id": None,
                "linked_name": None,
                "linked_email": None,
            },
        ],
        "review_pages": [{"page": 1, "rows": 2}],
        "customers": [
            {"id": 1, "name": "Alex Doe", "email": "alex@example.test"},
            {"id": 2, "name": "Robin Doe", "email": "robin@example.test"},
        ],
        "customer_pages": [{"page": 1, "rows": 2, "total": 2}],
    }
    rows = [{"index": 1, "arguments": calls[0], "status": "success", "result": json.dumps(value)}]
    contract = {
        "schema": PROTOCOL_V3,
        "task_id": 1,
        "canonical_calls_sha256": digest(calls),
        "sources": [
            {"name": "read", "step": 1, "action_sha256": digest(calls[0]), "decode": "json"}
        ],
        "projection": {"distinct_exact_strings": {"array": [ref("/customers/0/email")]}},
        "requirements": {"scope": ref("/scope"), "completeness": ref("/complete")},
        "reviewed_scope": {
            "review_pointer": "/scope_review",
            "observed_requirements": [ref("/complete")],
            "populations": [
                {
                    "records": ref("/reviews"),
                    "pages": ref("/review_pages"),
                    "id_pointer": "/id",
                    "required_fields": ["/text", "/nickname"],
                },
                {
                    "records": ref("/customers"),
                    "pages": ref("/customer_pages"),
                    "id_pointer": "/id",
                    "required_fields": ["/name", "/email"],
                },
            ],
            "identity_joins": [
                {
                    "selected": ref("/reviews/0"),
                    "candidates": ref("/customers"),
                    "result": ref("/customers/0"),
                    "selected_fields": {
                        "id": "/customer_id",
                        "name": "/nickname",
                        "linked_name": "/linked_name",
                        "linked_email": "/linked_email",
                    },
                    "candidate_fields": {"id": "/id", "name": "/name", "email": "/email"},
                    "allow_unique_exact_name_inference": True,
                }
            ],
        },
    }
    materialize(contract, rows)
    return calls, contract, rows


def build(calls, contract, rows):
    before = copy.deepcopy(rows)
    result = build_actual_response(
        contract=contract,
        task_id=1,
        calls=calls,
        records=rows,
        run_id="synthetic-run",
        attempt_id="synthetic-attempt",
    )
    assert rows == before
    return result


def mutate(rows, callback):
    value = json.loads(rows[0]["result"])
    callback(value)
    rows[0]["result"] = json.dumps(value)


def assert_insufficient(result):
    assert result["status"] == "insufficient"
    assert result["actual_response"] is None
    assert result["reason"]


def test_reviewed_scope_projects_current_email_without_changing_false_scope():
    calls, contract, rows = fixture()
    result = build(calls, contract, rows)
    assert result["status"] == "supported"
    assert result["actual_response"]["retrieved_data"] == ["alex@example.test"]
    assert result["reviewed_scope"]["identity_bases"] == ["unique_exact_name_inference"]
    assert json.loads(rows[0]["result"])["scope"] is False


@pytest.mark.parametrize(
    "scope,complete,expected",
    [
        (False, True, "insufficient"),
        (True, False, "insufficient"),
        (True, True, "supported"),
    ],
)
def test_reviewed_scope_cannot_turn_partial_empty_observation_into_not_found(
    scope, complete, expected
):
    calls, contract, rows = fixture()
    contract["projection"] = {
        "distinct_exact_strings": {"source": "read", "pointer": "/answers"}
    }
    contract["empty_result"] = "NOT_FOUND_ERROR"
    mutate(rows, lambda value: value.update(answers=[], scope=scope, complete=complete))
    materialize(contract, rows)
    result = build(calls, contract, rows)
    assert result["status"] == expected
    if expected == "supported":
        assert result["actual_response"]["status"] == "NOT_FOUND_ERROR"
        assert result["actual_response"]["retrieved_data"] is None
    else:
        assert result["actual_response"] is None


@pytest.mark.parametrize("kind", ["missing", "null", "zero", "string_false"])
def test_reviewed_scope_only_replaces_explicit_false(kind):
    calls, contract, rows = fixture()
    mutate(
        rows,
        lambda v: (
            v.pop("scope")
            if kind == "missing"
            else v.update(scope={"null": None, "zero": 0, "string_false": "false"}[kind])
        ),
    )
    materialize(contract, rows)
    assert_insufficient(build(calls, contract, rows))


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.update(complete=False),
        lambda v: v.update(customer_pages=[]),
        lambda v: v["customer_pages"][0].update(rows=1),
        lambda v: v["customer_pages"][0].update(total=3),
        lambda v: v["reviews"][1].update(id=11),
        lambda v: v["reviews"][1].update(text=""),
        lambda v: v["customers"][1].update(id=1),
        lambda v: v["customers"][1].update(name="Alex Doe"),
        lambda v: v["reviews"][0].update(nickname="Missing Person"),
        lambda v: v["reviews"][0].update(nickname="customer"),
        lambda v: v["reviews"][0].update(customer_id=1, linked_email="other@example.test"),
        lambda v: v["reviews"][0].update(customer_id=1, linked_name="Wrong Name"),
    ],
)
def test_fresh_accept_review_cannot_override_hard_failures(change):
    calls, contract, rows = fixture()
    mutate(rows, change)
    materialize(contract, rows)
    assert_insufficient(build(calls, contract, rows))


def test_explicit_account_identity_does_not_require_nickname_match():
    calls, contract, rows = fixture()
    mutate(rows, lambda v: v["reviews"][0].update(customer_id=1, nickname="nickname"))
    materialize(contract, rows)
    result = build(calls, contract, rows)
    assert result["status"] == "supported"
    assert result["reviewed_scope"]["identity_bases"] == ["explicit_customer_id"]


@pytest.mark.parametrize(
    "change",
    [
        lambda c, r: c.pop("review_evidence"),
        lambda c, r: c["review_evidence"]["source_review_artifact"].update(sha256="0" * 64),
        lambda c, r: r[0].update(result=r[0]["result"] + "\n"),
        lambda c, r: mutate(r, lambda v: v["reviews"][0].update(text="Changed judgement context")),
        lambda c, r: mutate(r, lambda v: v["customers"][0].update(email="changed@example.test")),
        lambda c, r: c["reviewed_scope"].update(identity_joins=[]),
        lambda c, r: r[0].update(status="error"),
    ],
)
def test_missing_or_stale_scope_review_fails_closed(change):
    calls, contract, rows = fixture()
    change(contract, rows)
    assert_insufficient(build(calls, contract, rows))


@pytest.mark.parametrize("decision", ["reject", "unknown", None, []])
def test_scope_review_rejection_or_bad_decision_is_insufficient(decision):
    calls, contract, rows = fixture()
    materialize(contract, rows, decision)
    assert_insufficient(build(calls, contract, rows))


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["reviewed_scope"].update(review_pointer="/absent"),  # KeyError
        lambda c: c["reviewed_scope"]["identity_joins"][0]["result"].update(
            pointer="/customers/99"
        ),  # IndexError
        lambda c: c["reviewed_scope"]["identity_joins"][0]["result"].update(
            pointer="not-a-pointer"
        ),  # ValueError
        lambda c: c["reviewed_scope"].update(populations=None),  # invalid type
    ],
)
def test_review_validation_errors_use_public_insufficient_result(change):
    calls, contract, rows = fixture()
    change(contract)
    materialize(contract, rows)
    assert_insufficient(build(calls, contract, rows))


@pytest.mark.parametrize("schema", [PROTOCOL_V1, PROTOCOL_V3])
@pytest.mark.parametrize("scope,expected", [(False, "insufficient"), (True, "supported")])
def test_existing_protocols_without_reviewed_scope_keep_semantics(schema, scope, expected):
    calls, contract, rows = fixture()
    contract.pop("reviewed_scope")
    if schema == PROTOCOL_V1:
        contract["schema"] = schema
        contract.pop("review_evidence")
        contract.pop("semantic_reference")
        contract["projection"] = {"array": [ref("/customers/0/email")]}
    mutate(rows, lambda v: v.update(scope=scope))
    assert build(calls, contract, rows)["status"] == expected


def test_fully_rehashed_wrong_current_digest_still_rejected():
    calls, contract, rows = fixture()
    review = copy.deepcopy(contract["review_evidence"]["source_review"])
    review["scope_review"]["sources"][0]["value_sha256"] = "0" * 64
    bind_review(contract, review)
    assert_insufficient(build(calls, contract, rows))


def test_review_cannot_enable_undeclared_name_inference():
    calls, contract, rows = fixture()
    contract["reviewed_scope"]["identity_joins"][0]["allow_unique_exact_name_inference"] = False
    materialize(contract, rows)
    assert_insufficient(build(calls, contract, rows))


@pytest.mark.parametrize("requirement", ["scope", "completeness"])
@pytest.mark.parametrize("renew", [False, True])
def test_review_binds_requirement_references(requirement, renew):
    calls, contract, rows = fixture()
    mutate(rows, lambda value: value.update(other_scope=False, other_complete=True))
    materialize(contract, rows)
    unchanged_review = copy.deepcopy(contract["review_evidence"])
    contract["requirements"][requirement] = ref(
        "/other_scope" if requirement == "scope" else "/other_complete"
    )
    if renew:
        materialize(contract, rows)
        assert build(calls, contract, rows)["status"] == "supported"
    else:
        assert contract["review_evidence"] == unchanged_review
        assert_insufficient(build(calls, contract, rows))


def test_legacy_scope_only_digest_is_rejected_even_when_materialized():
    calls, contract, rows = fixture()
    review = copy.deepcopy(contract["review_evidence"]["source_review"])
    review["scope_review"]["scope_contract_sha256"] = digest(contract["reviewed_scope"])
    bind_review(contract, review)
    assert_insufficient(build(calls, contract, rows))
