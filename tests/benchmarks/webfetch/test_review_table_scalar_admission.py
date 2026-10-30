"""Table scalar presentation, record admission and selected-row completeness."""

import copy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_numeric_table_surfaces import composite_case, table_case


def cases():
    for kind in ["table", "linked_table"]:
        for scalar, spec in [
            ("**12**", {"kind": "integer"}),
            ("`READY`", {"kind": "token", "pattern": "READY"}),
        ]:
            case = composite_case(kind)
            case["composite_acceptance"]["groups"][0]["required_columns"][1].update(spec)
            name = "[Alpha](/item/1)" if kind == "linked_table" else "Alpha"
            artifact = "|Name|Count|\n|---|---|\n|" + name + "|" + scalar + "|"
            yield (
                kind + "-" + spec["kind"] + "-formatted",
                "formatted-table-scalars",
                case,
                artifact,
                True,
            )
            yield (
                kind + "-" + spec["kind"] + "-invalid",
                "formatted-table-scalars",
                case,
                artifact.replace(scalar, "**12,**" if spec["kind"] == "integer" else "`BROKEN`"),
                False,
            )
    for shape in ["table", "linearized"]:
        for label, passed in [("Alpha", True), ("Excluded", False), ("Details", False)]:
            case = composite_case("linked_table")
            group = case["composite_acceptance"]["groups"][0]
            group["record_name_exclusions"] = ["excluded"]
            group["required_columns"][1].update(kind="text")
            artifact = (
                ("|Name|Count|\n|---|---|\n|[" + label + "](/item/1)|Owner|")
                if shape == "table"
                else ("Name\nCount\n[" + label + "](/item/1)\n[Owner](/profile/1)")
            )
            yield shape + "-" + label.lower(), "linked-table-name-admission", case, artifact, passed
    case = composite_case("linked_table")
    group = case["composite_acceptance"]["groups"][0]
    group.update(minimum_records=2, entry_path=r"/item/alpha\)(?:beta|gamma)")
    for second, passed in [("gamma", True), ("beta", False)]:
        artifact = (
            '<table><tr><th>Name</th><th>Count</th></tr><tr><td><a href="/item/alpha)beta">Alpha</a></td><td>12</td></tr><tr><td><a href="/item/alpha)'
            + second
            + '">Bravo</a></td><td>12</td></tr></table>'
        )
        yield (
            "native-destination-" + second,
            "html-table-destination-preservation",
            case,
            artifact,
            passed,
        )
    for shape in ["html", "markdown"]:
        for extra in ["", "|Bravo|", "|Bravo|12|extra|"]:
            case = table_case()
            case["acceptance_contract"]["maximum_records"] = 1
            artifact = "|Name|Count|\n|---|---|\n|Alpha|12|" + ("\n" + extra if extra else "")
            if shape == "html":
                artifact = (
                    '<table><tr><th>Name</th><th>Count</th></tr><tr class="data"><td>Alpha</td><td>12</td></tr>'
                    + (
                        '<tr class="data">'
                        + "".join("<td>" + v + "</td>" for v in extra.strip("|").split("|"))
                        + "</tr>"
                        if extra
                        else ""
                    )
                    + "</table>"
                )
            yield (
                shape + "-width-" + str(extra.count("|")),
                "selected-table-row-completeness",
                case,
                artifact,
                not bool(extra),
            )


CASES = list(cases())


@pytest.mark.parametrize("id,root,case,artifact,expected", CASES, ids=[row[0] for row in CASES])
def test_table_public_boundary(id, root, case, artifact, expected):
    metrics = score_attempt(copy.deepcopy(case), artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected


def scoring_controls():
    for id, root, case, artifact, expected in CASES:
        yield {
            "id": "round9-" + id,
            "failure_mode": "false-negative" if expected else "false-positive",
            "category": "structured-extraction",
            "root_cause": root,
            "case": case,
            "artifact": artifact,
            "ok": True,
            "expected_task_pass": expected,
        }
