import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.participants import bind_participant, participant_registry
from harness.trajectory_replay.webarena_review import (
    load_validated_failure_review,
    programmatic_failure,
)


def test_participant_identity_preserves_engine_and_rejects_ambiguous_legacy() -> None:
    methods = [{"id": "moli-old", "engine": "moli"}, {"id": "moli-fixed", "engine": "moli"}]
    registry = participant_registry(methods)
    row = {"participant_id": "moli-fixed", "engine": "moli"}
    assert bind_participant(row, registry) == row
    with pytest.raises(ValueError, match="ambiguous"):
        bind_participant({"engine": "moli"}, registry)
    with pytest.raises(ValueError, match="disagrees"):
        bind_participant({"participant_id": "moli-old", "engine": "chromium"}, registry)
    assert bind_participant({"engine": "moli"}, participant_registry(methods[:1])) == {
        "engine": "moli",
        "participant_id": "moli-old",
    }


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_programmatic_failure_keeps_location_separate_from_root_cause() -> None:
    result = programmatic_failure(
        {
            "process_status": "fail",
            "error": "RuntimeError: step 7 failed: Click failed: target missing\n",
        }
    )
    assert result == {
        "stage": "browser_tool_execution",
        "step": 7,
        "message": "Click failed: target missing",
    }


def test_historical_task173_review_supersedes_browser_attribution_with_bound_evidence() -> None:
    review = json.loads(Path("research/trajectory-replay-reviews/20260917-original-36.json").read_text())
    matches = [
        (group, row)
        for group in review["cause_groups"]
        for row in group["results"]
        if row["task_id"] == 173
    ]
    assert len(matches) == 1
    group, row = matches[0]
    assert group["responsibility"] == "benchmark"
    assert group["code"] == "shared_viewport_configuration"
    assert "separate navigation observation error" in row["reason_en"]
    revision = next(
        item for item in review["review_revisions"] if item["id"] == row["review_revision"]
    )
    assert revision["superseded_review"]["responsibility"] == "browser"
    assert (
        revision["evidence_bindings"]["effective_results_sha256"]
        == review["bindings"]["effective_results_sha256"]
    )
    observations = revision["historical_trace_observations"]
    assert observations["css_rule"] == ".d-lg-none{display:none !important}"
    assert len(observations["artifacts"]) == 4
    assert {item["viewport"]["width"] for item in observations["artifacts"]} == {780, 1920}


def test_review_requires_exact_hash_bound_failure_coverage(tmp_path: Path) -> None:
    results = _write(
        tmp_path / "results.json",
        {
            "engines": ["chromium", "moli"],
            "attempts": [
                {"engine": "chromium", "task_id": 1, "status": "pass"},
                {"engine": "moli", "task_id": 1, "status": "fail"},
            ],
        },
    )
    review = _write(
        tmp_path / "review.json",
        {
            "schema": "lex_webarena_trajectory_replay_failure_review/1",
            "bindings": {
                "effective_results_sha256": hashlib.sha256(results.read_bytes()).hexdigest()
            },
            "coverage": {"failed_results": 1, "verified_results": 1},
            "cause_groups": [
                {
                    "code": "browser",
                    "label_zh": "浏览器差异",
                    "label_en": "Browser difference",
                    "responsibility": "browser",
                    "remediation_owner": "browser",
                    "remediation_status": "open",
                    "results": [
                        {
                            "engine": "moli",
                            "task_id": 1,
                            "reason_zh": "目标不存在。",
                            "reason_en": "Target missing.",
                        }
                    ],
                }
            ],
        },
    )
    loaded = load_validated_failure_review(results, review)
    assert set(loaded["by_result"]) == {("moli", 1)}

    payload = json.loads(review.read_text(encoding="utf-8"))
    payload["cause_groups"][0]["results"][0]["task_id"] = 2
    review.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="coverage mismatch"):
        load_validated_failure_review(results, review)
