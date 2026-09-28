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
RECEIPT_SCHEMA = "lex_webarena_evaluator_transition_receipt/3"
SOURCE_KINDS = {"revision", "evaluator_fixes_sha256"}


def validate_source_pass(
    evaluation: dict[str, Any],
    *,
    task_id: int,
    source: dict[str, str],
) -> None:
    """Validate immutable source-era provenance before current re-evaluation.

    Source-era correction metadata cannot be compared with the current correction
    catalog because accepted fixes may have moved upstream. The transition receipt
    binds the exact old evaluation, response, HAR and current re-evaluation.
    """
    evaluator_rows = evaluation.get("evaluators_results") or []
    correction_id = evaluation.get("contract_correction_id")
    corrected_task = evaluation.get("corrected_task_sha256")
    if (
        evaluation.get("task_id") != task_id
        or float(evaluation.get("score") or 0) != 1.0
        or str(evaluation.get("status") or "").lower() not in {"pass", "success"}
        or evaluation.get("webarena_verified_evaluator_checksum")
        != source["evaluator_checksum"]
        or evaluation.get("webarena_verified_data_checksum") != source["data_checksum"]
        or not isinstance(evaluator_rows, list)
        or not evaluator_rows
        or any(
            float(row.get("score") or 0) != 1.0
            or str(row.get("status") or "").lower() not in {"pass", "success"}
            for row in evaluator_rows
        )
        or ((correction_id is None) != (corrected_task is None))
        or (corrected_task is not None and len(str(corrected_task)) != 64)
    ):
        raise ValueError(f"task {task_id} source pass has stale or incomplete provenance")


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_transition(path: Path) -> dict[str, Any]:
    transition = json.loads(path.read_text(encoding="utf-8"))
    compatible = transition.get("compatible_from_revisions")
    sources = transition.get("compatible_sources")
    source_keys = {
        (source.get("kind"), source.get("identity"))
        for source in sources or []
        if isinstance(source, dict)
    }
    if (
        transition.get("schema") != TRANSITION_SCHEMA
        or transition.get("repository") != WEBARENA_VERIFIED_FORK_URL
        or transition.get("to_revision") != WEBARENA_VERIFIED_REVISION
        or transition.get("to_evaluator_checksum") != compute_evaluator_checksum()
        or not isinstance(compatible, list)
        or transition.get("from_revision") not in compatible
        or len(set(compatible)) != len(compatible)
        or any(not isinstance(value, str) or len(value) != 40 for value in compatible)
        or not isinstance(sources, list)
        or not sources
        or len(source_keys) != len(sources)
        or any(
            not isinstance(source, dict)
            or source.get("kind") not in SOURCE_KINDS
            or not isinstance(source.get("identity"), str)
            or len(source["identity"])
            != (40 if source.get("kind") == "revision" else 64)
            or any(
                not isinstance(source.get(name), str) or len(source[name]) != 64
                for name in ("evaluator_checksum", "data_checksum")
            )
            for source in sources
        )
        or {identity for kind, identity in source_keys if kind == "revision"}
        != set(compatible)
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


def compatible_source(
    transition: dict[str, Any],
    *,
    revision: str | None,
    evaluator_fixes_sha256: str | None,
) -> dict[str, str] | None:
    """Resolve the exact evaluator/data identity used by an older run."""

    candidates = []
    if isinstance(revision, str):
        candidates.append(("revision", revision))
    if isinstance(evaluator_fixes_sha256, str):
        candidates.append(("evaluator_fixes_sha256", evaluator_fixes_sha256))
    matches = [
        source
        for source in transition["compatible_sources"]
        if (source["kind"], source["identity"]) in candidates
    ]
    if len(matches) > 1:
        raise ValueError("run matches more than one evaluator transition source")
    return matches[0] if matches else None


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


def load_receipt(
    path: Path, transition: dict[str, Any]
) -> tuple[
    dict[tuple[int, str, str], dict[str, Any]],
    set[tuple[int, str, str]],
]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    rows = receipt.get("rows") if isinstance(receipt, dict) else None
    excluded = (
        receipt.get("excluded_old_evaluations", []) if isinstance(receipt, dict) else None
    )
    excluded_keys = [
        (
            row.get("task_id"),
            row.get("old_evaluation_sha256"),
            row.get("run_inputs_sha256"),
        )
        for row in excluded or []
        if isinstance(row, dict)
    ]
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("transition_sha256") != json_sha256(transition)
        or receipt.get("from_revision") != transition["from_revision"]
        or receipt.get("compatible_from_revisions")
        != transition["compatible_from_revisions"]
        or receipt.get("compatible_sources") != transition["compatible_sources"]
        or receipt.get("to_revision") != transition["to_revision"]
        or receipt.get("from_evaluator_checksum") != transition["from_evaluator_checksum"]
        or receipt.get("from_data_checksum") != transition["from_data_checksum"]
        or receipt.get("to_evaluator_checksum") != transition["to_evaluator_checksum"]
        or receipt.get("to_data_checksum") != transition["to_data_checksum"]
        or not isinstance(rows, list)
        or not isinstance(excluded, list)
        or len(excluded_keys) != len(excluded)
        or excluded_keys != sorted(set(excluded_keys))
        or any(
            type(task_id) is not int
            or not isinstance(evaluation_sha256, str)
            or len(evaluation_sha256) != 64
            or not isinstance(run_inputs_sha256, str)
            or len(run_inputs_sha256) != 64
            for task_id, evaluation_sha256, run_inputs_sha256 in excluded_keys
        )
    ):
        raise ValueError("WebArena evaluator transition receipt is invalid")
    indexed: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("task_id"),
            row.get("old_evaluation_sha256"),
            row.get("run_inputs_sha256"),
        )
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
            or not isinstance(row.get("source"), dict)
            or row["source"] not in transition["compatible_sources"]
        ):
            raise ValueError("WebArena evaluator transition receipt row is invalid")
        indexed[key] = row
    if receipt.get("row_count") != len(rows):
        raise ValueError("WebArena evaluator transition receipt count changed")
    if set(indexed) & set(excluded_keys):
        raise ValueError("WebArena evaluator transition receipt includes excluded evidence")
    return indexed, set(excluded_keys)
