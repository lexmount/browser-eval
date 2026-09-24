"""Create a corrected local-audit bundle without making provider calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.local_audit import MANIFEST_NAME, local_audit_manifest
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.result_review import review_input_identity
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from scripts.rescore_content import rescore


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def rows(raw: bytes) -> dict[str, dict]:
    return {row["attempt_id"]: row for row in map(json.loads, raw.splitlines())}


def revise(source: Path, output: Path, canonical_dataset: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Corrected local-audit output already exists")
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Local-audit source must not contain symlinks")
    before_dataset = (source / "dataset.json").read_bytes()
    before_results = (source / "results.jsonl").read_bytes()
    before_rows = rows(before_results)
    shutil.copytree(source, output)
    shutil.copy2(canonical_dataset, output / "dataset.json")

    changes = rescore(output)
    after_dataset = (output / "dataset.json").read_bytes()
    after_results = (output / "results.jsonl").read_bytes()
    after_rows = rows(after_results)
    dataset = json.loads(after_dataset)
    cases = {case["id"]: case for case in dataset["cases"]}
    verdict_changes = []
    for attempt_id, before in before_rows.items():
        after = after_rows[attempt_id]
        kind = cases[after["case_id"]].get("task_kind", "body")
        if task_passed(before["ok"], before["metrics"], kind) != task_passed(
                after["ok"], after["metrics"], kind):
            verdict_changes.append(attempt_id)
    if changes or verdict_changes:
        raise ValueError("Scoring correction changed local-audit verdicts")

    review_path = output / "result-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    reviewed_ids = {row["attempt_id"] for row in review.get("attempts", [])}
    for attempt_id in reviewed_ids:
        if review_input_identity(before_rows[attempt_id]) != review_input_identity(
                after_rows[attempt_id]):
            raise ValueError("Scoring correction changed review-visible evidence")
    prior_bindings = dict(review.get("bindings", {}))
    review["bindings"]["dataset_sha256"] = digest(after_dataset)
    review["bindings"]["results_sha256"] = digest(after_results)
    review.setdefault("review_protocol", {})["rebound_after_verdict_neutral_correction"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "prior_dataset_sha256": digest(before_dataset),
        "prior_results_sha256": digest(before_results),
        "prior_review_bindings": prior_bindings,
        "provider_calls": 0,
        "condition": "attempt inventory, review-visible evidence and every task verdict unchanged",
    }
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

    preview_path = output / "local-preview.json"
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    preview["bindings"] = {
        "dataset.json": digest(after_dataset),
        "results.jsonl": digest(after_results),
    }
    preview["complete_inventory"] = MANIFEST_NAME
    preview_path.write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")

    correction_path = output / "local-evidence/local-audit-correction.json"
    correction = {
        "schema_version": 1,
        "kind": "verdict_neutral_local_audit_correction",
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": digest(before_dataset),
        "input_results_sha256": digest(before_results),
        "output_dataset_sha256": digest(after_dataset),
        "output_results_sha256": digest(after_results),
        "scoring_source_sha256": SCORING_SOURCE_SHA256,
        "provider_calls": 0,
        "task_verdict_changes": [],
        "changes": [
            "restore shared ordered-tree scoring and its paired controls",
            "correct non-judgmental Moli runtime status metadata to 1.1.9",
            "rebind the retained supplement review after review-visible evidence stayed unchanged",
        ],
    }
    correction_path.write_text(json.dumps(correction, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")

    manifest_path = output / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(local_audit_manifest(output), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return {
        "dataset_sha256": digest(after_dataset),
        "results_sha256": digest(after_results),
        "result_review_sha256": digest(review_path.read_bytes()),
        "manifest_sha256": digest(manifest_path.read_bytes()),
        "correction_sha256": digest(correction_path.read_bytes()),
        "scoring_source_sha256": SCORING_SOURCE_SHA256,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(revise(args.source, args.output, args.dataset), indent=2))
