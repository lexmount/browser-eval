"""Create an immutable replay dataset revision from reviewed task corrections."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from harness.browser_use.runner import _configure_benchmark
from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.trajectory_replay.webarena_corrections import apply_correction, load_corrections


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked source: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def revise(
    base_path: Path,
    corrections_path: Path,
    task_ids: list[int],
    selection_path: Path | None = None,
) -> dict[str, Any]:
    if not task_ids or len(task_ids) != len(set(task_ids)):
        raise ValueError("revision task IDs must be nonempty and unique")
    base = json.loads(base_path.read_text(encoding="utf-8"))
    if base.get("schema") != "lex_webarena_verified_trajectory_dataset/1":
        raise ValueError("unsupported source trajectory dataset")
    if "revision" in base:
        raise ValueError("dataset revisions must start from the immutable base")
    trajectories = base.get("trajectories") or []
    if len(trajectories) != 90:
        raise ValueError("revision requires the complete 90-task source")
    rows = {int(row["task_id"]): row for row in trajectories}
    if len(rows) != 90 or not set(task_ids) <= set(rows):
        raise ValueError("revision task not in complete source dataset")
    if selection_path is not None:
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selected_ids = [int(row["task_id"]) for row in selection.get("tasks") or []]
        if (
            selection.get("schema") != "lex_webarena_verified_pilot/1"
            or len(selected_ids) != len(set(selected_ids))
            or not set(selected_ids) <= set(rows)
            or not set(task_ids) <= set(selected_ids)
        ):
            raise ValueError("revision selection is invalid or omits a corrected task")
        base["trajectories"] = [rows[task_id] for task_id in selected_ids]
        base["selection_sha256"] = _sha256(selection_path)
        base["counts"]["verified_tasks"] = len(selected_ids)
        base["counts"]["verified_trajectories"] = len(selected_ids)
    catalog = load_corrections(corrections_path)
    if not set(task_ids) - {243} <= set(catalog.records):
        raise ValueError("missing reviewed correction")
    benchmark = _configure_benchmark()
    records = []
    for task_id in sorted(task_ids):
        row = rows[task_id]
        original = benchmark.get_task(task_id)
        original_data = original.model_dump(mode="json")
        if task_id == 243:
            candidate_relpath = (
                Path("evaluation/trajectory-replay/webarena-verified")
                / "retrieval-candidates/task-243.json"
            )
            candidate_path = Path(__file__).resolve().parents[1] / candidate_relpath
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            candidate_calls = candidate.get("tool_calls")
            candidate_contract = candidate.get("retrieval_contract")
            if (
                candidate.get("task_id") != 243
                or not isinstance(candidate_calls, list)
                or not isinstance(candidate_contract, dict)
                or len(candidate_calls) < 2
                or any("customer/index" in json.dumps(call) for call in candidate_calls)
            ):
                raise ValueError(
                    "task 243: corrected trajectory is missing or uses the old customer lookup"
                )
            validate_contract(candidate_contract, candidate_calls)
            reference_path = (
                Path(__file__).resolve().parents[1]
                / "research/trajectory-replay-task243-nickname-reference.json"
            )
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
            source = reference.get("source") or {}
            reviews = source.get("review_population") or []
            lowest = min(item["rating"] for item in reviews)
            matches = [item for item in reviews if item["rating"] == lowest]
            if (
                reference.get("schema") != "trajectory_replay_independent_reference/1"
                or reference.get("task_id") != 243
                or reference.get("source_trajectory_sha256")
                != hashlib.sha256(_canonical(row)).hexdigest()
                or len(matches) != 1
                or source.get("lowest_rating_review_count") != 1
                or reference.get("expected_response", {}).get("retrieved_data")
                != [matches[0].get("nickname")]
                or original_data["revision"] != 3
                or "nickname" not in original_data["intent"]
                or original_data["eval"][0]["expected"] != reference["expected_response"]
                or len(reference.get("regression_ids") or []) < 2
            ):
                raise ValueError("task 243: fork replacement or independent reference differs")
            corrected_data = original_data
            correction_id = "fork-243-review-nickname"
            correction_sha256 = _sha256(reference_path)
            old_sha256 = hashlib.sha256(
                _canonical({"task": row["task"], "evaluation_input": row["evaluation_input"]})
            ).hexdigest()
            new_sha256 = hashlib.sha256(_canonical(corrected_data)).hexdigest()
            row["tool_calls"] = candidate_calls
            row["source"] = {
                "kind": "corrected_retrieval_candidate_pending_requalification",
                "path": candidate_relpath.as_posix(),
                "sha256": _sha256(candidate_path),
            }
        else:
            if (
                row["task"]["intent"] != original_data["intent"]
                or row["evaluation_input"] != original_data["eval"][0]["expected"]
            ):
                raise ValueError(f"task {task_id}: source differs from upstream authority")
            corrected = apply_correction(original, catalog)
            corrected_data = corrected.task.model_dump(mode="json")
            correction_id = corrected.correction_id
            correction_sha256 = corrected.correction_source_sha256
            old_sha256 = corrected.original_task_sha256
            new_sha256 = corrected.corrected_task_sha256
        for key in ("intent", "intent_template_id", "revision", "sites", "start_urls"):
            row["task"][key] = corrected_data[key]
        row["task"]["intent_template"] = corrected_data["intent_template"]
        row["task"]["instantiation_dict"] = corrected_data["instantiation_dict"]
        row["evaluation_input"] = corrected_data["eval"][0]["expected"]
        previous = row["verification"]
        row["verification"] = {
            "browsers": [],
            "process_status": "pending_requalification",
            "evaluator_status": "pending_requalification",
            "contract": {"kind": "corrected_task_contract"},
            "model_calls": 0,
            "prior_receipt_run_ids": previous.get("receipt_run_ids") or [],
        }
        records.append(
            {
                "task_id": task_id,
                "correction_id": correction_id,
                "correction_record_sha256": correction_sha256,
                "original_task_sha256": old_sha256,
                "corrected_task_sha256": new_sha256,
                "task_contract_sha256": hashlib.sha256(
                    _canonical({"task": row["task"], "evaluation_input": row["evaluation_input"]})
                ).hexdigest(),
            }
        )
    base["revision"] = {
        "schema": "webarena_replay_dataset_revision/1",
        "base_dataset_sha256": _sha256(base_path),
        "correction_catalog_sha256": _sha256(corrections_path),
        "changed_tasks": records,
        "qualification_status": "pending_full_scope_reference_requalification",
    }
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--task-id", type=int, action="append", required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace-generated", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        if not args.replace_generated or args.output.is_symlink():
            raise ValueError("revision output exists")
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous.get("revision", {}).get(
            "schema"
        ) != "webarena_replay_dataset_revision/1" or previous["revision"].get(
            "base_dataset_sha256"
        ) != _sha256(args.base):
            raise ValueError("cannot replace a different dataset")
    result = revise(args.base, args.corrections, args.task_id, args.selection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"tasks": args.task_id, "sha256": _sha256(args.output)}))


if __name__ == "__main__":
    main()
