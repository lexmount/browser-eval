"""Body extraction acceptance, independent of page-shell output."""

import copy

import pytest

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.risk_control_dataset import assign_content_acceptance_roles
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import load_scoring_regressions
from harness.web_fetch.stable_tasks import validate_acceptance_contract


def body_case():
    row = load_scoring_regressions()["explicit-body-without-page-shell-passes"]
    return copy.deepcopy(row["case"])


@pytest.mark.parametrize(
    "artifact,accepted",
    [
        ("alpha bravo charlie delta echo foxtrot golf hotel", True),
        ("<main>alpha bravo charlie delta echo foxtrot golf hotel</main>", True),
        ("Account login and site footer", False),
        ("alpha bravo charlie delta", False),
        ("", False),
    ],
)
def test_body_contract_controls_acceptance_across_answer_formats(artifact, accepted):
    metrics = score_attempt(body_case(), artifact.encode(), ok=True)
    assert task_passed(True, metrics) is accepted
    assert metrics["stable_anchor_total"] == 1


def test_final_url_mismatch_remains_diagnostic_with_complete_body():
    case = body_case()
    case["track"] = "risk_control"
    case["source_benchmark"] = {
        "risk_control": {
            "navigation_contract": {
                "requested_url": case["url"],
                "observed_final_url": case["url"],
                "target_language": "en",
            }
        }
    }
    metrics = score_attempt(
        case,
        b"alpha bravo charlie delta echo foxtrot golf hotel",
        ok=True,
        final_url="https://example.test/wrong",
    )
    assert task_passed(True, metrics)
    assert metrics["page_identity_status"] == "mismatch"
    assert metrics["final_url_consistent"] is False
    assert metrics["page_identity_required"] is False


def test_diagnostic_role_cannot_remove_the_only_acceptance_gate():
    case = body_case()
    del case["body_requirements"]
    with pytest.raises(ValueError, match="requires body, composite, or page-pattern"):
        assign_content_acceptance_roles([case])
    with pytest.raises(ValueError, match="requires body, composite, or page-pattern"):
        score_attempt(case, b"unrelated body", ok=True)


def test_page_pattern_can_own_scope_while_legacy_literals_stay_diagnostic():
    case = body_case()
    del case["body_requirements"]
    case["acceptance_contract"]["page_patterns"] = [r"archive introduction"]
    assign_content_acceptance_roles([case])
    passing = score_attempt(case, b"archive introduction", ok=True)
    failing = score_attempt(case, b"Account login and site footer", ok=True)
    assert task_passed(True, passing)
    assert not task_passed(True, failing)


def test_empty_composite_owner_cannot_make_stable_text_diagnostic():
    case = body_case()
    del case["body_requirements"]
    case["composite_acceptance"] = {"version": 1, "groups": []}
    with pytest.raises(ValueError, match="requires body, composite, or page-pattern"):
        assign_content_acceptance_roles([case])


def test_structured_contract_cannot_relax_its_stable_text():
    contract = {
        "version": 1,
        "schema": "named_link",
        "minimum_records": 1,
        "hosts": ["example.test"],
        "entry_path": "/item/[0-9]+",
        "stable_text_role": "diagnostic",
    }
    with pytest.raises(ValueError, match="body-text"):
        validate_acceptance_contract(contract)


def test_compiler_policy_does_not_infer_diagnostic_from_gate_presence():
    body = body_case()
    del body["acceptance_contract"]["stable_text_role"]
    composite = copy.deepcopy(body)
    del composite["body_requirements"]
    composite["composite_acceptance"] = {
        "version": 1,
        "groups": [
            {"id": "identity", "kind": "literals", "required_literals": ["Target identity"]}
        ],
    }
    assign_content_acceptance_roles([body, composite])
    assert "stable_text_role" not in body["acceptance_contract"]
    assert "stable_text_role" not in composite["acceptance_contract"]


def test_explicit_diagnostic_role_is_preserved_with_an_independent_body_gate():
    case = body_case()
    assert case["acceptance_contract"]["stable_text_role"] == "diagnostic"
    assign_content_acceptance_roles([case])
    assert case["acceptance_contract"]["stable_text_role"] == "diagnostic"


@pytest.mark.parametrize("restriction", ["forbidden", "blocked", "too_short"])
def test_diagnostic_shell_does_not_bypass_other_body_gates(restriction):
    case = body_case()
    artifact = "alpha bravo charlie delta echo foxtrot golf hotel"
    if restriction == "forbidden":
        case["forbidden_any"] = ["foxtrot"]
    elif restriction == "blocked":
        case["forbidden_any"] = ["Access denied"]
        artifact += "\nAccess denied"
    else:
        case["min_content_chars"] = len(artifact) + 1
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert not task_passed(True, metrics)


def test_omitted_role_preserves_the_existing_mandatory_heading_contract():
    case = body_case()
    del case["acceptance_contract"]["stable_text_role"]
    metrics = score_attempt(case, b"alpha bravo charlie delta echo foxtrot golf hotel", ok=True)
    assert not task_passed(True, metrics)
