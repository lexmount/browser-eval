"""Supersede a declared WebMainBench case scope in new, immutable provider shards."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from harness.record_io import load_jsonl

from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.web_fetch.safety import evidence_tree_identity, stage_evidence_revision
from .webmainbench_evidence import copy_alias_artifacts, copy_bound, require_unique_attempt_ids, retry_chain_matches
from .webmainbench_scoring import CORE_METRICS, DATASET_SHA256, SCORER_COMMIT, aggregate

AXES = ("language", "level", "table", "code", "equation")
SHARD_FILES = ("scored-results.jsonl", "results.jsonl", "physical-attempts.jsonl",
               "score-summary.json", "execution.json", "dataset.json")


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


def _read_json(root: Path, name: str) -> dict:
    value = json.loads(contained_path(root, name, reject_symlinks=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Evidence document must be an object: " + name)
    return value


def _fixture_bindings(execution: dict):
    relative = execution.get("fixture_ca_artifact")
    if relative:
        expected = execution.get("network_isolation_probe", {}).get("fixture_ca_sha256")
        if not expected:
            raise ValueError("Fixture CA has no hash binding")
        yield relative, expected
    for run in execution.get("correction_runs", []):
        if run.get("fixture_ca_sha256"):
            # Historical receipts used one fixed path; new ones bind the path explicitly.
            yield run.get("fixture_ca_artifact", "correction-fixture-ca.pem"), run["fixture_ca_sha256"]


def _shard(root: Path, provider: str, cases: dict[str, dict], profile: dict):
    rows = read_jsonl(contained_path(root, "scored-results.jsonl", reject_symlinks=True))
    raw = read_jsonl(contained_path(root, "results.jsonl", reject_symlinks=True))
    calls = read_jsonl(contained_path(root, "physical-attempts.jsonl", reject_symlinks=True))
    execution = _read_json(root, "execution.json")
    summary = _read_json(root, "score-summary.json")
    dataset = _read_json(root, "dataset.json")
    ids = set(cases)
    if (len(rows) != len(ids) or {row["track_id"] for row in rows} != ids
            or any(row["provider"] != provider for row in rows + calls)
            or {row["track_id"] for row in calls} != ids
            or raw != [{key: value for key, value in row.items() if key != "metrics"} for row in rows]
            or execution.get("provider") != provider or execution.get("case_count") != len(ids)
            or execution.get("source_dataset_sha256") != profile["source_dataset_sha256"]
            or execution.get("timeout_seconds") != profile["timeout_seconds"]
            or execution.get("max_failure_retries") != profile["max_failure_retries"]
            or dataset.get("case_count") != len(ids) or len(dataset.get("case_ids", [])) != len(ids)
            or set(dataset.get("case_ids", [])) != ids
            or summary.get("provider") != provider or summary.get("case_count") != len(ids)
            or summary.get("scorer", {}).get("commit") != SCORER_COMMIT
            or summary.get("official_aggregate") != aggregate(rows)):
        raise ValueError(provider + ": incomplete or mismatched shard contract")
    require_unique_attempt_ids(rows, "Logical")
    require_unique_attempt_ids(calls, "Physical")
    for relative, expected in _fixture_bindings(execution):
        if digest(contained_path(root, relative, reject_symlinks=True)) != expected:
            raise ValueError(provider + ": fixture CA binding mismatch")
    grouped = defaultdict(list)
    for call in calls:
        grouped[call["track_id"]].append(call)
    for row in rows:
        record = cases[row["track_id"]]
        if (not all(key in row.get("metrics", {}) for key in (*CORE_METRICS, "overall"))
                or row.get("input_html_sha256") != hashlib.sha256(record["html"].encode()).hexdigest()
                or row.get("reference_sha256") != hashlib.sha256(record["groundtruth_content"].encode()).hexdigest()
                or not retry_chain_matches(row, grouped[row["track_id"]])
                or row["physical_attempt_count"] > profile["max_failure_retries"] + 1):
            raise ValueError(provider + ": input, metrics or retry chain mismatch")
    return rows, calls, execution, summary


def _copy_ca(source: Path, target: Path, execution: dict) -> None:
    for relative, expected in _fixture_bindings(execution):
        copy_bound(source, target, relative, expected)


def apply(source_root: Path, correction_roots: dict[str, Path], output: Path, case_id: str,
          providers: tuple[str, ...], dataset: Path, manifest: Path, *, profile: Path) -> dict:
    for path in (dataset, manifest, profile):
        if path.is_symlink():
            raise ValueError("Symlinked correction input is forbidden")
    frozen = {path: digest(path) for path in (dataset, manifest, profile)}
    authority = _read_json(profile.parent, profile.name)
    sample = _read_json(manifest.parent, manifest.name)
    declared = authority["providers"]
    if (not declared or len(set(declared)) != len(declared)
            or any(safe_identifier(name) != name for name in declared)
            or frozen[dataset] != DATASET_SHA256
            or authority.get("source_dataset_sha256") != frozen[dataset]
            or authority.get("sample_manifest_sha256") != frozen[manifest]
            or authority.get("official_scorer_commit") != SCORER_COMMIT
            or authority.get("case_count") != 200
            or authority.get("timeout_seconds") != 45
            or authority.get("max_failure_retries") != 1):
        raise ValueError("Frozen sample profile or dataset binding mismatch")
    case_ids = [row["track_id"] for row in sample["cases"]]
    if len(case_ids) != 200 or len(set(case_ids)) != 200 or case_id not in case_ids:
        raise ValueError("Case is not in the frozen 200-page sample")
    if (not providers or len(set(providers)) != len(providers)
            or not set(providers) <= set(declared) or set(correction_roots) != set(providers)):
        raise ValueError("Correction bindings differ from declared provider scope")
    records = read_jsonl(dataset)
    parent = {row["track_id"]: row for row in records}
    if len(parent) != len(records) or not set(case_ids) <= set(parent):
        raise ValueError("Dataset case inventory mismatch")
    cases = {name: parent[name] for name in case_ids}
    correction_trees = {root: evidence_tree_identity(root) for root in correction_roots.values()}
    for root in correction_roots.values():
        if output.resolve().is_relative_to(root.resolve()):
            raise ValueError("Correction output must be outside replacement evidence")

    def revise(staging: Path) -> dict:
        receipt = {"schema_version": 1, "kind": "proven_fixture_correction", "case_id": case_id,
                   "sample_manifest_sha256": frozen[manifest], "source_dataset_sha256": frozen[dataset],
                   "profile_sha256": frozen[profile], "providers": list(providers), "effects": {}}
        staging.mkdir()
        logical_inventory, physical_inventory = [], []
        for name in declared:
            source = contained_path(source_root, name, reject_symlinks=True)
            rows, calls, execution, summary = _shard(source, name, cases, authority)
            target = staging / name
            target.mkdir()
            if name not in providers:
                copy_alias_artifacts(source, target, rows + calls, name, name)
                _copy_ca(source, target, execution)
                for filename in SHARD_FILES:
                    copy_bound(source, target, filename, digest(source / filename))
                _shard(target, name, cases, authority)
                logical_inventory.extend(rows)
                physical_inventory.extend(calls)
                continue
            replacement = correction_roots[name]
            new_rows, new_calls, new_execution, new_summary = _shard(
                replacement, name, {case_id: parent[case_id]}, authority)
            old = next(row for row in rows if row["track_id"] == case_id)
            new = new_rows[0]
            identity = execution.get("implementation_identity", {})
            if (not new["ok"] or not identity.get("binary_sha256")
                    or new_execution.get("implementation_identity") != identity
                    or new_summary["scorer"] != summary["scorer"]
                    or old["output_sha256"] == new["output_sha256"]):
                raise ValueError(name + ": corrected execution contract mismatch or unchanged output")
            old_calls = [call for call in calls if call["track_id"] == case_id]
            # Verify superseded evidence before excluding it from the new shard.
            for row in [old, *old_calls]:
                for key in ("artifact_path", "physical_artifact_path"):
                    if key in row and digest(contained_path(source, row[key], reject_symlinks=True)) != row["output_sha256"]:
                        raise ValueError(name + ": superseded artifact binding mismatch")
            kept_rows = [row for row in rows if row["track_id"] != case_id]
            kept_calls = [call for call in calls if call["track_id"] != case_id]
            copy_alias_artifacts(source, target, kept_rows + kept_calls, name, name)
            copy_alias_artifacts(replacement, target, new_rows + new_calls, name, name)
            rows = [new if row["track_id"] == case_id else row for row in rows]
            calls = kept_calls + new_calls
            original_aggregate = summary["official_aggregate"]
            summary.update(request_successes=sum(bool(row["ok"]) for row in rows),
                           official_aggregate=aggregate(rows), dimensions=dimensions(rows))
            summary["request_success_rate"] = summary["request_successes"] / len(rows)
            summary["correction"] = {"case_id": case_id,
                                     "prior_scored_results_sha256": digest(source / "scored-results.jsonl"),
                                     "replacement_scored_result_sha256": digest(replacement / "scored-results.jsonl")}
            run = {"case_id": case_id, "run_id": new_execution["run_id"],
                   "execution_sha256": digest(replacement / "execution.json"),
                   "runner_sha256": new_execution["runner_sha256"], "old_output_sha256": old["output_sha256"],
                   "new_output_sha256": new["output_sha256"],
                   "old_scored_result_sha256": digest(source / "scored-results.jsonl"),
                   "correction_calls": len(new_calls)}
            relative = new_execution.get("fixture_ca_artifact")
            if relative:
                expected = new_execution.get("network_isolation_probe", {}).get("fixture_ca_sha256")
                if not expected:
                    raise ValueError("Correction fixture CA has no hash binding")
                run.update(fixture_ca_artifact=f"fixture-certificates/{expected}.pem", fixture_ca_sha256=expected)
                copy_bound(replacement, target, relative, expected, target_relative=run["fixture_ca_artifact"])
            _copy_ca(source, target, execution)
            execution.setdefault("correction_runs", []).append(run)
            receipt["effects"][name] = {
                "old_output_sha256": old["output_sha256"], "new_output_sha256": new["output_sha256"],
                "old_text_edit": old["metrics"]["text_edit"].get("score"),
                "new_text_edit": new["metrics"]["text_edit"].get("score"),
                "old_aggregate": original_aggregate, "new_aggregate": summary["official_aggregate"],
                "old_physical_calls": len(old_calls), "new_physical_calls": len(new_calls)}
            write_jsonl(target / "scored-results.jsonl", rows)
            write_jsonl(target / "results.jsonl", [{k: v for k, v in row.items() if k != "metrics"} for row in rows])
            write_jsonl(target / "physical-attempts.jsonl", calls)
            write_json(target / "score-summary.json", summary)
            write_json(target / "execution.json", execution)
            copy_bound(source, target, "dataset.json", digest(source / "dataset.json"))
            _shard(target, name, cases, authority)
            logical_inventory.extend(rows)
            physical_inventory.extend(calls)
        require_unique_attempt_ids(logical_inventory, "Logical")
        require_unique_attempt_ids(physical_inventory, "Physical")
        write_json(staging / "fixture-correction.json", receipt)
        if (any(digest(path) != expected for path, expected in frozen.items())
                or any(evidence_tree_identity(root) != expected for root, expected in correction_trees.items())):
            raise ValueError("Correction inputs changed during revision")
        return receipt

    return stage_evidence_revision(source_root, output, revise)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--correction", action="append", required=True,
                        help="Declared provider=immutable one-case evidence path; repeat for each provider")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--providers", help="Comma-separated scope; defaults to the correction bindings")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True, help="Frozen parent sample profile")
    args = parser.parse_args()
    bindings = {}
    for item in args.correction:
        name, separator, path = item.partition("=")
        if not separator or name in bindings or not path:
            raise ValueError("Invalid correction binding")
        bindings[name] = Path(path)
    providers = tuple(value.strip() for value in args.providers.split(",")) if args.providers is not None else tuple(bindings)
    result = apply(args.source_root, bindings, args.output, args.case_id, providers,
                   args.dataset, args.sample_manifest, profile=args.profile)
    print(json.dumps({"case_id": result["case_id"], "providers": result["providers"],
                      "text_edit_effects": {name: [row["old_text_edit"], row["new_text_edit"]]
                                            for name, row in result["effects"].items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
