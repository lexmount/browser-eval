import hashlib
import json
import zipfile
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
                    "model": "test-model",
                    "environment_prepare_receipt": "required"
                    if require_prepare_receipt
                    else "legacy-unavailable",
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
    assert review["attempts"][0]["model"] == "test-model"
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


def test_review_binds_application_probe_and_rejects_help_only_gitlab(tmp_path: Path) -> None:
    _write_fixture(tmp_path, require_prepare_receipt=True)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["protocol"]["environment_prepare_receipt"] = "application_probe_v2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path = tmp_path / "chromium/44/attempt-1/environment_prepare.json"
    receipt = {
        "reset_requested": True,
        "status": "ready",
        "required_sites": ["gitlab"],
        "application_readiness": {
            "gitlab": {
                "site": "gitlab",
                "probe_path": "/explore?visibility_level=20",
                "http_statuses": [200, 200, 200],
                "required_consecutive_responses": 3,
            }
        },
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    verified = review_results(evidence=tmp_path, selection_sha256="selection-sha")
    assert verified["unresolved"] == []
    assert verified["attempts"][0]["checks"]["application_readiness_binding"]

    receipt["application_readiness"]["gitlab"]["probe_path"] = "/help"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    rejected = review_results(evidence=tmp_path, selection_sha256="selection-sha")
    assert rejected["unresolved"][0]["failed_checks"] == ["application_readiness_binding"]


def test_review_binds_raw_and_evaluated_hars_to_wire_witness(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["protocol"]["server_wire_witness"] = "required"
    manifest_path.write_text(json.dumps(manifest))
    attempts_path = tmp_path / "attempts.jsonl"
    row = json.loads(attempts_path.read_text())
    raw = tmp_path / "chromium/44/attempt-1/network-cdp.har"
    raw.write_text(json.dumps({"log": {"entries": []}}))
    evaluated = Path(row["paths"]["network_har"])
    evaluated.write_bytes(raw.read_bytes())
    witness = raw.parent / "network-wire-witness.json"
    source = raw.parent / "network-wire-source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("0-gitlab.log", b"")
    witness.write_text(json.dumps({
        "schema": "lex_browser_use_server_wire_witness/1",
        "raw_har_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "evaluated_har_sha256": hashlib.sha256(evaluated.read_bytes()).hexdigest(),
        "source_archive_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "sources": [{
            "archive_member": "0-gitlab.log",
            "origin": "http://site.test",
            "segment_sha256": hashlib.sha256(b"").hexdigest(),
            "segment_bytes": 0,
            "parsed_request_count": 0,
        }],
        "unique_header_additions": [],
    }))
    row["paths"].update(
        network_har_raw=str(raw), network_witness=str(witness), network_wire_source=str(source)
    )
    attempts_path.write_text(json.dumps(row) + "\n")
    assert review_results(evidence=tmp_path, selection_sha256="selection-sha")["unresolved"] == []

    manifest["environment"]["site_access_host"] = "192.168.5.2"
    manifest_path.write_text(json.dumps(manifest))
    receipt = json.loads(witness.read_text())
    receipt["schema"] = "lex_browser_use_server_wire_witness/2"
    receipt["gateway_host"] = "192.168.5.2"
    receipt["sources"][0]["origin"] = "http://192.168.5.2:8023"
    witness.write_text(json.dumps(receipt))
    assert review_results(evidence=tmp_path, selection_sha256="selection-sha")["unresolved"] == []

    receipt["gateway_host"] = "192.168.5.3"
    witness.write_text(json.dumps(receipt))
    assert review_results(evidence=tmp_path, selection_sha256="selection-sha")["unresolved"][0][
        "failed_checks"
    ] == ["wire_witness_binding"]
    receipt["gateway_host"] = "192.168.5.2"
    witness.write_text(json.dumps(receipt))

    evaluated.write_text('{"tampered":true}')
    review = review_results(evidence=tmp_path, selection_sha256="selection-sha")
    assert review["unresolved"][0]["failed_checks"] == ["wire_witness_binding"]

    evaluated.write_bytes(raw.read_bytes())
    witness.write_bytes(b"\xff")
    review = review_results(evidence=tmp_path, selection_sha256="selection-sha")
    assert "wire_witness_binding" in review["unresolved"][0]["failed_checks"]
