"""Post-call source evidence must keep its own identity and completion receipt."""
import hashlib
import json
from copy import deepcopy

import pytest

from harness.web_fetch.reference_lifecycle import build_observations


@pytest.fixture
def inputs(tmp_path):
    scope = {"case_id": "page", "url": "https://example.test/page"}
    binding = {"scope_identity": scope, "source_verification": {"observation_route": "http"}}
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps({"reference_checks": {"cases": {"page": binding}}}))
    captures = tmp_path / "captures"
    folder = captures / "page"
    folder.mkdir(parents=True)
    text = "Independent complete source"
    (folder / "reference.txt").write_text(text)
    row = {
        "case_id": "page", "url": scope["url"], "scope_identity": scope,
        "independent": True, "source": "independent HTTP GET (no provider output)",
        "status": 200, "state": "captured", "missing_facts": [], "quality_issues": [],
        "checked_at": "2026-10-09T01:00:00+00:00",
        "reference_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "completion": {"satisfied": True, "elapsed_ms": 81, "timeout_ms": 120000},
    }
    return run, captures, row, binding


def save_capture(captures, row):
    (captures / "page" / "evidence.json").write_text(json.dumps(row))


def test_http_capture_retains_measured_completion_and_content_hash(inputs):
    run, captures, row, _ = inputs
    save_capture(captures, row)
    observed = build_observations(run, captures)[0]
    assert observed["completion"] == row["completion"]
    assert observed["content_sha256"] == row["reference_sha256"]
    assert observed["scope_identity"] == row["scope_identity"]


@pytest.mark.parametrize("field,value", [
    ("case_id", "other"), ("url", "https://example.test/other"),
    ("scope_identity", {}), ("independent", False), ("state", "needs_review"),
    ("quality_issues", ["whole_body_requires_scope_review"]),
    ("reference_sha256", "0" * 64), ("completion", None),
    ("completion", {"satisfied": True, "elapsed_ms": 121000, "timeout_ms": 120000}),
])
def test_http_capture_cannot_be_upgraded_into_valid_evidence(inputs, field, value):
    run, captures, row, _ = inputs
    row[field] = value
    save_capture(captures, row)
    with pytest.raises(ValueError):
        build_observations(run, captures)


@pytest.mark.parametrize("field,value", [
    ("independent", False), ("scope_identity", {}), ("case_id", "other"),
    ("completion", None),
])
def test_browser_observation_is_validated_without_repair(inputs, field, value):
    run, captures, row, _ = inputs
    row["content_text"] = "Independent complete source"
    row[field] = value
    path = run / "browser.json"
    path.write_text(json.dumps({"page": row}))
    with pytest.raises(ValueError):
        build_observations(run, captures, path)


def test_duplicate_browser_observations_are_rejected(inputs):
    run, captures, row, _ = inputs
    row["content_text"] = "Independent complete source"
    path = run / "browser.json"
    path.write_text(json.dumps([row, deepcopy(row)]))
    with pytest.raises(ValueError):
        build_observations(run, captures, path)


def test_valid_browser_observation_is_preserved(inputs):
    run, captures, row, _ = inputs
    row["content_text"] = "Independent complete source"
    path = run / "browser.json"
    path.write_text(json.dumps([row]))
    assert build_observations(run, captures, path) == [row]


def test_browser_required_scope_rejects_http_substitution(inputs):
    run, captures, row, binding = inputs
    binding["source_verification"]["observation_route"] = "browser"
    (run / "manifest.json").write_text(
        json.dumps({"reference_checks": {"cases": {"page": binding}}}))
    save_capture(captures, row)
    with pytest.raises(ValueError):
        build_observations(run, captures)


@pytest.mark.parametrize("name", ["evidence.json", "reference.txt"])
def test_symlinked_capture_evidence_is_rejected(inputs, name):
    run, captures, row, _ = inputs
    save_capture(captures, row)
    path = captures / "page" / name
    target = captures / ("retained-" + name)
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="Symlink"):
        build_observations(run, captures)


def test_cli_rejects_invalid_input_before_creating_output(inputs):
    from harness.web_fetch.reference_lifecycle import observations_main

    run, captures, row, _ = inputs
    row["independent"] = False
    save_capture(captures, row)
    output = run / "postchecks.json"
    with pytest.raises(ValueError):
        observations_main(["--run-dir", str(run), "--captures", str(captures),
                           "--output", str(output)])
    assert not output.exists()
