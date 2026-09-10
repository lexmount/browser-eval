import json
from pathlib import Path

from harness.browser_use.review import review_results, write_review


def _write_fixture(
    root: Path, *, row_score: float = 1.0, require_prepare_receipt: bool = False
) -> None:
    attempt_dir = root / "chromium" / "44" / "attempt-1"
    attempt_dir.mkdir(parents=True)
    response = {"task_type": "NAVIGATE", "status": "SUCCESS", "retrieved_data": None}
    evaluation = {
        "status": "success",
        "score": 1.0,
        "webarena_verified_version": "1.2.3",
        "webarena_verified_evaluator_checksum": "eval-sha",
        "webarena_verified_data_checksum": "data-sha",
    }
    paths = {
        "network_har": attempt_dir / "network.har",
        "agent_trace": attempt_dir / "agent_trace.zip",
        "response": attempt_dir / "agent_response.json",
        "evaluation": attempt_dir / "eval_result.json",
        "events": attempt_dir / "agent_events.json",
    }
    if require_prepare_receipt:
        paths["environment_prepare"] = attempt_dir / "environment_prepare.json"
    paths["network_har"].write_text("{}", encoding="utf-8")
    paths["agent_trace"].write_bytes(b"trace")
    paths["response"].write_text(json.dumps(response), encoding="utf-8")
    paths["evaluation"].write_text(json.dumps(evaluation), encoding="utf-8")
    paths["events"].write_text("[]", encoding="utf-8")
    if require_prepare_receipt:
        paths["environment_prepare"].write_text(
            json.dumps({"reset_requested": True, "status": "ready"}), encoding="utf-8"
        )
    row = {
        "engine": "chromium",
        "task_id": 44,
        "attempt": 1,
        "score": row_score,
        "status": "success",
        "agent_response": response,
        "model_usage": {"request_count": 2, "request_attempt_count": 2},
        "model_transport_errors": [],
        "paths": {name: str(path) for name, path in paths.items()},
    }
    (root / "attempts.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "selection_sha256": "selection-sha",
                "protocol": {
                    "environment_prepare_receipt": "required"
                    if require_prepare_receipt
                    else "legacy-unavailable"
                },
                "environment": {
                    "official_evaluator": {
                        "evaluator_checksum": "eval-sha",
                        "data_checksum": "data-sha",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_review_verifies_every_artifact_and_binding(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    path = write_review(evidence=tmp_path, selection_sha256="selection-sha")
    review = json.loads(path.read_text(encoding="utf-8"))

    assert review["coverage"] == {"recorded_attempts": 1, "reviewed_attempts": 1}
    assert review["disagreements"] == []
    assert review["unresolved"] == []
    assert review["attempts"][0]["outcome"] == "verified"
    assert set(review["attempts"][0]["artifacts"]) == {
        "network_har",
        "agent_trace",
        "response",
        "evaluation",
        "events",
    }


def test_review_never_adjudicates_score_disagreement(tmp_path: Path) -> None:
    _write_fixture(tmp_path, row_score=0.0)

    review = review_results(evidence=tmp_path, selection_sha256="selection-sha")

    assert review["attempts"][0]["outcome"] == "unresolved"
    assert review["disagreements"][0]["failed_checks"] == ["score_matches_evaluator"]
    assert review["unresolved"][0]["failed_checks"] == ["score_matches_evaluator"]


def test_review_requires_declared_environment_prepare_receipt(tmp_path: Path) -> None:
    _write_fixture(tmp_path, require_prepare_receipt=True)

    review = review_results(evidence=tmp_path, selection_sha256="selection-sha")

    assert review["unresolved"] == []
    assert "environment_prepare" in review["attempts"][0]["artifacts"]
