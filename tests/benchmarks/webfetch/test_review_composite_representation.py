"""Composite linked records honor literal formats and explicit plain URL fields."""

import html

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


@pytest.mark.parametrize("wildcard", [False, True])
@pytest.mark.parametrize("fields", [False, True])
@pytest.mark.parametrize("shape", ["markdown", "html", "plain_fields"])
@pytest.mark.parametrize("representation", ["plain_text", "markdown"])
def test_composite_named_record_format(wildcard, fields, shape, representation):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["*"] if wildcard else ["example.test"],
        "entry_path": r"/item/\d+",
    }
    if fields:
        group["required_fields"] = [{"id": "price", "kind": "price"}]
    artifact = {
        "markdown": "[Alpha](/item/1)",
        "html": '<div><a href="/item/1">Alpha</a></div>',
        "plain_fields": "Alpha\nhttps://example.test/item/1",
    }[shape] + "\nPrice: $12"
    expected = (
        (shape == "plain_fields") if representation == "plain_text" else shape != "plain_fields"
    )
    metrics = score_attempt(
        composite_case(group),
        artifact.encode(),
        ok=True,
        metadata={"answer_format": representation},
    )
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("wildcard", [False, True])
def test_plain_composite_can_use_explicit_named_metadata(wildcard):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["*"] if wildcard else ["example.test"],
        "entry_path": r"/item/\d+",
    }
    metrics = score_attempt(
        composite_case(group),
        b"Alpha",
        ok=True,
        metadata={"answer_format": "plain_text", "links": [{"text": "Alpha", "url": "/item/1"}]},
    )
    assert task_passed(True, metrics, "body")


@pytest.mark.parametrize("wildcard", [False, True])
@pytest.mark.parametrize("fields", [False, True])
@pytest.mark.parametrize(
    "representation,literal",
    [(None, False), ("html", False), ("plain_text", False), ("markdown", True)],
)
def test_original_inline_anchor_reaches_structural_owner(wildcard, fields, representation, literal):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["*"] if wildcard else ["example.test"],
        "entry_path": r"/item/\d+",
    }
    if fields:
        group["required_fields"] = [{"id": "price", "kind": "price"}]
    artifact = '<a href="/item/1">Alpha</a>\nPrice: $12'
    if literal:
        artifact = "```html\n" + artifact + "\n```"
    metadata = {"answer_format": representation} if representation else None
    metrics = score_attempt(composite_case(group), artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, "body") is (representation != "plain_text" and not literal)


@pytest.mark.parametrize("kind", ["field_report", "literals"])
@pytest.mark.parametrize("hidden", [False, True])
def test_structural_raw_view_keeps_prose_group_visibility(kind, hidden):
    group = {"id": "item", "kind": kind}
    if kind == "field_report":
        group["required_fields"] = [{"id": "count", "kind": "integer", "aliases": ["Count"]}]
        body = "Count: 12"
    else:
        group["required_literals"] = ["Alpha"]
        body = "Alpha"
    artifact = "<div" + (" hidden" if hidden else "") + ">\n" + body + "\n</div>"
    metrics = score_attempt(composite_case(group), artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is not hidden


@pytest.mark.parametrize(
    "title,admitted",
    [
        ("**Alpha**", True),
        ("_Alpha_", True),
        ("`Alpha`", True),
        ("&amp;Alpha;", True),
        ("<b>Alpha</b>", False),
        ("<code>Alpha</code>", False),
    ],
)
@pytest.mark.parametrize("wildcard", [False, True])
@pytest.mark.parametrize("fields", [False, True])
def test_native_html_literal_names_do_not_gain_markdown_semantics(
    title, admitted, wildcard, fields
):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["*"] if wildcard else ["example.test"],
        "entry_path": r"/item/\d+",
        "record_name_exclusions": ["Alpha"],
    }
    if fields:
        group["required_fields"] = [{"id": "price", "kind": "price"}]
    artifact = '<article><a href="/item/1">' + title + "</a><p>Price: $12</p></article>"
    metrics = score_attempt(
        composite_case(group), artifact.encode(), ok=True, metadata={"answer_format": "html"}
    )
    assert task_passed(True, metrics, "body") is admitted
    if admitted:
        assert metrics["composite_group_results"][0]["records"][0]["title"] == html.unescape(title)


@pytest.mark.parametrize("inside,expected", [("**Alpha**", True), ("Alpha", False)])
@pytest.mark.parametrize("outside_last", [False, True])
@pytest.mark.parametrize("fields", [False, True])
def test_native_name_admission_uses_the_scoped_occurrence(inside, expected, outside_last, fields):
    group = {
        "id": "items",
        "kind": "named_link",
        "minimum_records": 1,
        "hosts": ["example.test"],
        "entry_path": r"/item/\d+",
        "record_name_exclusions": ["Alpha"],
        "start_pattern": r"^# Catalog$",
        "end_pattern": r"^# End$",
    }
    if fields:
        group["required_fields"] = [{"id": "price", "kind": "price"}]
    outside = "Alpha" if expected else "**Alpha**"

    def card(title):
        return '<article><a href="/item/1">' + title + "</a><p>Price: $12</p></article>"

    selected = "<h1>Catalog</h1>" + card(inside) + "<h1>End</h1>"
    artifact = selected + card(outside) if outside_last else card(outside) + selected
    metrics = score_attempt(
        composite_case(group), artifact.encode(), ok=True, metadata={"answer_format": "html"}
    )
    assert task_passed(True, metrics, "body") is expected
