"""Correct DSH request status from its hash-bound saved answer, without calls.

The correction is deliberately split in two.  ``extract`` fills status codes,
which are not review-visible.  ``classify`` then changes only the non-2xx
request records and removes the now-stale installed review.  After those rows
have been independently re-reviewed and merged, ``finalize`` closes the
audited revision chain.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.failure_diagnosis import diagnose
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.reference_lifecycle import (
    scoring_revision_history_sha256, valid_scoring_revision,
)
from harness.web_fetch.result_review import require_result_review
from harness.web_fetch.report import failure_category
from harness.web_fetch.safety import contained_path
from scripts.revise_body_scoring import rebind_unchanged_result_review
from scripts.run_benchmark import artifact_bindings


PROVIDER = "deepseek-harness-built-in"
HEADER = re.compile(r"Fetched (https?://\S+) \(HTTP ([1-5][0-9][0-9])\)")
RECEIPT = "request-status-corrections.json"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def records(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "results.jsonl").read_text().splitlines()]


def encode(rows: list[dict]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows).encode()


def saved_status(root: Path, row: dict) -> tuple[int, str] | None:
    artifact = contained_path(root, row["artifact_path"])
    raw = artifact.read_bytes()
    if digest(raw) != row["metrics"]["content_sha256"]:
        raise ValueError("Output integrity failure: " + row["attempt_id"])
    first = raw.decode("utf-8", errors="strict").splitlines()[0] if raw else ""
    match = HEADER.fullmatch(first)
    return (int(match.group(2)), match.group(1)) if match else None


def rebind_references(root: Path, dataset_hash: str, old_hash: str,
                      new_hash: str, field: str, at: str) -> None:
    path = root / "reference-revisions.json"
    if not path.is_file():
        return
    receipts = json.loads(path.read_text())
    changed = 0
    for receipt in receipts:
        if (receipt.get("output_dataset_sha256"), receipt.get("output_results_sha256")) == (
                dataset_hash, old_hash):
            receipt["prior_output_results_sha256"] = old_hash
            receipt["output_results_sha256"] = new_hash
            receipt[field] = {"at": at, "provider_calls": 0}
            changed += 1
    if not changed:
        raise ValueError("No reference revision receipt is bound to the prior evidence")
    path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + "\n")


def append_binding(root: Path, old_hash: str, *, at: str, detail: str) -> None:
    dataset_hash = digest((root / "dataset.json").read_bytes())
    new_hash = digest((root / "results.jsonl").read_bytes())
    dataset = json.loads((root / "dataset.json").read_text())
    rows = records(root)
    require_result_review(root, dataset, rows)
    history_path = root / "scoring-revisions.json"
    history = json.loads(history_path.read_text())
    review_hash = digest((root / "result-review.json").read_bytes())
    history.append({
        "kind": "audited_assembled_scoring_revision",
        "scored_at": at,
        "input_dataset_sha256": dataset_hash,
        "input_results_sha256": old_hash,
        "output_dataset_sha256": dataset_hash,
        "output_results_sha256": new_hash,
        "changed_cases": [],
        "task_decision_changes": [],
        "provider_calls": 0,
        "immutable_call_fields": detail,
        "source_scoring_receipts_sha256": scoring_revision_history_sha256(history),
        "independent_result_review_sha256": review_hash,
        "request_status_correction_receipt": RECEIPT,
    })
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")
    if not valid_scoring_revision(root):
        raise ValueError("Corrected evidence has an invalid revision chain")


def extract(base: Path, output: Path, *, expected_headers: int = 133) -> dict:
    if output.exists():
        raise ValueError("Destination exists: " + str(output))
    shutil.copytree(base, output)
    before = (output / "results.jsonl").read_bytes()
    dataset = (output / "dataset.json").read_bytes()
    rows = records(output)
    changes = []
    for row in rows:
        if row.get("provider") != PROVIDER:
            continue
        observed = saved_status(output, row)
        if observed is None:
            continue
        status, url = observed
        previous = row.get("status_code")
        row["status_code"] = status
        row.setdefault("provider_metadata", {})["saved_status_evidence"] = {
            "kind": "exact_saved_answer_header",
            "header": f"Fetched {url} (HTTP {status})",
            "artifact_sha256": row["metrics"]["content_sha256"],
        }
        changes.append({"attempt_id": row["attempt_id"], "before": previous,
                        "after": status, "artifact_sha256": row["metrics"]["content_sha256"]})
    if len(changes) != expected_headers:
        raise ValueError(
            f"Expected {expected_headers} DSH saved HTTP headers, found {len(changes)}")
    after = encode(rows)
    (output / "results.jsonl").write_bytes(after)
    at = datetime.now(timezone.utc).isoformat()
    rebind_unchanged_result_review(
        output / "result-review.json", dataset, before, dataset, after, rebound_at=at)
    receipt = {"schema_version": 1, "corrected_at": at, "provider": PROVIDER,
               "basis": "strict full-match of the hash-bound saved answer's first line",
               "provider_calls": 0, "phase": "status-code-extraction",
               "input_results_sha256": digest(before), "output_results_sha256": digest(after),
               "changes": changes}
    (output / RECEIPT).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    rebind_references(output, digest(dataset), digest(before), digest(after),
                      "rebound_after_saved_status_extraction", at)
    append_binding(
        output, digest(before), at=at,
        detail="attempt identity, output bytes/hash, timestamps, elapsed time, metrics and task verdicts retained; only status_code and non-answer audit metadata derived from exact saved output headers",
    )
    return receipt


def classify(base: Path, output: Path, *, expected_non_2xx: int = 7) -> dict:
    if output.exists():
        raise ValueError("Destination exists: " + str(output))
    shutil.copytree(base, output)
    before = (output / "results.jsonl").read_bytes()
    dataset = json.loads((output / "dataset.json").read_text())
    cases = {case["id"]: case for case in dataset["cases"]}
    rows = records(output)
    changes = []
    for row in rows:
        evidence = row.get("provider_metadata", {}).get("saved_status_evidence")
        status = row.get("status_code")
        if row.get("provider") != PROVIDER or not evidence or not isinstance(status, int):
            continue
        if 200 <= status < 300:
            continue
        case = cases[row["case_id"]]
        before_verdict = task_passed(row["ok"], row["metrics"], case.get("task_kind", "body"))
        old = {key: row.get(key) for key in ("ok", "status_code", "error_type", "error_message", "failure_category")}
        row.update(ok=False, error_type="http_status",
                   error_message=f"Origin returned HTTP {status}")
        raw = contained_path(output, row["artifact_path"]).read_text()
        row["diagnosis"] = diagnose(row, case, raw)
        row["failure_category"] = failure_category(row, case)
        after_verdict = task_passed(row["ok"], row["metrics"], case.get("task_kind", "body"))
        if before_verdict or after_verdict:
            raise ValueError(row["attempt_id"] + ": request correction changed a task verdict")
        changes.append({"attempt_id": row["attempt_id"], "before": old,
                        "after": {key: row.get(key) for key in old},
                        "artifact_sha256": row["metrics"]["content_sha256"]})
    if len(changes) != expected_non_2xx:
        raise ValueError(
            f"Expected {expected_non_2xx} DSH non-2xx answers, found {len(changes)}")
    after = encode(rows)
    (output / "results.jsonl").write_bytes(after)
    (output / "result-review.json").unlink(missing_ok=True)
    receipt = json.loads((output / RECEIPT).read_text())
    receipt["pending_request_classification"] = {
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "phase": "non-2xx-request-classification", "provider_calls": 0,
        "input_results_sha256": digest(before), "output_results_sha256": digest(after),
        "task_decision_changes": 0, "changes": changes,
    }
    (output / RECEIPT).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt["pending_request_classification"]


def make_subset(evidence: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("Destination exists: " + str(output))
    receipt = json.loads((evidence / RECEIPT).read_text())["pending_request_classification"]
    attempt_ids = {row["attempt_id"] for row in receipt["changes"]}
    rows = [row for row in records(evidence) if row["attempt_id"] in attempt_ids]
    case_ids = {row["case_id"] for row in rows}
    dataset = json.loads((evidence / "dataset.json").read_text())
    dataset["cases"] = [case for case in dataset["cases"] if case["id"] in case_ids]
    output.mkdir(parents=True)
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    (output / "results.jsonl").write_bytes(encode(rows))
    for row in rows:
        for relative, expected in artifact_bindings(row):
            source = contained_path(evidence, relative)
            if digest(source.read_bytes()) != expected:
                raise ValueError("Artifact differs from result binding: " + relative)
            target = contained_path(output, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return {"attempts": len(rows), "cases": len(case_ids)}


def finalize(base: Path, evidence: Path) -> dict:
    pending_path = evidence / RECEIPT
    receipt = json.loads(pending_path.read_text())
    pending = receipt.get("pending_request_classification")
    if not isinstance(pending, dict):
        raise ValueError("Request classification correction is not pending")
    old_hash = digest((base / "results.jsonl").read_bytes())
    if pending.get("input_results_sha256") != old_hash:
        raise ValueError("Final correction input is not the status-only evidence")
    dataset_hash = digest((evidence / "dataset.json").read_bytes())
    new_hash = digest((evidence / "results.jsonl").read_bytes())
    if pending.get("output_results_sha256") != new_hash:
        raise ValueError("Final correction results changed after classification")
    require_result_review(evidence, json.loads((evidence / "dataset.json").read_text()), records(evidence))
    at = datetime.now(timezone.utc).isoformat()
    receipt["request_classification"] = receipt.pop("pending_request_classification")
    receipt["finalized_at"] = at
    pending_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    rebind_references(evidence, dataset_hash, old_hash, new_hash,
                      "rebound_after_request_status_classification", at)
    append_binding(
        evidence, old_hash, at=at,
        detail="attempt identity, output bytes/hash, timestamps, elapsed time, metrics and task verdicts retained; seven request status/error/diagnosis fields corrected from exact saved non-2xx headers and independently re-reviewed",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("extract", "classify"):
        command = sub.add_parser(name)
        command.add_argument("base", type=Path)
        command.add_argument("output", type=Path)
    subset = sub.add_parser("subset")
    subset.add_argument("evidence", type=Path)
    subset.add_argument("output", type=Path)
    finish = sub.add_parser("finalize")
    finish.add_argument("base", type=Path)
    finish.add_argument("evidence", type=Path)
    args = parser.parse_args()
    if args.command == "extract": result = extract(args.base.resolve(), args.output.resolve())
    elif args.command == "classify": result = classify(args.base.resolve(), args.output.resolve())
    elif args.command == "subset": result = make_subset(args.evidence.resolve(), args.output.resolve())
    else: result = finalize(args.base.resolve(), args.evidence.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
