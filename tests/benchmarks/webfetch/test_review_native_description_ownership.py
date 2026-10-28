"""Composite descriptions keep native record containers through scope selection."""

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


def description_case(minimum=1, scoped=False):
    group = {"id": "items", "kind": "name_description", "minimum_records": minimum}
    if scoped:
        group.update(start_pattern=r"^# Catalog$", end_pattern=r"^# End$")
    return composite_case(group)


def card(name, description=None, tag="article"):
    return (
        "<"
        + tag
        + "><p>"
        + name
        + "</p>"
        + ("<p>" + description + "</p>" if description else "")
        + "</"
        + tag
        + ">"
    )


CASES = [
    ("incomplete-siblings", description_case(), card("Alpha") + card("Beta"), False),
    (
        "two-complete-siblings",
        description_case(2),
        card("Alpha", "First description") + card("Beta", "Second description"),
        True,
    ),
    (
        "one-complete-sibling",
        description_case(2),
        card("Alpha", "First description") + card("Beta"),
        False,
    ),
    (
        "one-independent-complete",
        description_case(),
        card("Alpha", "First description") + card("Beta"),
        True,
    ),
    (
        "scoped-two-complete",
        description_case(2, True),
        card("Outside")
        + "<h1>Catalog</h1>"
        + card("Alpha", "First description")
        + card("Beta", "Second description")
        + "<h1>End</h1>"
        + card("Other"),
        True,
    ),
    (
        "outside-cannot-repair",
        description_case(1, True),
        card("Outside", "Full description")
        + "<h1>Catalog</h1>"
        + card("Alpha")
        + "<h1>End</h1>"
        + card("Other", "Other description"),
        False,
    ),
    (
        "missing-end",
        description_case(1, True),
        "<h1>Catalog</h1>" + card("Alpha", "Full description"),
        False,
    ),
    (
        "literal-native-fields",
        description_case(),
        card("**Alpha**", "&lt;b&gt;Body&lt;/b&gt;"),
        True,
    ),
    (
        "scoped-nested-container",
        description_case(2, True),
        "<section><h1>Catalog</h1>"
        + card("Alpha", "First description")
        + card("Beta", "Second description")
        + "<h1>End</h1></section>",
        True,
    ),
]


@pytest.mark.parametrize("id,case,artifact,expected", CASES, ids=[row[0] for row in CASES])
@pytest.mark.parametrize("representation", [None, "html"])
@pytest.mark.parametrize("tag", ["article", "section", "li"])
def test_native_container_description_ownership(id, case, artifact, expected, representation, tag):
    artifact = artifact.replace("<article", "<" + tag).replace("</article>", "</" + tag + ">")
    metadata = {"answer_format": representation} if representation else None
    metrics = score_attempt(case, artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, "body") is expected
    if id == "literal-native-fields" and expected:
        assert metrics["composite_group_results"][0]["records"][0]["fields"] == {
            "name": "**Alpha**",
            "description": "<b>Body</b>",
        }


def test_plain_html_syntax_does_not_deliver_native_descriptions():
    artifact = card("Alpha", "First description") + card("Beta", "Second description")
    metrics = score_attempt(
        description_case(2), artifact.encode(), ok=True, metadata={"answer_format": "plain_text"}
    )
    assert not task_passed(True, metrics, "body")
