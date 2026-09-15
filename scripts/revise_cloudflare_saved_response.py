"""Correct saved request/content semantics without calling providers.

No provider is called. The correction is staged until the changed attempts have
been blindly reviewed and the complete corrected matrix passes anomaly review.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.failure_diagnosis import diagnose
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.providers.cloudflare import _interface_error, _origin_status
from harness.web_fetch.providers.moli import _markdown_http_error_status
from harness.web_fetch.reference_lifecycle import scoring_revision_history_sha256, valid_scoring_revision
from harness.web_fetch.result_review import require_result_review
from harness.web_fetch.report import failure_category
from harness.web_fetch.response_input import apply_request_status
from harness.web_fetch.safety import contained_path
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256, load_list_reference, score_attempt
from scripts.revise_saved_request_status import rebind_references


PROVIDERS = ("cloudflare-markdown", "moli-webfetch")
RECEIPT = "request-status-corrections.json"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def records(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "results.jsonl").read_text().splitlines()]


def encode(rows: list[dict]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows).encode()


def reference_revision_path(root: Path, start: tuple[str, str],
                            target: tuple[str, str]) -> bool:
    """Return whether audited reference transitions connect two evidence states."""
    path = root / "reference-revisions.json"
    if not path.is_file():
        return False
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for receipt in json.loads(path.read_text()):
        for transition in receipt.get("revision_transitions", []):
            before = (transition.get("input_dataset_sha256"),
                      transition.get("input_results_sha256"))
            after = (transition.get("output_dataset_sha256"),
                     transition.get("output_results_sha256"))
            if all(before) and all(after):
                edges.setdefault(before, set()).add(after)
    seen = {start}
    frontier = [start]
    while frontier:
        state = frontier.pop()
        for next_state in edges.get(state, set()):
            if next_state == target:
                return True
            if next_state not in seen:
                seen.add(next_state)
                frontier.append(next_state)
    return start == target


def correct(base: Path, output: Path, *, expected_changes: int = 14) -> dict:
    if output.exists():
        raise ValueError("Destination exists: " + str(output))
    shutil.copytree(base, output)
    dataset_bytes = (output / "dataset.json").read_bytes()
    dataset = json.loads(dataset_bytes)
    cases = {case["id"]: case for case in dataset["cases"]}
    before_results = (output / "results.jsonl").read_bytes()
    rows = records(output)
    changes = []
    scored_at = datetime.now(timezone.utc).isoformat()
    scoring_audit = {
        "mode": "offline_saved_response_semantics_correction",
        "scored_at": scored_at,
        "source_sha256": digest(b"\0".join([
            Path(__file__).read_bytes(), SCORING_SOURCE_SHA256.encode("ascii"),
        ])),
        "dataset_sha256": digest(dataset_bytes),
        "input_results_sha256": digest(before_results),
        "request_status_basis": "provider_owned_saved_response_outcome",
        "rule_revision": False,
        "reference_revision": False,
    }
    for row in rows:
        metadata = row.get("provider_metadata", {})
        provider = row.get("provider")
        if provider not in PROVIDERS:
            continue
        artifact = contained_path(output, row["artifact_path"])
        current_content = artifact.read_bytes()
        if digest(current_content) != row.get("metrics", {}).get("content_sha256"):
            raise ValueError("Output integrity failure: " + row["attempt_id"])
        raw_sha256 = None
        if provider == "cloudflare-markdown":
            raw_relative = metadata.get("raw_response_path")
            if not raw_relative:
                continue
            raw = contained_path(output, raw_relative).read_bytes()
            if digest(raw) != metadata.get("raw_response_sha256"):
                raise ValueError("Raw response integrity failure: " + row["attempt_id"])
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Saved Cloudflare envelope is not an object: " + row["attempt_id"])
            envelope_success = payload.get("success") is True
            result = payload.get("result") if isinstance(payload.get("result"), str) else ""
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            origin_status = _origin_status(meta.get("status"))
            if origin_status != metadata.get("upstream_status"):
                raise ValueError("Saved origin status differs from public metadata: " + row["attempt_id"])
            error_type, error_message = _interface_error(
                response_status=int(row.get("status_code") or 0),
                envelope_success=envelope_success,
                origin_status=origin_status,
            )
            expected_ok = error_type is None
            expected_content = result.encode("utf-8") if envelope_success else b""
            raw_sha256 = metadata["raw_response_sha256"]
        else:
            content = current_content.decode("utf-8", errors="strict")
            parsed_error_status = _markdown_http_error_status(content)
            empty_success = (
                metadata.get("exit_code") == 0
                and row.get("error_type") == "empty_output"
                and not current_content
            )
            explicit_http_failure = (
                metadata.get("exit_code") == 0
                and row.get("ok") is True
                and parsed_error_status is not None
            )
            if not (empty_success or explicit_http_failure):
                continue
            expected_ok = parsed_error_status is None
            error_type = None if expected_ok else "api_error"
            error_message = (
                None if expected_ok else f"Moli returned HTTP {parsed_error_status}"
            )
            expected_content = current_content
            raw_sha256 = metadata.get("raw_stdout_sha256")
            if parsed_error_status is not None:
                row["status_code"] = parsed_error_status
        current = (bool(row.get("ok")), row.get("error_type"), row.get("error_message"), current_content)
        corrected = (expected_ok, error_type, error_message, expected_content)
        if current == corrected:
            continue
        before_task = task_passed(
            bool(row.get("ok")), row["metrics"], cases[row["case_id"]].get("task_kind", "body"))
        before = {
            "ok": row.get("ok"), "error_type": row.get("error_type"),
            "error_message": row.get("error_message"),
            "content_sha256": row["metrics"].get("content_sha256"),
        }
        artifact.write_bytes(expected_content)
        row.update(ok=expected_ok, error_type=error_type, error_message=error_message)
        case = cases[row["case_id"]]
        row["metrics"] = score_attempt(
            case, expected_content, ok=expected_ok, status_code=row.get("status_code"),
            title=row.get("title"), metadata=metadata, previous_metrics=row["metrics"],
            list_reference=load_list_reference(case, output),
        )
        apply_request_status(row)
        row["diagnosis"] = diagnose(row, case, expected_content.decode("utf-8", errors="replace"))
        row["failure_category"] = failure_category(row, case)
        row["scoring"] = {**row.get("scoring", {}), **scoring_audit}
        after_task = task_passed(
            bool(row.get("ok")), row["metrics"], case.get("task_kind", "body"))
        if before_task or after_task:
            raise ValueError(row["attempt_id"] + ": saved-envelope correction changed a task verdict")
        changes.append({
            "attempt_id": row["attempt_id"], "case_id": row["case_id"],
            "provider": provider,
            "source_artifact_sha256": raw_sha256,
            "before": before,
            "after": {
                "ok": row.get("ok"), "error_type": row.get("error_type"),
                "error_message": row.get("error_message"),
                "content_sha256": row["metrics"].get("content_sha256"),
            },
            "task_verdict_changed": False,
        })
    if len(changes) != expected_changes:
        raise ValueError(f"Expected {expected_changes} saved-result corrections, found {len(changes)}")
    after_results = encode(rows)
    (output / "results.jsonl").write_bytes(after_results)
    (output / "result-review.json").unlink(missing_ok=True)
    receipt = {
        "schema_version": 1,
        "providers": list(PROVIDERS),
        "phase": "pending-independent-review-and-anomaly-audit",
        "corrected_at": scored_at,
        "basis": "Cloudflare result/success/meta.status from hash-bound envelopes; Moli exit_code plus its bounded whole-response HTTP error envelope from immutable call records, with answer emptiness kept separate",
        "provider_calls": 0,
        "input_dataset_sha256": digest(dataset_bytes),
        "input_results_sha256": digest(before_results),
        "output_dataset_sha256": digest(dataset_bytes),
        "output_results_sha256": digest(after_results),
        "task_decision_changes": [],
        "changes": changes,
    }
    (output / RECEIPT).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def finalize(base: Path, evidence: Path) -> dict:
    receipt_path = evidence / RECEIPT
    receipt = json.loads(receipt_path.read_text())
    old_results = digest((base / "results.jsonl").read_bytes())
    dataset_hash = digest((evidence / "dataset.json").read_bytes())
    new_results = digest((evidence / "results.jsonl").read_bytes())
    staged = (receipt.get("output_dataset_sha256"),
              receipt.get("output_results_sha256"))
    current = (dataset_hash, new_results)
    if receipt.get("phase") != "pending-independent-review-and-anomaly-audit":
        raise ValueError("Saved-result correction is not pending")
    if (receipt.get("input_results_sha256") != old_results
            or (current != staged
                and not reference_revision_path(evidence, staged, current))):
        raise ValueError("Saved-result correction receipt is not bound to current evidence")
    dataset = json.loads((evidence / "dataset.json").read_text())
    rows = records(evidence)
    require_result_review(evidence, dataset, rows)
    at = datetime.now(timezone.utc).isoformat()
    receipt["phase"] = "finalized"
    receipt["finalized_at"] = at
    receipt["independent_result_review_sha256"] = digest(
        (evidence / "result-review.json").read_bytes())
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    rebind_references(
        evidence, staged[0], old_results, staged[1],
        "rebound_after_saved_response_semantics_correction", at,
    )
    history_path = evidence / "scoring-revisions.json"
    history = json.loads(history_path.read_text())
    history.append({
        "kind": "audited_assembled_scoring_revision",
        "scored_at": at,
        "input_dataset_sha256": staged[0],
        "input_results_sha256": old_results,
        "output_dataset_sha256": staged[0],
        "output_results_sha256": staged[1],
        "changed_cases": [],
        "task_decision_changes": [],
        "provider_calls": 0,
        "immutable_call_fields": "attempt identity, raw API envelopes, timestamps and elapsed time retained; normalized request status and public result bytes reconstructed from hash-bound envelopes",
        "source_scoring_receipts_sha256": scoring_revision_history_sha256(history),
        "independent_result_review_sha256": receipt["independent_result_review_sha256"],
        "request_status_correction_receipt": RECEIPT,
    })
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")
    if not valid_scoring_revision(evidence):
        raise ValueError("Corrected evidence has an invalid revision chain")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    correct_parser = sub.add_parser("correct")
    correct_parser.add_argument("base", type=Path)
    correct_parser.add_argument("output", type=Path)
    finish = sub.add_parser("finalize")
    finish.add_argument("base", type=Path)
    finish.add_argument("evidence", type=Path)
    args = parser.parse_args()
    result = (correct(args.base.resolve(), args.output.resolve())
              if args.command == "correct"
              else finalize(args.base.resolve(), args.evidence.resolve()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
