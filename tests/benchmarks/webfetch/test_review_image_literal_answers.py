"""Typed image answers exclude literal examples without changing representation semantics."""

import json

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def cases():
    for syntax in ["![Image](/target.png)", '<img src="/target.png">']:
        for literal in ["`{}`", "```example\n{}\n```", "    {}"]:
            for representation in [None, "markdown"]:
                yield (
                    "literal-"
                    + str(syntax.startswith("!"))
                    + "-"
                    + str(len(literal))
                    + "-"
                    + str(representation),
                    literal.format(syntax),
                    representation,
                    None,
                    False,
                )
    for syntax in ["![Noise](/noise.png)", '<img src="/noise.png">']:
        for literal in ["```example\n{}\n```", "`{}`"]:
            yield (
                "noise-" + str(syntax.startswith("!")) + "-" + str(len(literal)),
                "![Target](/target.png)\n\n" + literal.format(syntax),
                "markdown",
                None,
                True,
            )
    yield (
        "real-extra-image",
        "![Target](/target.png)\n![Noise](/noise.png)",
        "markdown",
        None,
        False,
    )
    yield "real-image", "![Target](/target.png)", "markdown", None, True
    native_card = (
        "    - [![Image](/target.png)](/item/1)\n    [Image](/item/1)\nA complete description."
    )
    yield "auto-native-image-card", native_card, None, None, True
    yield "declared-indented-image-example", native_card, "markdown", None, False
    yield "html-real-backticks", '`<img src="/target.png">`', "html", None, True
    yield "auto-html-document", '<div>`<img src="/target.png">`</div>', None, None, True
    yield "native-json-html", json.dumps({"html": '`<img src="/target.png">`'}), None, None, True
    yield (
        "native-json-content-literal",
        json.dumps({"content": "`![Image](/target.png)`"}),
        None,
        None,
        False,
    )
    yield "html-markdown-literal", "<pre>![Image](/target.png)</pre>", "html", None, False
    yield "plain-markdown-literal", "![Image](/target.png)", "plain_text", None, False
    yield "plain-html-literal", '<img src="/target.png">', "plain_text", None, False
    yield "plain-url-answer", "https://example.test/target.png", "plain_text", None, True
    yield "plain-json-literal", '["https://example.test/target.png"]', "plain_text", None, False
    yield (
        "metadata-typed-image",
        "```example\n![Noise](/noise.png)\n```",
        "markdown",
        {"images": ["https://example.test/target.png"]},
        True,
    )
    yield (
        "metadata-plain-image",
        '<img src="/noise.png">',
        "plain_text",
        {"images": ["https://example.test/target.png"]},
        True,
    )


CASES = list(cases())


def image_case():
    return {
        "url": "https://example.test/gallery",
        "task_kind": "resource",
        "expected_resources": [{"id": "image", "urls": ["https://example.test/target.png"]}],
        "resource_output_contract": {
            "candidate_scope": "image_answer_fields",
            "unexpected": "reject",
        },
    }


@pytest.mark.parametrize(
    "id,artifact,representation,extra,expected", CASES, ids=[row[0] for row in CASES]
)
def test_image_answer_representation(id, artifact, representation, extra, expected):
    metadata = dict(extra or {})
    if representation:
        metadata["answer_format"] = representation
    metrics = score_attempt(image_case(), artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, "resource") is expected
