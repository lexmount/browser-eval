"""Project a completed WebMainBench shard onto a predeclared sample without rerunning providers."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.safety import contained_path


DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def copy_scored_artifact(source_root: Path, output_root: Path, row: dict) -> None:
    source = contained_path(source_root, row["artifact_path"])
    target = contained_path(output_root, row["artifact_path"])
    if digest(source) != row["output_sha256"]:
        raise ValueError("Source artifact hash mismatch: " + row["track_id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output exists; projected evidence is immutable")
    if digest(args.dataset) != DATASET_SHA256:
        raise ValueError("WebMainBench parent dataset hash mismatch")
    manifest = json.loads(args.sample_manifest.read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    selected = {row.get("track_id") for row in cases}
    if (manifest.get("schema_version") != 1
            or manifest.get("parent", {}).get("sha256") != DATASET_SHA256
            or len(cases) != manifest.get("sampling", {}).get("size")
            or None in selected or len(selected) != len(cases)):
        raise ValueError("Invalid WebMainBench sample manifest")

    source_results = read_jsonl(args.source / "results.jsonl")
    source_physical = read_jsonl(args.source / "physical-attempts.jsonl")
    if len(source_results) != 545 or len({row["track_id"] for row in source_results}) != 545:
        raise ValueError("Source shard is not a complete 545-case measurement")
    results = [row for row in source_results if row["track_id"] in selected]
    physical = [row for row in source_physical if row["track_id"] in selected]
    if len(results) != len(selected) or {row["track_id"] for row in results} != selected:
        raise ValueError("Projected logical matrix is incomplete")

    args.output.mkdir(parents=True)
    for row in results:
        copy_scored_artifact(args.source, args.output, row)
    if (args.source / "fixture-ca.pem").is_file():
        shutil.copy2(args.source / "fixture-ca.pem", args.output / "fixture-ca.pem")

    binding = {
        "dataset_id": manifest.get("dataset_id"),
        "sample_manifest_sha256": digest(args.sample_manifest),
        "selection_seed": manifest.get("sampling", {}).get("seed"),
        "case_count": len(selected),
        "selected_before_result_inspection": True,
    }
    source_dataset = json.loads((args.source / "dataset.json").read_text(encoding="utf-8"))
    source_dataset.update({
        "case_count": len(selected),
        "case_ids": [row["track_id"] for row in source_results if row["track_id"] in selected],
        "selection_manifest": binding,
    })
    write_json(args.output / "dataset.json", source_dataset)
    execution = json.loads((args.source / "execution.json").read_text(encoding="utf-8"))
    execution["report_selection"] = binding
    execution["source_batch_bindings"] = {
        "results_sha256": digest(args.source / "results.jsonl"),
        "physical_attempts_sha256": digest(args.source / "physical-attempts.jsonl"),
        "execution_sha256": digest(args.source / "execution.json"),
    }
    write_json(args.output / "execution.json", execution)
    shutil.copy2(args.sample_manifest, args.output / "sample-manifest.json")
    with (args.output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for row in results:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (args.output / "physical-attempts.jsonl").open("w", encoding="utf-8") as stream:
        for row in physical:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({
        "provider": results[0]["provider"],
        "logical_results": len(results),
        "physical_attempts": len(physical),
        "source_batch_case_count": execution.get("case_count"),
        "sample_manifest_sha256": binding["sample_manifest_sha256"],
    }))


if __name__ == "__main__":
    main()
