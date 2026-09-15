import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_review import (
    load_validated_failure_review,
    programmatic_failure,
)


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


def test_review_requires_exact_hash_bound_failure_coverage(tmp_path: Path) -> None:
    results = _write(
        tmp_path / "results.json",
        {
            "attempts": [
                {"engine": "chromium", "task_id": 1, "status": "pass"},
                {"engine": "moli", "task_id": 1, "status": "fail"},
            ]
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
