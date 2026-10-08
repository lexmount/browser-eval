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
from harness.web_fetch.result_review import require_blind_result_review, review_input_identity
from harness.web_fetch.artifacts import artifact_bindings


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def refresh_review_attempt_ids(evidence: Path, prior_evidence: Path) -> set[str]:
    """Return attempts whose task or reviewer-visible evidence changed."""
    current_dataset = json.loads((evidence / "dataset.json").read_text())
    current_records = _records(evidence / "results.jsonl")
    prior_dataset = json.loads((prior_evidence / "dataset.json").read_text())
    prior_records = _records(prior_evidence / "results.jsonl")
    # Selecting the supplemental blind-review scope precedes the post-blind
    # anomaly gate. Requiring that later gate here creates a circular workflow.
    prior_review = require_blind_result_review(
        prior_evidence, prior_dataset, prior_records)
    current_cases = {row["id"]: row for row in current_dataset["cases"]}
    prior_cases = {row["id"]: row for row in prior_dataset["cases"]}
    previous = {row["attempt_id"]: row for row in prior_records}
    prior_judgments = {row["attempt_id"]: row for row in prior_review["attempts"]}
    if len(previous) != len(prior_records):
        raise ValueError("Prior evidence contains duplicate attempt IDs")
    return {
        row["attempt_id"] for row in current_records
        if prior_cases.get(row["case_id"]) != current_cases[row["case_id"]]
        or previous.get(row["attempt_id"]) is None
        or prior_judgments.get(row["attempt_id"]) is None
        or review_input_identity(previous[row["attempt_id"]]) != review_input_identity(row)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--prior-evidence", type=Path,
        help="Select the exact non-reusable attempts relative to a prior complete review",
    )
    args = parser.parse_args()

    evidence = args.evidence.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    if bool(args.case_id) == bool(args.prior_evidence):
        parser.error("choose exactly one of --case-id or --prior-evidence")
    dataset = json.loads((evidence / "dataset.json").read_text())
    all_records = _records(evidence / "results.jsonl")
    selected_attempts = (
        refresh_review_attempt_ids(evidence, args.prior_evidence.resolve())
        if args.prior_evidence else None
    )
    requested = ({row["case_id"] for row in all_records
                  if row["attempt_id"] in selected_attempts}
                 if selected_attempts is not None else set(args.case_id))
    cases = [case for case in dataset.get("cases", []) if case.get("id") in requested]
    found = {case["id"] for case in cases}
    if found != requested:
        raise SystemExit("Unknown case IDs: " + ", ".join(sorted(requested - found)))
    records = [row for row in all_records
               if (row["attempt_id"] in selected_attempts
                   if selected_attempts is not None
                   else row.get("case_id") in requested)]
    providers = {row["provider"] for row in all_records}
    if selected_attempts is not None:
        expected = len(selected_attempts)
        if ({row["attempt_id"] for row in records} != selected_attempts
                or len(records) != expected):
            raise SystemExit(f"Refresh subset is incomplete: expected {expected}, found {len(records)}")
    else:
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
