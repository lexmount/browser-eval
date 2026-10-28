"""Declared record requirements must be enforced or rejected at configuration time."""

import copy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_numeric_table_surfaces import composite_case

ERROR = "Required fields are supported only for named_link and field_report groups"
FIELD = {"id": "price", "kind": "price"}


def controls():
    for kind in ["name_description", "table", "linked_table", "literals"]:
        if kind in {"table", "linked_table"}:
            case = composite_case(kind)
            name = "[Alpha](/item/1)" if kind == "linked_table" else "Alpha"
            artifact = "|Name|Count|\n|---|---|\n|" + name + "|12|"
        else:
            group = {"id": "items", "kind": kind}
            if kind == "literals":
                group["required_literals"] = ["Alpha"]
                artifact = "Alpha"
            else:
                group["minimum_records"] = 1
                artifact = "|Name|Description|\n|---|---|\n|Alpha|A useful description.|"
            case = {
                "url": "https://example.test/catalog",
                "task_kind": "body",
                "min_content_chars": 1,
                "composite_acceptance": {"version": 1, "groups": [group]},
            }
        for declared in [False, True]:
            configured = copy.deepcopy(case)
            configured["composite_acceptance"]["groups"][0]["required_fields"] = (
                [FIELD] if declared else []
            )
            row = {
                "id": "unsupported-fields-" + kind + ("-declared" if declared else "-empty"),
                "case": configured,
                "artifact": artifact,
                "expected_task_pass": not declared,
            }
            if declared:
                row["expected_validation_error"] = ERROR
            yield row
    for kind in ["named_link", "field_report"]:
        group = {"id": "items", "kind": kind, "required_fields": [FIELD]}
        if kind == "named_link":
            group.update(minimum_records=1, hosts=["example.test"], entry_path=r"/item/\d+")
        for present in [False, True]:
            artifact = "[Alpha](/item/1)" if kind == "named_link" else "Report"
            if present:
                artifact += "\nPrice: $12"
            yield {
                "id": "supported-fields-" + kind + ("-present" if present else "-missing"),
                "case": {
                    "url": "https://example.test/catalog",
                    "task_kind": "body",
                    "min_content_chars": 1,
                    "composite_acceptance": {"version": 1, "groups": [group]},
                },
                "artifact": artifact,
                "expected_task_pass": present,
            }


CASES = list(controls())


@pytest.mark.parametrize("control", CASES, ids=[c["id"] for c in CASES])
def test_declared_field_support_at_public_boundary(control):
    if control.get("expected_validation_error"):
        with pytest.raises(ValueError, match=control["expected_validation_error"]):
            score_attempt(control["case"], control["artifact"].encode(), ok=True)
    else:
        metrics = score_attempt(control["case"], control["artifact"].encode(), ok=True)
        assert task_passed(True, metrics, "body") is control["expected_task_pass"]


@pytest.mark.parametrize("kind", ["named_link", "field_report"])
def test_supported_groups_still_reject_unknown_field_properties(kind):
    control = next(
        copy.deepcopy(row) for row in CASES if row["id"] == "supported-fields-" + kind + "-present"
    )
    control["case"]["composite_acceptance"]["groups"][0]["required_fields"][0]["unknown_field"] = (
        True
    )
    with pytest.raises(ValueError, match="Invalid composite"):
        score_attempt(control["case"], control["artifact"].encode(), ok=True)
