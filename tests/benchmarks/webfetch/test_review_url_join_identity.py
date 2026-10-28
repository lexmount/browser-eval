"""Relative URL joining preserves reserved bytes before record and resource scoring."""

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def cases():
    for wildcard in [False, True]:
        for fields in [False, True]:
            group = {
                "id": "items",
                "kind": "named_link",
                "minimum_records": 2,
                "hosts": ["*"] if wildcard else ["example.test"],
                "entry_path": r"/item/part(?:;|%3B)",
            }
            if fields:
                group["required_fields"] = [{"id": "price", "kind": "price"}]
            case = {
                "url": "https://example.test/catalog",
                "task_kind": "body",
                "min_content_chars": 1,
                "composite_acceptance": {"version": 1, "groups": [group]},
            }
            for second, passed in [("%3B", True), (";", False)]:
                artifact = "[Alpha](/item/part;)\n$12\n\n[Beta](/item/part" + second + ")\n$24"
                yield (
                    ("wildcard" if wildcard else "ordinary")
                    + "-"
                    + str(fields)
                    + "-"
                    + str(passed),
                    case,
                    artifact,
                    passed,
                )
    for expected in [";", "%3B"]:
        case = {
            "url": "https://example.test/catalog",
            "task_kind": "resource",
            "content_score_applicable": False,
            "expected_resources": [
                {"id": "file", "urls": ["https://example.test/item/part" + expected]}
            ],
        }
        for actual in [";", "%3B", ""]:
            yield (
                "resource-"
                + ("literal" if expected == ";" else "encoded")
                + "-"
                + ("missing" if not actual else "literal" if actual == ";" else "encoded"),
                case,
                "[File](/item/part" + actual + ")",
                actual == expected,
            )


CASES = list(cases())


@pytest.mark.parametrize("id,case,artifact,expected", CASES, ids=[row[0] for row in CASES])
def test_preserve_terminal_empty_path_parameters(id, case, artifact, expected):
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert task_passed(True, metrics, case["task_kind"]) is expected


@pytest.mark.parametrize("valid", [False, True])
def test_malformed_auxiliary_resource_does_not_abort_valid_delivery(valid):
    case = {
        "url": "https://example.test/catalog",
        "task_kind": "resource",
        "content_score_applicable": False,
        "expected_resources": [{"id": "file", "urls": ["https://example.test/file.png"]}],
    }
    artifact = "[Malformed](http://[bad])"
    if valid:
        artifact += "\n[File](/file.png)"
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert task_passed(True, metrics, "resource") is valid


@pytest.mark.parametrize("surface", ["html", "markdown", "metadata"])
@pytest.mark.parametrize("literal_uri", [False, True])
def test_resource_uri_values_decode_entities_only_at_the_syntax_boundary(surface, literal_uri):
    target = "https://example.test/image&amp;/detail"
    case = {
        "url": "https://example.test/catalog",
        "task_kind": "resource",
        "content_score_applicable": False,
        "expected_resources": [{"urls": [target]}],
    }
    metadata = None
    if surface == "metadata":
        artifact = ""
        metadata = {"images": [{"url": target if literal_uri else target.replace("&amp;", "&")}]}
    else:
        destination = "/image&amp;amp;/detail" if literal_uri else "/image&amp;/detail"
        artifact = (
            '<a href="' + destination + '">Image</a>'
            if surface == "html"
            else "[Image](" + destination + ")"
        )
    metrics = score_attempt(case, artifact.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics, "resource") is literal_uri
    assert metrics["resource_candidate_urls"] == [
        target if literal_uri else target.replace("&amp;", "&")
    ]
