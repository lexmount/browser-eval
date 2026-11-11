"""Primary content keeps body and record requirements while excluding page shell."""

import json
import subprocess
import sys
from copy import deepcopy

import pytest

from lexbench_webfetch.content_scopes import select_content_scope
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def annotated_case():
    main = {
        "task_prompt": "Return the complete article body.",
        "required_facts": [{"id": "body", "alternatives": ["Complete article body"]}],
        "min_content_chars": 20,
    }
    full = {
        **deepcopy(main),
        "task_prompt": "Return body and page footer.",
        "required_facts": main["required_facts"]
        + [{"id": "footer", "alternatives": ["Contact publisher"]}],
    }
    return {
        "id": "article",
        "name": "Article",
        "category": "synthetic",
        "url": "https://example.test/article",
        "expected_any": [],
        **deepcopy(full),
        "content_scopes": {
            "version": 1,
            **{
                name: {
                    "requirements": requirements,
                    "evidence": {"scope": name, "source_sha256": "a" * 64},
                }
                for name, requirements in [("main", main), ("full", full)]
            },
        },
    }


@pytest.mark.parametrize(
    "answer,main,full",
    [
        ("Complete article body", True, False),
        ("Contact publisher", False, False),
        ("Complete article body\nContact publisher", True, True),
        ("", False, False),
    ],
)
def test_main_full_keep_the_same_body(answer, main, full):
    c = annotated_case()
    original = deepcopy(c)
    for scope, expected in [("main", main), ("full", full)]:
        result = evaluate_response(c, answer.encode(), content_scope=scope)
        assert result["passed"] is expected
        assert result["metrics"]["evaluation_content_scope"] == scope
    assert c == original


def test_cli_scoring_accepts_explicit_scope_and_rejects_unannotated_tasks(tmp_path):
    task = tmp_path / "task.json"
    response = tmp_path / "answer.txt"
    task.write_text(json.dumps(annotated_case()))
    response.write_text("Complete article body")
    command = [
        sys.executable,
        "-m",
        "lexbench_webfetch.cli",
        "score",
        "--task",
        str(task),
        "--response",
        str(response),
        "--content-scope",
        "main",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"] is True
    c = annotated_case()
    del c["content_scopes"]
    task.write_text(json.dumps(c))
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode != 0
    assert "annotated main and full" in result.stderr


def test_scopes_cannot_change_navigation_or_accept_challenge_pages():
    c = annotated_case()
    c["content_scopes"]["main"]["requirements"]["url"] = "https://other.test/"
    with pytest.raises(ValueError):
        select_content_scope(c, "main")
    c = annotated_case()
    c["content_scopes"]["main"]["requirements"]["source_benchmark"] = {"mode": "different"}
    with pytest.raises(ValueError, match="navigation identity"):
        select_content_scope(c, "main")
    c = annotated_case()
    c["content_scopes"]["main"]["requirements"]["forbidden_any"] = ["Access denied"]
    with pytest.raises(ValueError, match="blocked-page"):
        select_content_scope(c, "main")


@pytest.mark.parametrize(
    "answer,expected",
    [
        (
            "## Installation\nInstall the package with the documented command.\n## Usage\nRun the example application.",
            True,
        ),
        (
            "Installation Install the package with the documented command. Usage Run the example application.",
            False,
        ),
        ("## Installation\n## Usage\nRun the example application.", False),
        (
            "<main><h2>Installation</h2><p>Install the package with the documented command.</p><h2>Usage</h2><p>Run the example application.</p></main>",
            True,
        ),
        (
            "<nav><h2>Installation</h2><p>Install the package with the documented command.</p><h2>Usage</h2><p>Run the example application.</p></nav>",
            False,
        ),
    ],
)
def test_main_keeps_document_sections(answer, expected):
    c = annotated_case()
    for name in ["main", "full"]:
        c["content_scopes"][name]["requirements"] = {
            "task_prompt": "Return complete installation and usage sections.",
            "document_structure": {
                "version": 1,
                "minimum_section_chars": 10,
                "minimum_heading_coverage": 1.0,
                "minimum_body_chars": 1,
                "headings": [{"text": "Installation", "level": 2}, {"text": "Usage", "level": 2}],
            },
        }
    metrics = score_attempt(c, answer.encode(), ok=True, content_scope="main")
    assert task_passed(True, metrics) is expected


def test_public_definitions_preserve_both_scopes_without_publishing_reference_bodies():
    import hashlib

    from lexbench_webfetch.benchmark import public_task

    c = annotated_case()
    private = "Complete licensed reference text, kept outside the public dataset."
    for scope in ("main", "full"):
        requirements = c["content_scopes"][scope]["requirements"]
        requirements["ground_truth_main_content"] = private
        requirements["body_requirements"] = {
            "version": 3,
            "regions": [{"id": "body", "text": private}],
        }
    public = public_task(c)
    assert private not in json.dumps(public)
    for scope in ("main", "full"):
        requirement = public["content_scopes"][scope]["requirements"]
        assert "ground_truth_main_content" not in requirement
        assert (
            requirement["body_requirements"]["regions"][0]["text_binding"]["sha256"]
            == hashlib.sha256(private.encode()).hexdigest()
        )


@pytest.mark.parametrize(
    "identifier",
    [
        "main-product-full-title-without-shopping-controls",
        "main-product-wrong-record-price",
        "main-product-missing-owned-availability",
        "main-product-negated-availability",
        "reference-domain-current-complete-notice",
        "reference-domain-original-complete-notice",
        "reference-domain-missing-notice",
    ],
)
def test_primary_product_controls_use_the_public_scorer(identifier):
    from importlib.resources import files

    registry = json.loads(
        files("harness.benchmarks").joinpath("scoring-regressions.json").read_bytes()
    )
    fixture = next(row for row in registry["cases"] if row["id"] == identifier)
    metrics = score_attempt(fixture["case"], fixture["artifact"].encode(), ok=fixture["ok"])
    assert task_passed(fixture["ok"], metrics) is fixture["expected_task_pass"]
    for key, expected in fixture["expected_metrics"].items():
        assert metrics[key] == expected
