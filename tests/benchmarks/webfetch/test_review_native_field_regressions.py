"""Native links retain scoped field ownership and declared resolution bases."""

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.scoring_regressions import load_scoring_regressions
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


@pytest.mark.parametrize(
    "identifier",
    [
        "mixed-html-markdown-record-attributes-are-not-fields",
        "field-card-adjacent-name-html-visible",
        "field-card-adjacent-name-html-consecutive-records",
    ],
)
def test_immutable_public_controls_keep_their_declared_behavior(identifier):
    row = load_scoring_regressions()[identifier]
    metrics = score_attempt(row["case"], row["artifact"].encode(), ok=row["ok"])
    assert (
        task_passed(row["ok"], metrics, row["case"].get("task_kind", "body"))
        is row["expected_task_pass"]
    )


@pytest.mark.parametrize("representation", ["markdown", "html"])
@pytest.mark.parametrize("shape", ["anchor", "table"])
@pytest.mark.parametrize("override", [False, True])
def test_native_and_markdown_links_share_the_declared_base(representation, shape, override):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["example.test"],
        "entry_path": r"/catalog/item/\d+",
    }
    if shape == "table":
        group["required_fields"] = [{"id": "price", "kind": "price"}]
    if override:
        group["link_base_url"] = "https://example.test/catalog/"
    case = composite_case(group)
    case["url"] = "https://example.test/old/"
    artifact = (
        "[Alpha](item/1)\nPrice: $12"
        if representation == "markdown"
        else '<a href="item/1">Alpha</a>\nPrice: $12'
    )
    if shape == "table" and representation == "html":
        artifact = '<table><tr><td><a href="item/1">Alpha</a></td><td>Price: $12</td></tr></table>'
    metrics = score_attempt(
        case, artifact.encode(), ok=True, metadata={"answer_format": representation}
    )
    assert task_passed(True, metrics, "body") is override


@pytest.mark.parametrize(
    "name_surface,expected",
    [
        ("<div>10 Cedar Road</div>", True),
        ('<div data-name="10 Cedar Road"></div>', False),
        ("<div hidden>10 Cedar Road</div>", False),
    ],
)
def test_adjacent_native_name_requires_visible_scalar(name_surface, expected):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["example.test"],
        "entry_path": r"/item/\d+",
        "required_fields": [{"id": "price", "kind": "price"}, {"id": "area", "kind": "area"}],
    }
    target = "https://example.test/item/1"
    link = '<a href="' + target + '">' + target + "</a>"
    artifact = (
        "<article><div>$100,000</div>"
        + link
        + "<div>900 sqft</div>"
        + name_surface
        + link
        + "</article>"
    )
    metrics = score_attempt(composite_case(group), artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("wrong_second_base", [False, True])
def test_one_group_override_does_not_leak_into_other_groups(wrong_second_base):
    first = {
        "id": "override",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["example.test"],
        "entry_path": r"/catalog/item/\d+",
        "link_base_url": "https://example.test/catalog/",
    }
    second = {
        "id": "request",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["example.test"],
        "entry_path": r"/catalog/other/\d+" if wrong_second_base else r"/old/other/\d+",
    }
    case = composite_case(first)
    case["url"] = "https://example.test/old/"
    case["composite_acceptance"]["groups"].append(second)
    artifact = '<a href="item/1">Alpha</a>\n<a href="other/2">Beta</a>'
    metrics = score_attempt(case, artifact.encode(), ok=True, metadata={"answer_format": "html"})
    assert task_passed(True, metrics, "body") is not wrong_second_base
