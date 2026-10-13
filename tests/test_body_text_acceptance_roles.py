"""Body extraction acceptance, independent of page-shell output."""
import copy
import json
from pathlib import Path

import pytest

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.risk_control_dataset import assign_content_acceptance_roles
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.scoring_regressions import load_scoring_regressions
from harness.web_fetch.stable_tasks import validate_acceptance_contract
from tests.conftest import read_frozen_dataset_bytes


def body_case():
    row = load_scoring_regressions()["explicit-body-without-page-shell-passes"]
    return copy.deepcopy(row["case"])


@pytest.mark.parametrize("artifact,accepted", [
    ("alpha bravo charlie delta echo foxtrot golf hotel", True),
    ("<main>alpha bravo charlie delta echo foxtrot golf hotel</main>", True),
    ("Account login and site footer", False),
    ("alpha bravo charlie delta", False),
    ("", False),
])
def test_body_contract_controls_acceptance_across_answer_formats(artifact, accepted):
    metrics = score_attempt(body_case(), artifact.encode(), ok=True)
    assert task_passed(True, metrics) is accepted
    assert metrics["stable_anchor_total"] == 1


def test_page_identity_remains_required_with_diagnostic_shell():
    case = body_case()
    case["track"] = "risk_control"
    case["source_benchmark"] = {"risk_control": {"navigation_contract": {
        "requested_url": case["url"], "observed_final_url": case["url"],
        "target_language": "en"}}}
    metrics = score_attempt(case, b"alpha bravo charlie delta echo foxtrot golf hotel",
                            ok=True, final_url="https://example.test/wrong")
    assert not task_passed(True, metrics)


def test_diagnostic_role_cannot_remove_the_only_acceptance_gate():
    case = body_case()
    del case["body_requirements"]
    with pytest.raises(ValueError, match="requires body or composite"):
        score_attempt(case, b"unrelated body", ok=True)


def test_structured_contract_cannot_relax_its_stable_text():
    contract = {"version": 1, "schema": "named_link", "minimum_records": 1,
                "hosts": ["example.test"], "entry_path": "/item/[0-9]+",
                "stable_text_role": "diagnostic"}
    with pytest.raises(ValueError, match="body-text"):
        validate_acceptance_contract(contract)


def test_compiler_policy_covers_the_frozen_hundred_cases_without_changing_content():
    path = Path(__file__).resolve().parents[1] / "evaluation/risk-control/dataset.json"
    original = json.loads(read_frozen_dataset_bytes(path))["cases"]
    revised = copy.deepcopy(original)
    assign_content_acceptance_roles(revised)
    roles = [c["acceptance_contract"].pop("stable_text_role") for c in revised]
    assert roles.count("diagnostic") == 97
    assert roles.count("required") == 3
    assert revised == original
    for case, role in zip(original, roles, strict=True):
        has_content_contract = bool(case.get("body_requirements")
                                    or case.get("composite_acceptance"))
        assert (role == "diagnostic") == has_content_contract


@pytest.mark.parametrize("restriction", ["forbidden", "blocked", "too_short"])
def test_diagnostic_shell_does_not_bypass_other_body_gates(restriction):
    case = body_case()
    artifact = "alpha bravo charlie delta echo foxtrot golf hotel"
    if restriction == "forbidden":
        case["forbidden_any"] = ["foxtrot"]
    elif restriction == "blocked":
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
