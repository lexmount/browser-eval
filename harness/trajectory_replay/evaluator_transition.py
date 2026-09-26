"""Re-evaluate immutable WebArena evidence across a pinned evaluator update."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from webarena_verified.core.utils.checksum import compute_evaluator_checksum

from harness.trajectory_replay.catalog_contracts import file_sha256, validate_passing_evaluation
from harness.trajectory_replay.webarena_corrections import (
    apply_correction,
    evaluate_corrected_contract,
)
from harness.trajectory_replay.webarena_runtime_source import (
    WEBARENA_VERIFIED_FORK_URL,
    WEBARENA_VERIFIED_REVISION,
)

TRANSITION_SCHEMA = "lex_webarena_evaluator_transition/1"
RECEIPT_SCHEMA = "lex_webarena_evaluator_transition_receipt/1"


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_transition(path: Path) -> dict[str, Any]:
    transition = json.loads(path.read_text(encoding="utf-8"))
    compatible = transition.get("compatible_from_revisions")
    if (
        transition.get("schema") != TRANSITION_SCHEMA
        or transition.get("repository") != WEBARENA_VERIFIED_FORK_URL
        or transition.get("to_revision") != WEBARENA_VERIFIED_REVISION
        or transition.get("to_evaluator_checksum") != compute_evaluator_checksum()
        or not isinstance(compatible, list)
        or transition.get("from_revision") not in compatible
        or len(set(compatible)) != len(compatible)
        or any(not isinstance(value, str) or len(value) != 40 for value in compatible)
        or any(
            not isinstance(transition.get(key), str) or len(transition[key]) != 40
            for key in ("from_revision", "to_revision")
        )
        or any(
            not isinstance(transition.get(key), str) or len(transition[key]) != 64
            for key in (
                "from_evaluator_checksum",
                "from_data_checksum",
                "to_evaluator_checksum",
                "to_data_checksum",
                "fork_diff_sha256",
            )
        )
    ):
        raise ValueError("WebArena evaluator transition authority is invalid")
    return transition


def _regular_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"transition evidence file is missing: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"transition evidence file is not an object: {path.name}")
    return value


def reevaluate_pass(
    task_dir: Path,
    *,
    task_id: int,
    expected_data_checksum: str,
    benchmark: Any,
    corrections: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the current evaluation and a binding for the immutable old evidence."""

    old_evaluation_path = task_dir / "evaluation.json"
    actual_response_path = task_dir / "actual_response.json"
    network_har_path = task_dir / "network.har"
    old_evaluation = _regular_json(old_evaluation_path)
    actual_response = _regular_json(actual_response_path)
    if network_har_path.is_symlink() or not network_har_path.is_file():
        raise ValueError("transition network evidence is missing")

    official = benchmark.get_task(task_id)
    correction = (
        apply_correction(official, corrections) if task_id in corrections.records else None
    )
    result = (
        evaluate_corrected_contract(
            benchmark,
            correction,
            agent_response=actual_response,
            network_trace=network_har_path,
        )
        if correction is not None
        else benchmark.evaluate_task(
            task_id=task_id,
            agent_response=actual_response,
            network_trace=network_har_path,
        )
    )
    current = result.model_dump(mode="json")
    current["replay_assertions"] = old_evaluation.get("replay_assertions") or []
    if correction is not None:
        current["contract_correction_id"] = correction.correction_id
        current["corrected_task_sha256"] = correction.corrected_task_sha256
    validate_passing_evaluation(
        current,
        task_id=task_id,
        expected_data_checksum=expected_data_checksum,
        benchmark=benchmark,
        corrections=corrections,
    )
    return current, {
        "task_id": task_id,
        "old_evaluation_sha256": file_sha256(old_evaluation_path),
        "actual_response_sha256": file_sha256(actual_response_path),
        "network_har_sha256": file_sha256(network_har_path),
        "current_evaluation_sha256": json_sha256(current),
    }


def load_receipt(path: Path, transition: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    rows = receipt.get("rows") if isinstance(receipt, dict) else None
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("transition_sha256") != json_sha256(transition)
        or receipt.get("from_revision") != transition["from_revision"]
        or receipt.get("compatible_from_revisions")
        != transition["compatible_from_revisions"]
        or receipt.get("to_revision") != transition["to_revision"]
        or receipt.get("from_evaluator_checksum") != transition["from_evaluator_checksum"]
        or receipt.get("from_data_checksum") != transition["from_data_checksum"]
        or receipt.get("to_evaluator_checksum") != transition["to_evaluator_checksum"]
        or receipt.get("to_data_checksum") != transition["to_data_checksum"]
        or not isinstance(rows, list)
    ):
        raise ValueError("WebArena evaluator transition receipt is invalid")
    indexed: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("task_id"), row.get("old_evaluation_sha256"))
        if (
            type(key[0]) is not int
            or not isinstance(key[1], str)
            or len(key[1]) != 64
            or key in indexed
            or any(
                not isinstance(row.get(name), str) or len(row[name]) != 64
                for name in (
                    "actual_response_sha256",
                    "network_har_sha256",
                    "current_evaluation_sha256",
                    "run_inputs_sha256",
                )
            )
        ):
            raise ValueError("WebArena evaluator transition receipt row is invalid")
        indexed[key] = row
    if receipt.get("row_count") != len(rows):
        raise ValueError("WebArena evaluator transition receipt count changed")
    return indexed
