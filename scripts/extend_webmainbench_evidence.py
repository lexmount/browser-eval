"""Add one measured WebMainBench method without replacing any prior result."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from harness.web_fetch.safety import contained_path


NEW_PROVIDER = "moli-1.1.7"
DATASET_ID = "webmainbench-545-preselected-200-six-methods"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Complete five-method evidence before the TEDS correction")
    parser.add_argument("--new-shard", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output exists; evidence is immutable")
    source, shard = args.source.resolve(), args.new_shard.resolve()
    dataset = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    if dataset.get("id") != "webmainbench-545-preselected-200-five-browsers":
        raise ValueError("Expected the complete prior five-method evidence")
    cases = {row["id"]: row for row in dataset["cases"]}
    old_providers = dataset["coverage_plan"]["static_html"]
    old_rows = read_jsonl(source / "results.jsonl")
    if len(cases) != 200 or len(old_rows) != 200 * len(old_providers) or {
        (row["provider"], row["case_id"]) for row in old_rows
    } != {(name, case_id) for name in old_providers for case_id in cases}:
        raise ValueError("Prior evidence matrix is incomplete")
    new_rows = read_jsonl(shard / "scored-results.jsonl")
    new_physical = read_jsonl(shard / "physical-attempts.jsonl")
    if (len(new_rows) != len(cases)
            or {row["track_id"] for row in new_rows} != set(cases)
            or {row["provider"] for row in new_rows} != {NEW_PROVIDER}):
        raise ValueError("New measured method does not cover the same 200 cases")
    manifest = json.loads((shard / "execution.json").read_text(encoding="utf-8"))
    summary = json.loads((shard / "score-summary.json").read_text(encoding="utf-8"))
    selection = digest(source / "sample-manifest.json")
    if (manifest.get("provider") != NEW_PROVIDER or manifest.get("case_count") != 200
            or manifest.get("selection_manifest", {}).get("sha256") != selection
            or manifest.get("implementation_identity", {}).get("reported_version") != "moli 1.1.7"
            or manifest.get("source_dataset_sha256") != dataset["source_dataset_sha256"]
            or summary.get("scorer", {}).get("commit") != dataset["comparison"]["official_scorer_commit"]):
        raise ValueError("New run identity, selection or scorer does not match the frozen cohort")
    physical_by_case = {}
    for attempt in new_physical:
        if attempt.get("provider") != NEW_PROVIDER:
            raise ValueError("Physical call belongs to another method")
        physical_by_case.setdefault(attempt["track_id"], []).append(attempt)
    if set(physical_by_case) != set(cases):
        raise ValueError("Physical calls do not cover the same cases")
    for row in new_rows:
        case = cases[row["track_id"]]
        artifact = contained_path(shard, row["artifact_path"])
        attempts = physical_by_case[row["track_id"]]
        if (digest(artifact) != row["output_sha256"]
                or row["input_html_sha256"] != case["input_html_sha256"]
                or row["reference_sha256"] != case["reference_sha256"]
                or len(attempts) != row["physical_attempt_count"]
                or attempts[-1]["output_sha256"] != row["output_sha256"]
                or abs(sum(item["elapsed_ms"] for item in attempts) - row["total_elapsed_ms"]) > 1e-9):
            raise ValueError("New evidence binding failed: " + row["track_id"])
    for row in old_rows:
        if digest(contained_path(source, row["artifact_path"])) != row["output_sha256"]:
            raise ValueError("Prior output changed: " + row["attempt_id"])
    for case in cases.values():
        if digest(source / "references" / (case["id"] + ".md")) != case["reference_sha256"]:
            raise ValueError("Prior reference changed: " + case["id"])

    shutil.copytree(source, args.output)
    output = args.output
    dataset["id"] = DATASET_ID
    dataset["name"] = "WebMainBench-545 preselected 200 · six Web Fetch methods"
    dataset["coverage_plan"]["static_html"].insert(-1, NEW_PROVIDER)
    write_json(output / "dataset.json", dataset)
    with (output / "results.jsonl").open("a", encoding="utf-8") as stream:
        for row in new_rows:
            source_artifact = contained_path(shard, row["artifact_path"])
            target = contained_path(output, row["artifact_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_artifact, target)
            stream.write(json.dumps({**row, "case_id": row["track_id"]}, ensure_ascii=False) + "\n")
    with (output / "physical-attempts.jsonl").open("a", encoding="utf-8") as stream:
        for row in new_physical:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summaries = json.loads((output / "score-summaries.json").read_text(encoding="utf-8"))
    summaries[NEW_PROVIDER] = summary
    write_json(output / "score-summaries.json", summaries)
    executions = json.loads((output / "execution.json").read_text(encoding="utf-8"))
    source_ca = contained_path(shard, manifest["fixture_ca_artifact"])
    if digest(source_ca) != manifest["network_isolation_probe"]["fixture_ca_sha256"]:
        raise ValueError("New fixture certificate binding failed")
    target_ca = output / "fixture-certificates" / f"{NEW_PROVIDER}-ca.pem"
    shutil.copy2(source_ca, target_ca)
    manifest["assembled_fixture_ca_path"] = str(target_ca.relative_to(output))
    executions["batches"].append(manifest)
    write_json(output / "execution.json", executions)
    print(json.dumps({"output": str(output), "prior_results": len(old_rows),
                      "new_results": len(new_rows), "physical_calls": len(new_physical)}))


if __name__ == "__main__":
    main()
