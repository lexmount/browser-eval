"""Build a redacted, hash-bound packet for proposing one WebArena trace repair.

The packet is deliberately not a qualification record.  It gives a repair
author the public task, the recorded trajectory, evaluator *shape*, and paths
to the immutable browser evidence, while withholding answer-bearing
``AgentResponseEvaluator.expected.retrieved_data``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.public_webarena.qualification import task_contract_sha256

PACKET_SCHEMA = "lex_webarena_codex_repair_packet/1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_binding(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"repair evidence file is missing: {resolved}")
    raw = resolved.read_bytes()
    return {
        "path": str(resolved),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _contained_evidence_path(directory: Path, name: Any, label: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"final-page {label} path is missing")
    path = (directory / name).resolve()
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise ValueError(f"final-page {label} path escapes evidence directory") from exc
    return path


def _redacted_evaluator_contract(task: dict[str, Any]) -> tuple[list[Any], list[str]]:
    contracts = deepcopy((task.get("source") or {}).get("verified_evaluators") or [])
    redactions: list[str] = []
    for index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            continue
        if contract.get("evaluator") != "AgentResponseEvaluator":
            continue
        expected = contract.get("expected")
        if isinstance(expected, dict) and "retrieved_data" in expected:
            expected.pop("retrieved_data")
            redactions.append(f"task.evaluator_contracts[{index}].expected.retrieved_data")
    return contracts, redactions


def build_packet(
    suite_path: str | Path,
    attempt_path: str | Path,
) -> dict[str, Any]:
    """Return one immutable-input repair packet after verifying every binding."""
    suite_file = Path(suite_path).resolve()
    attempt_file = Path(attempt_path).resolve()
    if not suite_file.is_file() or not attempt_file.is_file():
        raise ValueError("suite and attempt JSON files are required")
    suite_raw = suite_file.read_bytes()
    attempt_raw = attempt_file.read_bytes()
    suite = json.loads(suite_raw)
    attempt = json.loads(attempt_raw)
    task_id = str(attempt.get("task_id") or "").strip()
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if not task_id or len(matches) != 1:
        raise ValueError("attempt must identify exactly one suite task")
    task = matches[0]
    evidence_directory = Path(str(attempt.get("evidence_directory") or "")).resolve()
    if not evidence_directory.is_dir() or attempt_file.parent != evidence_directory:
        raise ValueError("attempt path must be inside its declared evidence directory")

    final_page = attempt.get("final_page_evidence") or {}
    if final_page.get("status") != "complete":
        raise ValueError("repair packet requires complete final-page evidence")
    final_bindings: dict[str, Any] = {}
    for key in ("screenshot", "page"):
        metadata = final_page.get(key) or {}
        path = _contained_evidence_path(evidence_directory, metadata.get("path"), key)
        binding = _file_binding(path)
        declared = str(metadata.get("sha256") or "")
        if not _SHA256_RE.fullmatch(declared) or binding["sha256"] != declared:
            raise ValueError(f"declared final-page {key} sha256 does not match evidence")
        final_bindings[key] = binding
    har_binding = _file_binding(evidence_directory / "network.har")

    evaluator_contracts, redactions = _redacted_evaluator_contract(task)
    safe_task = {
        "id": task.get("id"),
        "title": task.get("title"),
        "difficulty": task.get("difficulty"),
        "tags": deepcopy(task.get("tags") or []),
        "trajectory": deepcopy(task.get("trajectory")),
        "evaluator": deepcopy(task.get("evaluator")),
        "environment": deepcopy(task.get("environment")),
        "evaluator_contracts": evaluator_contracts,
    }
    # Evaluator diagnostics may echo expected answers.  Keep the process trace
    # (which is browser-observed evidence), but never copy the evaluate_result
    # row, validation payload, or evaluator error into the authoring packet.
    process_trace = [
        deepcopy(row)
        for row in (attempt.get("trace") or [])
        if isinstance(row, dict) and row.get("action") != "evaluate_result"
    ]
    attempt_summary = {
        key: deepcopy(attempt.get(key))
        for key in (
            "engine",
            "task_id",
            "attempt",
            "is_retry",
            "qualification_run",
            "status",
            "process_status",
            "result_status",
            "combined_status",
            "evidence_status",
            "process_validation",
        )
        if key in attempt
    }
    attempt_summary["process_trace"] = process_trace
    return {
        "schema": PACKET_SCHEMA,
        "purpose": "candidate_trajectory_repair_only",
        "qualification_effect": "none_until_independent_chromium_replay_passes",
        "bindings": {
            "suite_sha256": hashlib.sha256(suite_raw).hexdigest(),
            "task_id": task_id,
            "task_sha256": _json_sha256(task),
            "task_contract_sha256": task_contract_sha256(task),
            "attempt_sha256": hashlib.sha256(attempt_raw).hexdigest(),
            "final_page": final_bindings,
            "network_har": har_binding,
        },
        "task": safe_task,
        "attempt": attempt_summary,
        "evidence_references": {
            "attempt": str(attempt_file),
            "screenshot": final_bindings["screenshot"]["path"],
            "final_page": final_bindings["page"]["path"],
            "network_har": har_binding["path"],
        },
        "redactions": {
            "paths": redactions,
            "reason": "withhold AgentResponseEvaluator reference answers from repair author",
        },
        "proposal_contract": {
            "schema": "lex_browser_use_repair_proposal/1",
            "validator": "scripts.build_webarena_repair_probe.build_probe",
            "qualification_status": "proposal_only",
        },
    }


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite a different repair packet: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _write_immutable(args.output, build_packet(args.suite, args.attempt))


if __name__ == "__main__":
    main()
