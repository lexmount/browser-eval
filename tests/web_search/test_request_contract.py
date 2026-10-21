"""A hash-consistent response file cannot silently change frozen API controls."""

import json
import os
from copy import deepcopy
from unittest.mock import patch

import pytest

from harness.web_search.core import digest
from harness.web_search.example import example
from harness.web_search.profiles import load_profile
from harness.web_search.report import load_run
from harness.web_search.runner import run


@pytest.fixture
def measured_run(tmp_path):
    profile = load_profile()
    profile["cases"] = profile["cases"][:1]
    profile["methods"] = [profile["methods"][1]]
    profile["protocol"].update(rounds=1, interval_s=0)
    profile["protocol"].pop("reference_scope", None)
    profile.pop("reference_checks", None)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))

    def http(url, body=None, *args, **kwargs):
        return {"http_status": 200, "elapsed_ms": 1, "transport_exit": 0, "transport_error": ""}, (
            b"reference " * 20 if body is None else b'{"results":[]}'
        )

    output = tmp_path / "run"
    with (
        patch("harness.web_search.runner.http_call", side_effect=http),
        patch("harness.web_search.runner.load_runtime_env"),
        patch.dict(os.environ, DEEPSEEK_API_KEY="fixture"),
    ):
        run(profile_path, output)
    assert load_run(output)[2]["certified"] is True
    return output


def replace_rows(output, change):
    path = output / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    change(rows[0])
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    receipt_path = output / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["results_sha256"] = digest(path.read_bytes())
    receipt_path.write_text(json.dumps(receipt))


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["request"].update(query="COMPLETELY DIFFERENT QUERY"),
        lambda r: r["request"].update(count=100),
        lambda r: r["request"].update(need_content=not r["request"]["need_content"]),
        lambda r: r.pop("request"),
        lambda r: r["request"].update(count=True),
    ],
)
def test_certified_request_changes_are_rejected_after_consistent_rehash(measured_run, change):
    replace_rows(measured_run, change)
    with pytest.raises(ValueError, match="request"):
        load_run(measured_run)


def test_physical_attempt_request_cannot_differ_from_logical_request(measured_run):
    def change(row):
        row["attempts"][0]["request"] = dict(row["request"], count=100)

    replace_rows(measured_run, change)
    with pytest.raises(ValueError, match="Physical attempt request"):
        load_run(measured_run)


def test_matching_physical_request_remains_valid(measured_run):
    replace_rows(
        measured_run, lambda row: row["attempts"][0].update(request=deepcopy(row["request"]))
    )
    assert load_run(measured_run)[2]["certified"] is True


def test_synthetic_exception_cannot_promote_evidence_to_certified(tmp_path):
    example(tmp_path)
    source = tmp_path / "fixture"
    assert load_run(source)[2]["synthetic"] is True
    path = source / "receipt.json"
    receipt = json.loads(path.read_text())
    receipt["certified"] = True
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Synthetic evidence cannot be certified"):
        load_run(source)
