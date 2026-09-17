"""Deterministic evidence review for autonomous browser-use attempts.

This review verifies evidence integrity. It does not replace or reinterpret the
official WebArena-Verified evaluator score.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .wire_witness import validate_wire_source

REQUIRED_ARTIFACTS = ("network_har", "agent_trace", "response", "evaluation", "events")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _attempts_sha256(path: Path) -> str:
    return _sha256(path)


def _read_attempts(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def review_results(*, evidence: Path, selection_sha256: str) -> dict[str, Any]:
    """Review every recorded attempt and return a result bound to its input bytes."""
    attempts_path = evidence / "attempts.jsonl"
    manifest_path = evidence / "manifest.json"
    if not attempts_path.is_file() or not manifest_path.is_file():
        raise ValueError("result review requires attempts.jsonl and manifest.json")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("selection_sha256") != selection_sha256:
        raise ValueError("manifest does not match the pre-registered selection")

    rows = _read_attempts(attempts_path)
    model = str((manifest.get("protocol") or {}).get("model") or "")
    if not model:
        raise ValueError("manifest protocol must identify the evidence model")
    required_artifacts = list(REQUIRED_ARTIFACTS)
    prepare_contract = (manifest.get("protocol") or {}).get("environment_prepare_receipt")
    if prepare_contract in {"required", "application_probe_v2"}:
        required_artifacts.append("environment_prepare")
    require_wire_witness = (manifest.get("protocol") or {}).get("server_wire_witness") == "required"
    if require_wire_witness:
        required_artifacts.extend(("network_har_raw", "network_witness", "network_wire_source"))
    reviewed: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    expected_provenance = manifest.get("environment", {}).get("official_evaluator", {})

    for row in rows:
        row_model = str(row.get("model") or model)
        if row_model != model:
            raise ValueError("attempt model differs from the evidence manifest")
        identity = {
            "model": model,
            "engine": str(row.get("engine")),
            "task_id": int(row.get("task_id")),
            "attempt": int(row.get("attempt")),
        }
        paths = row.get("paths") or {}
        checks: dict[str, bool] = {}
        artifacts: dict[str, dict[str, Any]] = {}
        for name in required_artifacts:
            raw_path = paths.get(name)
            path = Path(str(raw_path)) if raw_path else Path()
            exists = bool(raw_path) and path.is_file()
            checks[f"{name}_exists"] = exists
            if exists:
                artifacts[name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}

        if prepare_contract == "application_probe_v2":
            try:
                preparation = json.loads(
                    Path(str(paths["environment_prepare"])).read_text(encoding="utf-8")
                )
                sites = preparation["required_sites"]
                probes = preparation["application_readiness"]
                checks["application_readiness_binding"] = (
                    preparation.get("reset_requested") is True
                    and preparation.get("status") == "ready"
                    and isinstance(sites, list)
                    and bool(sites)
                    and isinstance(probes, dict)
                    and set(sites) == set(probes)
                    and all(
                        isinstance(probe, dict)
                        and probe.get("site") == site
                        and isinstance(probe.get("probe_path"), str)
                        and probe["probe_path"].startswith("/")
                        and (
                            site != "gitlab"
                            or probe["probe_path"] == "/explore?visibility_level=20"
                        )
                        and isinstance(probe.get("required_consecutive_responses"), int)
                        and probe["required_consecutive_responses"] >= 1
                        and isinstance(probe.get("http_statuses"), list)
                        and len(probe["http_statuses"])
                        == probe["required_consecutive_responses"]
                        and all(
                            type(status) is int
                            and (status == 200 if site == "gitlab" else 200 <= status < 500)
                            for status in probe["http_statuses"]
                        )
                        for site, probe in probes.items()
                    )
                )
            except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                checks["application_readiness_binding"] = False

        if require_wire_witness:
            try:
                witness = json.loads(
                    Path(str(paths["network_witness"])).read_text(encoding="utf-8")
                )
                checks["wire_witness_binding"] = (
                    witness.get("schema") == "lex_browser_use_server_wire_witness/1"
                    and witness.get("raw_har_sha256") == artifacts["network_har_raw"]["sha256"]
                    and witness.get("evaluated_har_sha256") == artifacts["network_har"]["sha256"]
                    and witness.get("source_archive_sha256")
                    == artifacts["network_wire_source"]["sha256"]
                    and validate_wire_source(
                        json.loads(Path(str(paths["network_har_raw"])).read_text(encoding="utf-8")),
                        json.loads(Path(str(paths["network_har"])).read_text(encoding="utf-8")),
                        witness,
                        Path(str(paths["network_wire_source"])),
                    )
                )
            except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                checks["wire_witness_binding"] = False

        evaluation: dict[str, Any] = {}
        response: Any = None
        if checks["evaluation_exists"]:
            try:
                evaluation = json.loads(Path(str(paths["evaluation"])).read_text(encoding="utf-8"))
                checks["evaluation_json"] = isinstance(evaluation, dict)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                checks["evaluation_json"] = False
        else:
            checks["evaluation_json"] = False
        if checks["response_exists"]:
            try:
                response = json.loads(Path(str(paths["response"])).read_text(encoding="utf-8"))
                checks["response_json"] = True
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                checks["response_json"] = False
        else:
            checks["response_json"] = False

        checks["response_matches_record"] = checks["response_json"] and response == row.get(
            "agent_response"
        )
        checks["score_matches_evaluator"] = checks["evaluation_json"] and float(
            evaluation.get("score") or 0.0
        ) == float(row.get("score") or 0.0)
        checks["status_matches_evaluator"] = checks["evaluation_json"] and str(
            evaluation.get("status") or "error"
        ) == str(row.get("status") or "error")
        checks["official_evaluator_version_present"] = bool(
            evaluation.get("webarena_verified_version")
        )
        checks["official_evaluator_checksum_matches"] = bool(
            expected_provenance.get("evaluator_checksum")
        ) and evaluation.get("webarena_verified_evaluator_checksum") == expected_provenance.get(
            "evaluator_checksum"
        )
        checks["official_data_checksum_matches"] = bool(
            expected_provenance.get("data_checksum")
        ) and evaluation.get("webarena_verified_data_checksum") == expected_provenance.get(
            "data_checksum"
        )
        usage = row.get("model_usage") or {}
        transport_errors = row.get("model_transport_errors") or []
        checks["model_request_attempts_accounted"] = isinstance(transport_errors, list) and int(
            usage.get("request_attempt_count") or 0
        ) == int(usage.get("request_count") or 0) + len(transport_errors)

        failed = sorted(name for name, passed in checks.items() if not passed)
        item = {**identity, "outcome": "verified" if not failed else "unresolved", "checks": checks}
        item["artifacts"] = artifacts
        reviewed.append(item)
        if not checks["score_matches_evaluator"] or not checks["status_matches_evaluator"]:
            disagreements.append({**identity, "failed_checks": failed})
        if failed:
            unresolved.append({**identity, "failed_checks": failed})

    return {
        "schema": "lex_browser_use_result_review/1",
        "reviewer": {
            "name": "Browser Use evidence auditor",
            "kind": "deterministic programmatic review",
            "version": 1,
        },
        "reviewed_at": datetime.now(UTC).isoformat(),
        "bindings": {
            "attempts_sha256": _attempts_sha256(attempts_path),
            "manifest_sha256": _sha256(manifest_path),
            "selection_sha256": selection_sha256,
        },
        "protocol": {
            "scope": "evidence completeness, byte identity, and recorded score/status agreement",
            "score_authority": "unmodified WebArena-Verified evaluator",
            "manual_score_override": "not permitted",
            "adjudication": (
                "any disagreement or missing artifact remains unresolved and blocks publication"
            ),
        },
        "coverage": {"recorded_attempts": len(rows), "reviewed_attempts": len(reviewed)},
        "disagreements": disagreements,
        "unresolved": unresolved,
        "attempts": reviewed,
    }


def write_review(*, evidence: Path, selection_sha256: str) -> Path:
    review = review_results(evidence=evidence, selection_sha256=selection_sha256)
    path = evidence / "result-review.json"
    path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
