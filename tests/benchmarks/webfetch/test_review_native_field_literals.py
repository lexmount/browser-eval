"""Generated link syntax cannot reinterpret literal native field values."""

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case

VALUES = [
    ("**Ready**", r"\*\*Ready\*\*", True),
    ("**Ready**", "Ready", False),
    ("<b>Ready</b>", "Ready", True),
    ("<b>Ready</b>", r"\*\*Ready\*\*", False),
    ("&lt;b&gt;Ready&lt;/b&gt;", "<b>Ready</b>", True),
    ("&lt;b&gt;Ready&lt;/b&gt;", "Ready", False),
    ("&amp;copy; Ready", "&copy; Ready", True),
    ("&amp;copy; Ready", "© Ready", False),
    ("&copy; Ready", "© Ready", True),
    ("&copy; Ready", "&copy; Ready", False),
    ("&lt;span hidden&gt;Ready&lt;/span&gt;", "<span hidden>Ready</span>", True),
    ("&lt;span hidden&gt;Ready&lt;/span&gt;", "Ready", False),
    ("[Ready](/visible-literal)", r"\[Ready\]\(/visible-literal\)", True),
    ("[Ready](/visible-literal)", "Ready", False),
]


def native_case(pattern):
    return composite_case(
        {
            "id": "items",
            "kind": "named_link",
            "minimum_records": 1,
            "hosts": ["example.test"],
            "entry_path": r"/item/\d+",
            "required_fields": [
                {"id": "status", "kind": "token", "aliases": ["Status"], "pattern": pattern}
            ],
        }
    )


@pytest.mark.parametrize("value,pattern,expected", VALUES)
@pytest.mark.parametrize("shape", ["article", "table"])
def test_native_field_values_remain_literal(value, pattern, expected, shape):
    link = '<a href="/item/1">Example</a>'
    artifact = (
        ("<article>" + link + "<p>Status:" + value + "</p></article>")
        if shape == "article"
        else ("<table><tr><td>" + link + "</td><td>Status:" + value + "</td></tr></table>")
    )
    metrics = score_attempt(
        native_case(pattern), artifact.encode(), ok=True, metadata={"answer_format": "html"}
    )
    assert task_passed(True, metrics, "body") is expected
    if expected:
        from bs4 import BeautifulSoup

        assert (
            BeautifulSoup(value, "html.parser").get_text()
            in metrics["composite_group_results"][0]["records"][0]["context"]
        )


@pytest.mark.parametrize("entity", [False, True])
@pytest.mark.parametrize("literal,expected", [(True, True), (False, False)])
def test_native_literal_shield_preserves_existing_private_characters(entity, literal, expected):
    left, right = "\U000f0000", "\U000f0001"
    value = ("&#983040;" if entity else left) + "**Ready**" + ("&#983041;" if entity else right)
    pattern = left + (r"\*\*Ready\*\*" if literal else "Ready") + right
    artifact = '<article><a href="/item/1">Example</a><p>Status:' + value + "</p></article>"
    metrics = score_attempt(
        native_case(pattern), artifact.encode(), ok=True, metadata={"answer_format": "html"}
    )
    assert task_passed(True, metrics, "body") is expected


@pytest.mark.parametrize("representation", [None, "markdown", "html"])
@pytest.mark.parametrize("literal", [False, True])
def test_mixed_markdown_root_text_keeps_its_own_format(representation, literal):
    artifact = '<a href="/item/1">Example</a>\nStatus:**Ready**'
    pattern = r"\*\*Ready\*\*" if literal else "Ready"
    metadata = {"answer_format": representation} if representation else None
    metrics = score_attempt(native_case(pattern), artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, "body") is (literal == (representation == "html"))
