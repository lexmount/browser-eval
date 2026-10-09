"""Supersede a declared WebMainBench case scope in new, immutable provider shards."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from harness.record_io import load_jsonl
from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_scoring import (
    CORE_METRICS,
    DATASET_SHA256,
    SCORER_COMMIT,
    aggregate,
)

PROVIDERS = ("kitesurf", "lightpanda", "obscura", "moli", "chromium")
AXES = ("language", "level", "table", "code", "equation")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dimensions(rows: list[dict]) -> dict:
    grouped = {axis: defaultdict(list) for axis in AXES}
    for row in rows:
        for axis in AXES:
            value = row["meta"].get(axis)
            if isinstance(value, list):
                value = "+".join(sorted(value)) if value else "none"
            grouped[axis][str(value if value not in (None, "") else "unavailable")].append(row)
    return {axis: {key: aggregate(group) for key, group in sorted(values.items())}
            for axis, values in grouped.items()}


def apply(source_root: Path, correction_roots: dict[str, Path], output: Path, case_id: str,
          providers: tuple[str, ...], dataset: Path, manifest: Path) -> dict:
    if output.exists():
        raise ValueError("Correction output exists; refusing to overwrite")
    if digest(dataset) != DATASET_SHA256:
        raise ValueError("Parent dataset hash changed")
    sample_hash = digest(manifest)
    sample = json.loads(manifest.read_text(encoding="utf-8"))
    case_ids = {row["track_id"] for row in sample["cases"]}
    if case_id not in case_ids or len(case_ids) != 200:
        raise ValueError("Case is not in the frozen 200-page sample")
    if not providers or len(set(providers)) != len(providers) or any(name not in PROVIDERS for name in providers):
        raise ValueError("Invalid declared provider scope")
    if set(correction_roots) != set(providers):
        raise ValueError("Correction bindings differ from declared provider scope")
    parent_source = {row["track_id"]: row for row in read_jsonl(dataset)}
    source_case = parent_source[case_id]
    correction = {"schema_version": 1, "kind": "proven_fixture_correction", "case_id": case_id,
                  "sample_manifest_sha256": sample_hash, "source_dataset_sha256": DATASET_SHA256,
                  "providers": list(providers), "effects": {}}
    # The source root remains untouched. A failed correction build is an
    # unpublished new directory, never a mutation of an authoritative run.
    output.mkdir(parents=True)
    for name in PROVIDERS:
        source = source_root / name
        target = output / name
        shutil.copytree(source, target)
        if name not in providers:
            continue
        old_rows = read_jsonl(source / "scored-results.jsonl")
        old_calls = read_jsonl(source / "physical-attempts.jsonl")
        old = next(row for row in old_rows if row["track_id"] == case_id)
        if len([row for row in old_rows if row["track_id"] == case_id]) != 1:
            raise ValueError(name + ": correction scope is not unique")
        replacement_source = correction_roots[name]
        replacement_rows = read_jsonl(replacement_source / "scored-results.jsonl")
        replacement_calls = read_jsonl(replacement_source / "physical-attempts.jsonl")
        if len(replacement_rows) != 1 or not replacement_calls:
            raise ValueError(name + ": corrected scope is incomplete")
        new = replacement_rows[0]
        execution = json.loads((replacement_source / "execution.json").read_text(encoding="utf-8"))
        original_execution = json.loads((source / "execution.json").read_text(encoding="utf-8"))
        original_summary = json.loads((source / "score-summary.json").read_text(encoding="utf-8"))
        new_summary = json.loads((replacement_source / "score-summary.json").read_text(encoding="utf-8"))
        if (new["provider"] != name or new["track_id"] != case_id or not new["ok"]
                or execution.get("case_count") != 1 or execution.get("timeout_seconds") != 45
                or execution.get("max_failure_retries") != 1
                or not execution.get("implementation_identity", {}).get("binary_sha256")
                or execution.get("implementation_identity", {}).get("binary_sha256")
                    != original_execution.get("implementation_identity", {}).get("binary_sha256")
                or new_summary["scorer"] != original_summary["scorer"]
                or new_summary["scorer"]["commit"] != SCORER_COMMIT
                or any(row["track_id"] != case_id or row["provider"] != name for row in replacement_calls)
                or new["input_html_sha256"] != hashlib.sha256(source_case["html"].encode()).hexdigest()
                or new["reference_sha256"] != hashlib.sha256(source_case["groundtruth_content"].encode()).hexdigest()):
            raise ValueError(name + ": corrected execution contract mismatch")
        if not all(key in new["metrics"] for key in (*CORE_METRICS, "overall")):
            raise ValueError(name + ": corrected metrics are incomplete")
        old_artifact = contained_path(source, old["artifact_path"])
        new_artifact = contained_path(replacement_source, new["artifact_path"])
        if digest(old_artifact) != old["output_sha256"] or digest(new_artifact) != new["output_sha256"]:
            raise ValueError(name + ": artifact binding mismatch")
        if old["output_sha256"] == new["output_sha256"]:
            raise ValueError(name + ": correction made no output change")
        destination = contained_path(target, new["artifact_path"])
        shutil.copy2(new_artifact, destination)
        replaced_rows = [new if row["track_id"] == case_id else row for row in old_rows]
        replaced_calls = [row for row in old_calls if row["track_id"] != case_id] + replacement_calls
        write_jsonl(target / "scored-results.jsonl", replaced_rows)
        write_jsonl(target / "results.jsonl", [{key: value for key, value in row.items() if key != "metrics"}
                                               for row in replaced_rows])
        write_jsonl(target / "physical-attempts.jsonl", replaced_calls)
        summary = dict(original_summary)
        summary["request_successes"] = sum(bool(row["ok"]) for row in replaced_rows)
        summary["request_success_rate"] = summary["request_successes"] / len(replaced_rows)
        summary["official_aggregate"] = aggregate(replaced_rows)
        summary["dimensions"] = dimensions(replaced_rows)
        summary["correction"] = {"case_id": case_id, "prior_scored_results_sha256": digest(source / "scored-results.jsonl"),
                                 "replacement_scored_result_sha256": digest(replacement_source / "scored-results.jsonl")}
        write_json(target / "score-summary.json", summary)
        original_execution.setdefault("correction_runs", []).append({
            "case_id": case_id, "run_id": execution["run_id"], "execution_sha256": digest(replacement_source / "execution.json"),
            "runner_sha256": execution["runner_sha256"], "old_output_sha256": old["output_sha256"],
            "new_output_sha256": new["output_sha256"], "old_scored_result_sha256": digest(source / "scored-results.jsonl"),
            "correction_calls": len(replacement_calls),
        })
        if execution.get("fixture_ca_artifact"):
            correction_ca = contained_path(replacement_source, execution["fixture_ca_artifact"])
            shutil.copy2(correction_ca, target / "correction-fixture-ca.pem")
            original_execution["correction_runs"][-1]["fixture_ca_sha256"] = digest(correction_ca)
        write_json(target / "execution.json", original_execution)
        correction["effects"][name] = {
            "old_output_sha256": old["output_sha256"], "new_output_sha256": new["output_sha256"],
            "old_text_edit": old["metrics"]["text_edit"].get("score"),
            "new_text_edit": new["metrics"]["text_edit"].get("score"),
            "old_aggregate": original_summary["official_aggregate"],
            "new_aggregate": summary["official_aggregate"],
            "old_physical_calls": len([row for row in old_calls if row["track_id"] == case_id]),
            "new_physical_calls": len(replacement_calls),
        }
    write_json(output / "fixture-correction.json", correction)
    return correction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--correction", action="append", required=True,
                        help="Declared provider=immutable one-case evidence path")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--providers", default="lightpanda,moli,chromium")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    args = parser.parse_args()
    bindings = {}
    for item in args.correction:
        name, separator, path = item.partition("=")
        if not separator or name in bindings or not path:
            raise ValueError("Invalid correction binding")
        bindings[name] = Path(path)
    result = apply(args.source_root, bindings, args.output, args.case_id,
                   tuple(value.strip() for value in args.providers.split(",")), args.dataset, args.sample_manifest)
    print(json.dumps({"case_id": result["case_id"], "providers": result["providers"],
                      "text_edit_effects": {name: [row["old_text_edit"], row["new_text_edit"]]
                                            for name, row in result["effects"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
