"""Owned integer fields and independent table representations share acceptance rules."""

import copy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


def cases():
    for kind in ["field_report", "named_link", "patterned_card"]:
        field = {"id": "count", "kind": "integer"}
        field.update(pattern="comments" if kind == "patterned_card" else None)
        if field["pattern"] is None:
            del field["pattern"]
            field["aliases"] = ["Count"]
        group = {
            "id": "items",
            "kind": "named_link" if kind == "patterned_card" else kind,
            "required_fields": [field],
        }
        if kind != "field_report":
            group.update(minimum_records=1, hosts=["example.test"], entry_path=r"/item/\d+")
        for value, passed in [
            ("12", True),
            ("1,234", True),
            ("1.234", True),
            ("1 234", True),
            ("12.", True),
            ("12, as expected", True),
            ("1.5", False),
            ("12.34", False),
            ("1..2", False),
            ("1,234.567", False),
        ]:
            artifact = "" if kind == "field_report" else "[Alpha](/item/1)\n"
            artifact += (
                (
                    "12 comments."
                    if value == "12."
                    else "12 comments, as expected"
                    if value == "12, as expected"
                    else value + " comments"
                )
                if kind == "patterned_card"
                else "Count: " + value
            )
            yield (
                kind + "-" + value.replace(" ", "-"),
                "owned-integer-token",
                composite_case(group),
                artifact,
                passed,
            )
    group = {
        "id": "items",
        "kind": "linked_table",
        "minimum_records": 2,
        "hosts": ["example.test"],
        "entry_path": r"/item/\d+",
        "identity_column": "name",
        "link_column": "name",
        "required_columns": [
            {"id": field, "kind": "text", "aliases": [alias]}
            for field, alias in [
                ("name", "Name"),
                ("description", "Description"),
                ("owner", "Owner"),
            ]
        ],
    }
    linear = "Name\nDescription\nOwner\n[Alpha](/item/1)\n[Useful description](/desc/a)\n[Alice](/profile/a)"
    pipe = "|Name|Description|Owner|\n|---|---|---|\n|[Beta](/item/2)|Other description|Bob|"
    for order in ["linear-first", "pipe-first"]:
        for shape in ["complete", "missing-owner", "duplicate"]:
            first = (
                linear.replace("\n[Alice](/profile/a)", "") if shape == "missing-owner" else linear
            )
            second = pipe.replace("/item/2", "/item/1") if shape == "duplicate" else pipe
            artifact = (
                first + "\n\n" + second if order == "linear-first" else second + "\n\n" + first
            )
            yield (
                order + "-" + shape,
                "mixed-linked-table-blocks",
                composite_case(group),
                artifact,
                shape == "complete",
            )
    case = composite_case(copy.deepcopy(group))
    case["composite_acceptance"]["groups"][0]["minimum_records"] = 3
    yield (
        "linear-pipe-linear",
        "mixed-linked-table-blocks",
        case,
        linear
        + "\n\n"
        + pipe
        + "\n\n"
        + linear.replace("Alpha", "Gamma").replace("/item/1", "/item/3"),
        True,
    )
    yield (
        "no-header-borrow-across-pipe",
        "mixed-linked-table-blocks",
        composite_case(group),
        "Name\nDescription\nOwner\n\n" + pipe + "\n\n" + linear.split("Owner\n", 1)[1],
        False,
    )


CASES = list(cases())


@pytest.mark.parametrize("id,root,case,artifact,expected", CASES, ids=[row[0] for row in CASES])
def test_owned_values_and_mixed_blocks(id, root, case, artifact, expected):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize(
    "value,expected", [("1.234", True), ("1,234", True), ("1 234", True), ("1.235", False)]
)
def test_fixed_integer_values_compare_the_supported_grouped_scalar(value, expected):
    group = {
        "id": "items",
        "kind": "field_report",
        "required_fields": [{"id": "count", "kind": "integer", "aliases": ["Count"]}],
        "fixed_values": {"count": "1234"},
    }
    metrics = score_attempt(composite_case(group), ("Count: " + value).encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("separator", [".", ",", " "])
@pytest.mark.parametrize(
    "kind", ["field_report", "named_link", "patterned_card", "table", "linked_table"]
)
@pytest.mark.parametrize("leading,expected", [("0", False), ("1", True)])
def test_grouped_integer_first_group_is_nonzero(separator, kind, leading, expected):
    from tests.benchmarks.webfetch.test_review_numeric_table_surfaces import (
        composite_case as table_case,
    )

    value = leading + separator + "234"
    if kind in {"table", "linked_table"}:
        case = table_case(kind)
        name = "[Alpha](/item/1)" if kind == "linked_table" else "Alpha"
        artifact = "|Name|Count|\n|---|---|\n|" + name + "|" + value + "|"
    else:
        field = {"id": "count", "kind": "integer"}
        field.update(
            {"pattern": "comments"} if kind == "patterned_card" else {"aliases": ["Count"]}
        )
        group = {
            "id": "items",
            "kind": "named_link" if kind == "patterned_card" else kind,
            "required_fields": [field],
        }
        if kind != "field_report":
            group.update(minimum_records=1, hosts=["example.test"], entry_path=r"/item/\d+")
        case = composite_case(group)
        artifact = "" if kind == "field_report" else "[Alpha](/item/1)\n"
        artifact += value + " comments" if kind == "patterned_card" else "Count: " + value
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("value", ["0", "00012"])
def test_plain_zero_and_padded_integer_values_remain_valid(value):
    group = {
        "id": "items",
        "kind": "field_report",
        "required_fields": [{"id": "count", "kind": "integer", "aliases": ["Count"]}],
    }
    metrics = score_attempt(composite_case(group), ("Count: " + value).encode(), ok=True)
    assert task_passed(True, metrics, "body")


@pytest.mark.parametrize("digits", ["０１２３４５６７８９", "٠١٢٣٤٥٦٧٨٩"])
@pytest.mark.parametrize("separator", [".", ",", " "])
@pytest.mark.parametrize("kind", ["field_report", "table"])
@pytest.mark.parametrize("leading,expected", [("0", False), ("1", True)])
def test_unicode_decimal_grouping_keeps_digit_support(digits, separator, kind, leading, expected):
    from tests.benchmarks.webfetch.test_review_numeric_table_surfaces import (
        composite_case as table_case,
    )

    value = (leading + separator + "234").translate(str.maketrans("0123456789", digits))
    if kind == "table":
        case = table_case(kind)
        artifact = "|Name|Count|\n|---|---|\n|Alpha|" + value + "|"
    else:
        case = composite_case(
            {
                "id": "items",
                "kind": kind,
                "required_fields": [{"id": "count", "kind": "integer", "aliases": ["Count"]}],
            }
        )
        artifact = "Count: " + value
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("value", ["０", "０００１２", "٠", "٠٠٠١٢"])
def test_unicode_plain_zero_and_padded_integer_values_remain_valid(value):
    group = {
        "id": "items",
        "kind": "field_report",
        "required_fields": [{"id": "count", "kind": "integer", "aliases": ["Count"]}],
    }
    metrics = score_attempt(composite_case(group), ("Count: " + value).encode(), ok=True)
    assert task_passed(True, metrics, "body")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("１．５", False),
        ("١٫٥", False),
        ("１，５", False),
        ("١٬٥", False),
        ("１．．５", False),
        ("١٫٫٥", False),
        ("１２３", True),
        ("١٢٣", True),
        ("１２．", True),
        ("12.", True),
        ("12, as expected", True),
    ],
)
@pytest.mark.parametrize("patterned", [False, True])
def test_unsupported_decimal_separators_cannot_expose_integer_fragments(value, expected, patterned):
    field = {"id": "count", "kind": "integer"}
    field.update({"pattern": "comments"} if patterned else {"aliases": ["Count"]})
    group = {
        "id": "items",
        "kind": "named_link" if patterned else "field_report",
        "required_fields": [field],
    }
    if patterned:
        group.update(minimum_records=1, hosts=["example.test"], entry_path=r"/item/\d+")
        artifact = "[Alpha](/item/1)\n" + value + " comments"
        # The count precedes its declared unit; punctuation follows the phrase.
        if value == "12.":
            artifact = "[Alpha](/item/1)\n12 comments."
        elif value == "12, as expected":
            artifact = "[Alpha](/item/1)\n12 comments, as expected"
        elif value == "１２．":
            artifact = "[Alpha](/item/1)\n１２ comments．"
    else:
        artifact = "Count: " + value
    metrics = score_attempt(composite_case(group), artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected
