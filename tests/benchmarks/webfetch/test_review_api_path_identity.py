"""Distinct HTTP request paths retain their case in operation identity."""

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def case():
    return {
        "url": "https://example.test/",
        "acceptance_contract": {
            "version": 1,
            "schema": "api_operation",
            "minimum_records": 2,
            "stable_text_anchors": [],
        },
    }


def answer(format_name, distinct):
    paths = ["/users", "/Users" if distinct else "/users"]
    if format_name == "plain_text":
        return "\n".join(f"GET {path}\nRead account records" for path in paths)
    if format_name == "markdown":
        return "| Method | Path | Summary |\n|---|---|---|\n" + "\n".join(
            f"| GET | {path} | Read account records |" for path in paths
        )
    if format_name == "html":
        return (
            "<main>"
            + "".join(
                f"<section><h2>GET {path}</h2><p>Read account records</p></section>"
                for path in paths
            )
            + "</main>"
        )
    return (
        "<table><tr><th>Method</th><th>Path</th><th>Summary</th></tr>"
        + "".join(
            f"<tr><td>GET</td><td>{path}</td><td>Read account records</td></tr>" for path in paths
        )
        + "</table>"
    )


@pytest.mark.parametrize("format_name", ["plain_text", "markdown", "html", "html-table"])
@pytest.mark.parametrize("distinct", [False, True])
def test_api_operation_identity_preserves_path_case(format_name, distinct):
    representation = "html" if format_name == "html-table" else format_name
    metrics = score_attempt(
        case(),
        answer(format_name, distinct).encode(),
        ok=True,
        metadata={"answer_format": representation},
    )
    assert task_passed(True, metrics, "body") is distinct


def scoring_controls():
    for format_name in ["plain_text", "markdown", "html", "html-table"]:
        for distinct in [False, True]:
            yield {
                "id": f"api38-path-case-{format_name}-{'distinct' if distinct else 'repeated'}",
                "category": "structured-extraction",
                "failure_mode": "false-negative" if distinct else "false-positive",
                "root_cause": "API operation identity must preserve case-sensitive request paths.",
                "case": case(),
                "artifact": answer(format_name, distinct),
                "ok": True,
                "metadata": {
                    "answer_format": "html" if format_name == "html-table" else format_name
                },
                "expected_task_pass": distinct,
            }
