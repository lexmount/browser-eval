"""Create a hash-verified evidence subset for supplemental result review."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.safety import contained_path
from scripts.run_benchmark import artifact_bindings


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", required=True)
    args = parser.parse_args()

    evidence = args.evidence.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    requested = set(args.case_id)
    dataset = json.loads((evidence / "dataset.json").read_text())
    cases = [case for case in dataset.get("cases", []) if case.get("id") in requested]
    found = {case["id"] for case in cases}
    if found != requested:
        raise SystemExit("Unknown case IDs: " + ", ".join(sorted(requested - found)))
    records = [row for row in _records(evidence / "results.jsonl")
               if row.get("case_id") in requested]
    all_records = _records(evidence / "results.jsonl")
    providers = {row["provider"] for row in all_records}
    expected = len(requested) * len(providers)
    coverage = {(row["provider"], row["case_id"]) for row in records}
    expected_coverage = {(provider, case_id) for provider in providers for case_id in requested}
    if (coverage != expected_coverage or len(records) != expected
            or len({row["attempt_id"] for row in records}) != expected):
        raise SystemExit(f"Subset is incomplete: expected {expected}, found {len(records)}")

    output.mkdir(parents=True)
    subset = dict(dataset)
    subset["cases"] = cases
    comparison = dict(dataset.get("comparison", {}))
    comparison["require_independent_result_review"] = True
    subset["comparison"] = comparison
    (output / "dataset.json").write_text(
        json.dumps(subset, ensure_ascii=False, indent=2) + "\n")
    (output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))

    copied: dict[str, str] = {}
    for row in records:
        for relative, digest in artifact_bindings(row):
            if relative in copied and copied[relative] != digest:
                raise SystemExit(f"Conflicting artifact binding: {relative}")
            source = contained_path(evidence, relative)
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise SystemExit(f"Invalid artifact binding: {relative}")
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
            copied[relative] = digest
    print(f"cases={len(cases)} attempts={len(records)} artifacts={len(copied)} output={output}")


if __name__ == "__main__":
    main()
