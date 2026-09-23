"""Offline-rescore saved WebArena replay evidence after a bound correction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark
from harness.trajectory_replay.webarena_corrections import (
    apply_correction,
    evaluate_corrected_contract,
    load_corrections,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rescore(
    source_sweep: Path,
    output: Path,
    site_map: Path,
    task_ids: list[int],
    *,
    runtime_correction_id: str | None = None,
) -> dict:
    if output.exists():
        raise ValueError("offline rescore output already exists")
    progress_path = source_sweep / "progress.jsonl"
    progress = {
        row["task_id"]: row
        for row in (
            json.loads(line)
            for line in progress_path.read_text(encoding="utf-8").splitlines()
        )
        if "action_count" in row
    }
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("offline rescore task scope contains duplicates")
    catalog = load_corrections()
    benchmark = remote_benchmark(load_remote_sites(site_map))
    rows = []
    for task_id in task_ids:
        source = progress.get(task_id)
        if source is None or source.get("status") != "fail":
            raise ValueError(f"task {task_id} is not a saved failed attempt")
        task_dir = source_sweep / f"task-{task_id}"
        evidence = source.get("evidence_sha256") or {}
        for key, name in {
            "network_har": "network.har",
            "actual_response": "actual_response.json",
            "evaluation": "evaluation.json",
            "trace": "trace.json",
        }.items():
            path = task_dir / name
            if path.is_symlink() or not path.is_file() or _hash(path) != evidence.get(key):
                raise ValueError(f"task {task_id} source evidence changed: {name}")
        actual = json.loads((task_dir / "actual_response.json").read_text(encoding="utf-8"))
        if runtime_correction_id is None:
            contract = apply_correction(benchmark.get_task(task_id), catalog)
            result = evaluate_corrected_contract(
                benchmark,
                contract,
                agent_response=actual,
                network_trace=task_dir / "network.har",
            ).model_dump(mode="json")
            result["contract_correction_id"] = contract.correction_id
            result["corrected_task_sha256"] = contract.corrected_task_sha256
        else:
            result = benchmark.evaluate_task(
                task_id=task_id,
                agent_response=actual,
                network_trace=task_dir / "network.har",
            ).model_dump(mode="json")
            result["runtime_correction_id"] = runtime_correction_id
            result["corrected_runtime_sha256"] = _hash(
                Path(__file__).resolve().parents[1]
                / "harness"
                / "trajectory_replay"
                / "remote_runtime.py"
            )
        prior = json.loads((task_dir / "evaluation.json").read_text(encoding="utf-8"))
        result["replay_assertions"] = prior.get("replay_assertions") or []
        if float(result.get("score") or 0) != 1 or any(
            item.get("status") != "pass" for item in result["replay_assertions"]
        ):
            raise ValueError(f"task {task_id} remains failed after offline rescore")
        target = output / f"task-{task_id}" / "evaluation.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows.append(
            {
                "task_id": task_id,
                "source_evidence_sha256": evidence,
                "evaluation_sha256": _hash(target),
            }
        )
    manifest = {
        "schema": "lex_webarena_verified_offline_rescore/1",
        "source_sweep_progress_sha256": _hash(progress_path),
        "site_map_sha256": _hash(site_map),
        **(
            {"correction_catalog_sha256": catalog.source_sha256}
            if runtime_correction_id is None
            else {
                "runtime_correction_id": runtime_correction_id,
                "corrected_runtime_sha256": _hash(
                    Path(__file__).resolve().parents[1]
                    / "harness"
                    / "trajectory_replay"
                    / "remote_runtime.py"
                ),
            }
        ),
        "provider_call_count": 0,
        "rows": rows,
    }
    (output / "rescoring.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sweep", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--task-id", type=int, action="append", required=True)
    parser.add_argument("--runtime-correction-id")
    args = parser.parse_args()
    result = rescore(
        args.source_sweep,
        args.output,
        args.site_map,
        args.task_id,
        runtime_correction_id=args.runtime_correction_id,
    )
    print(json.dumps({"rescored": len(result["rows"]), "provider_call_count": 0}))


if __name__ == "__main__":
    main()
