"""Build a private, hash-bound diagnostic inventory for the original 36 failures.

The input selection is an explicit private JSON file. This program does not choose
the best run, change an official score, or certify a 90-task benchmark cohort.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls

SCHEMA = "original36_latest_moli_diagnostic_index/7"
EXPECTED_BINARY_SHA256 = "af808b3f7bb5dd0604262bba95cecdf06ba18d9547a169c1443bfbb1255b4397"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def ref(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlink evidence: {path}")
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def evidence_path(receipt_path: Path, name: str, entry: object) -> Path:
    if isinstance(entry, dict):
        path = Path(str(entry["path"]))
        expected = entry["sha256"]
    elif isinstance(entry, str):
        path = receipt_path.parent / name
        expected = entry
    else:
        raise ValueError(f"invalid evidence entry {name}: {entry!r}")
    actual = ref(path)
    if actual["sha256"] != expected:
        raise ValueError(f"receipt evidence hash mismatch: {path}")
    return path


def trace_completion(
    path: Path, original_action_scope: bool = False, original_count: int = 0
) -> tuple[int, int, int]:
    trace = json.loads(path.read_text())
    if not isinstance(trace, list):
        raise ValueError(f"trace must be an array: {path}")
    scoped = [
        item
        for item in trace
        if isinstance(item, dict)
        and type(item.get("index")) is int
        and item["index"] >= 1
        and (not original_action_scope or item["index"] <= original_count)
    ]
    expected = original_count if original_action_scope else len(scoped)
    if sorted(item["index"] for item in scoped) != list(range(1, expected + 1)):
        label = "original indexed actions" if original_action_scope else "qualified indexed actions"
        raise ValueError(f"missing or duplicate {label} in {path}")
    completed = sum(
        item.get("tool_status", item.get("status")) == "success"
        for item in scoped
    )
    return len(scoped), completed, len(trace) - len(scoped)


def reviewed_screenshot(spec: dict | None) -> dict | None:
    if spec is None:
        return None
    if spec.get("kind") != "last_observed_page" or spec.get("privacy_reviewed") is not True:
        raise ValueError("public screenshot requires explicit last-page privacy review")
    path = Path(spec["path"])
    if path.stat().st_size > 5_000_000 or path.open("rb").read(8) != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"invalid public PNG: {path}")
    result = ref(path)
    if result["sha256"] != spec["sha256"]:
        raise ValueError(f"public screenshot hash mismatch: {path}")
    return {
        **result,
        "kind": spec["kind"],
        "privacy_reviewed": True,
        "caption_zh": spec["caption_zh"],
        "caption_en": spec["caption_en"],
    }


def validate_task243_menu_proof(path: Path, expected_sha256: str) -> dict[str, str]:
    proof_ref = ref(path)
    if proof_ref["sha256"] != expected_sha256:
        raise ValueError("task 243 menu proof hash mismatch")
    rows = json.loads(path.read_text())
    if not (
        isinstance(rows, list)
        and any(
            row.get("step") == 11
            and row.get("passed") is True
            and "menu module" in row.get("readiness_gate", "")
            for row in rows
        )
        and any(
            row.get("step") == 11
            and row.get("matched_count") == 1
            and row.get("matched_texts") == ["CUSTOMERS"]
            for row in rows
        )
        and any(row.get("step") == 12 and row.get("visible_match_count") == 1 for row in rows)
    ):
        raise ValueError("task 243 menu readiness/target proof incomplete")
    return proof_ref


def load_cause_audits(path: Path | None) -> tuple[dict[int, dict], dict | None]:
    if path is None:
        return {}, None
    manifest = json.loads(path.read_text())
    if manifest.get("schema") != "original36_cause_audits/1":
        raise ValueError("invalid cause audit manifest schema")
    audits = {}
    for entry in manifest.get("audits", []):
        task_id = entry.get("task_id")
        if not isinstance(task_id, int) or task_id in audits:
            raise ValueError("duplicate or invalid cause audit task ID")
        audit_path = Path(entry["audit"])
        audit_ref = ref(audit_path)
        audit = json.loads(audit_path.read_text())
        if audit.get("task_id") != task_id or not audit.get("reviewed_cause", {}).get("category"):
            raise ValueError(f"task {task_id}: cause audit identity missing")
        historical_ref = ref(Path(entry["historical_trace"]))
        if audit.get("historical_result", {}).get("trace_sha256") != historical_ref["sha256"]:
            raise ValueError(f"task {task_id}: historical trace differs from cause audit")
        evidence_refs = [ref(Path(value)) for value in entry.get("evidence_receipts", [])]
        audit_text = json.dumps(audit, sort_keys=True)
        if not evidence_refs or any(item["sha256"] not in audit_text for item in evidence_refs):
            raise ValueError(f"task {task_id}: unbound cause evidence receipt")
        cause = audit["reviewed_cause"]
        category = cause["category"]
        code_or_process = cause.get("code_or_process", category)
        if not all(
            isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]*", value)
            for value in (category, code_or_process)
        ):
            raise ValueError(f"task {task_id}: invalid structured cause classification")
        for key in ("mechanism_zh", "mechanism_en"):
            if not cause.get(key):
                raise ValueError(f"task {task_id}: missing bilingual cause mechanism")
        fix = entry.get("fix") or {}
        if not fix.get("zh") or not fix.get("en"):
            raise ValueError(f"task {task_id}: missing bilingual remediation scope")
        audits[task_id] = {
            "audit": audit_ref,
            "historical_trace": historical_ref,
            "evidence_receipts": evidence_refs,
            "cause": {
                "category": category,
                "code_or_process": code_or_process,
                "summary_zh": cause["mechanism_zh"],
                "summary_en": cause["mechanism_en"],
                "fix_zh": fix["zh"],
                "fix_en": fix["en"],
                "source_location_zh": entry["source_location"]["zh"],
                "source_location_en": entry["source_location"]["en"],
            },
        }
    return audits, ref(path)


def build(
    selection_path: Path, predecessor_path: Path, original_path: Path,
    cause_audits_path: Path | None = None,
) -> dict:
    selection = json.loads(selection_path.read_text())
    original = json.loads(original_path.read_text())
    expected_ids = sorted(int(item["task_id"]) for item in original["tasks"])
    entries = selection["tasks"]
    selected_ids = sorted(int(item["task_id"]) for item in entries)
    if len(entries) != len(set(selected_ids)) or selected_ids != expected_ids:
        raise ValueError("selection must contain exactly the original 36 task IDs")
    if len(entries) != 36:
        raise ValueError("expected exactly 36 original failures")
    cause_audits, cause_audits_ref = load_cause_audits(cause_audits_path)
    if set(cause_audits) - set(expected_ids):
        raise ValueError("cause audit outside original failure scope")
    if selection.get("binary_sha256") != EXPECTED_BINARY_SHA256:
        raise ValueError("fixed Moli binary mismatch")
    trajectories_path = Path(selection["frozen_trajectories"])
    site_map_path = Path(selection["remote_site_map"])
    site_map_ref = ref(site_map_path)
    remote_sites = load_remote_sites(site_map_path)
    frozen = json.loads(trajectories_path.read_text())
    original_counts = {
        int(row["task_id"]): len(row["tool_calls"]) for row in frozen["trajectories"]
    }
    original_calls = {int(row["task_id"]): row["tool_calls"] for row in frozen["trajectories"]}
    binary_ref = ref(Path(selection["binary_path"]))
    if binary_ref["sha256"] != EXPECTED_BINARY_SHA256:
        raise ValueError("fixed Moli binary bytes mismatch")
    source_binding_path = Path(selection["source_binding"])
    source_binding = json.loads(source_binding_path.read_text())
    source_head = source_binding.get("source_head")
    if not isinstance(source_head, str) or len(source_head) != 40:
        raise ValueError("missing Moli source HEAD")
    output_tasks = []
    for item in sorted(entries, key=lambda row: row["task_id"]):
        task_id = int(item["task_id"])
        if item["frozen_original_action_count"] != original_counts.get(task_id):
            raise ValueError(f"task {task_id}: frozen original action count mismatch")
        receipt_path = Path(item["receipt"])
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("task_id") != task_id:
            raise ValueError(f"task {task_id}: receipt task mismatch")
        binary = (
            receipt.get("binary_sha256")
            or receipt.get("moli_binary_sha256")
            or (receipt.get("input_sha256") or {}).get("moli_binary")
        )
        if binary is None and item.get("binary_binding"):
            binding = json.loads(Path(item["binary_binding"]).read_text())
            binary = binding.get("binary_sha256")
        if binary != EXPECTED_BINARY_SHA256:
            raise ValueError(f"task {task_id}: receipt binary mismatch")
        evidence = receipt.get("evidence") or receipt.get("evidence_sha256") or {}
        if not isinstance(evidence, dict):
            raise ValueError(f"task {task_id}: invalid evidence map")
        retained = {
            name: ref(evidence_path(receipt_path, name, entry)) for name, entry in evidence.items()
        }
        trace_name = item.get("action_evidence_name", "trace.json")
        if trace_name in retained:
            action_ref = retained[trace_name]
            trace_count, trace_completed, extra_count = trace_completion(
                Path(action_ref["path"]),
                item.get("action_index_scope") == "original_numeric_indices",
                item["frozen_original_action_count"],
            )
        else:
            action_ref = None
            trace_count = trace_completed = 0
            extra_count = 0
        evaluation_name = item.get("evaluation_name", "evaluation.json")
        evaluation_ref = retained.get(evaluation_name)
        if evaluation_ref is None:
            raise ValueError(f"task {task_id}: missing official evaluation evidence")
        evaluation = json.loads(Path(evaluation_ref["path"]).read_text())
        official = receipt.get("official_score")
        if official is None:
            official = receipt.get("official_score_for_synthetic_source_derived_response")
        if official not in (0, 0.0, 1, 1.0):
            raise ValueError(f"task {task_id}: missing numeric official score")
        if not isinstance(evaluation, dict):
            raise ValueError(f"task {task_id}: invalid official evaluation")
        if evaluation.get("task_id") != task_id or evaluation.get("score") != official:
            raise ValueError(f"task {task_id}: official evaluation/receipt mismatch")
        menu_proof = None
        if (
            task_id == 243
            and receipt.get("schema") == "task243_semantic_gated_candidate_diagnostic/1"
        ):
            menu_proof = validate_task243_menu_proof(
                receipt_path.parent / "menu-target-proof.json",
                receipt["menu_target_proof_sha256"],
            )
            if receipt.get("identity_verified") is not False:
                raise ValueError("task 243 guest identity cannot be claimed verified")
            if receipt.get("candidate_calls_sha256") == receipt.get("original_calls_sha256"):
                raise ValueError("task 243 corrected input must differ from red input")
        additional = [ref(Path(path)) for path in item.get("additional_evidence", [])]
        cause_audit = cause_audits.get(task_id)
        if cause_audit:
            if item.get("cause", selection["defaults"]["cause"])["category"] != "unresolved":
                raise ValueError(f"task {task_id}: cause audit would overwrite reviewed cause")
            additional.extend(
                [cause_audit["audit"], cause_audit["historical_trace"]]
                + cause_audit["evidence_receipts"]
            )
        exact_original = None
        exact_path = selection.get("exact_original_action_receipts_by_task", {}).get(str(task_id))
        if exact_path:
            exact_receipt_path = Path(exact_path)
            exact_receipt = json.loads(exact_receipt_path.read_text())
            exact_trace_path = exact_receipt_path.parent / "trace.json"
            exact_trace = json.loads(exact_trace_path.read_text())
            canonical = original_calls[task_id]
            if (
                exact_receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
                or exact_receipt.get("task_id") != task_id
                or exact_receipt.get("status") != "exact_actions_complete"
                or exact_receipt.get("promotable") is not False
                or exact_receipt.get("official_evaluation") != "not_run"
                or exact_receipt.get("site_reset") is not False
                or exact_receipt.get("frozen_action_count") != len(canonical)
                or exact_receipt.get("completed_action_count") != len(canonical)
                or exact_receipt.get("frozen_calls_sha256") != json_sha256(canonical)
                or exact_receipt.get("input_sha256", {}).get("binary")
                != EXPECTED_BINARY_SHA256
                or exact_receipt.get("input_sha256", {}).get("dataset")
                != sha256(trajectories_path)
                or exact_receipt.get("input_sha256", {}).get("site_map")
                != site_map_ref["sha256"]
                or exact_receipt.get("trace_sha256") != sha256(exact_trace_path)
                or len(exact_trace) != len(canonical)
                or any(
                    row.get("index") != index or row.get("tool_status") != "success"
                    for index, row in enumerate(exact_trace, 1)
                )
            ):
                raise ValueError(f"task {task_id}: exact original action evidence differs")
            executed = [row["arguments"] for row in exact_trace]
            if json_sha256(executed) != exact_receipt.get("rebound_calls_sha256"):
                raise ValueError(f"task {task_id}: exact replayed calls differ")
            expected_rebound = rebind_remote_calls(
                canonical, remote_sites, {"192.168.5.2", "host.docker.internal"}
            )
            if executed != expected_rebound:
                raise ValueError(f"task {task_id}: original action arguments changed")
            exact_original = {
                "receipt": ref(exact_receipt_path),
                "action_evidence": ref(exact_trace_path),
                "completed_actions": len(canonical),
                "original_actions": len(canonical),
                "official_evaluation": "not_run",
                "final_url": exact_receipt["final_url"],
            }
        failed_original = None
        failed_path = selection.get("failed_original_action_receipts_by_task", {}).get(
            str(task_id)
        )
        if failed_path:
            failed_receipt_path = Path(failed_path)
            failed_receipt = json.loads(failed_receipt_path.read_text())
            failed_trace_path = failed_receipt_path.parent / "trace.json"
            failed_trace = json.loads(failed_trace_path.read_text())
            canonical = original_calls[task_id]
            failed_index = {173: 4, 243: 13}.get(task_id)
            expected_rebound = rebind_remote_calls(
                canonical, remote_sites, {"192.168.5.2", "host.docker.internal"}
            )
            if (
                failed_index is None
                or failed_receipt.get("schema") != "remote_exact_frozen_actions_diagnostic/1"
                or failed_receipt.get("task_id") != task_id
                or failed_receipt.get("status") != "diagnostic_incomplete"
                or failed_receipt.get("promotable") is not False
                or failed_receipt.get("official_evaluation") != "not_run"
                or failed_receipt.get("frozen_calls_sha256") != json_sha256(canonical)
                or failed_receipt.get("input_sha256", {}).get("binary")
                != EXPECTED_BINARY_SHA256
                or failed_receipt.get("input_sha256", {}).get("dataset")
                != sha256(trajectories_path)
                or failed_receipt.get("input_sha256", {}).get("site_map")
                != site_map_ref["sha256"]
                or failed_receipt.get("trace_sha256") != sha256(failed_trace_path)
                or failed_receipt.get("viewport", {}).get("requested")
                != {"width": 1280, "height": 900}
                or failed_receipt.get("completed_action_count") != failed_index - 1
                or len(failed_trace) != failed_index
                or any(
                    row.get("index") != index or row.get("tool_status") != "success"
                    for index, row in enumerate(failed_trace[:-1], 1)
                )
                or failed_trace[-1].get("index") != failed_index
                or failed_trace[-1].get("tool_status") != "error"
                or [row.get("arguments") for row in failed_trace]
                != expected_rebound[:failed_index]
                or "Timeout 60000ms exceeded" not in failed_trace[-1].get("result", "")
            ):
                raise ValueError(f"task {task_id} fixed-binary original red evidence differs")
            failed_original = {
                "receipt": ref(failed_receipt_path),
                "action_evidence": ref(failed_trace_path),
                "completed_actions": failed_index - 1,
                "failed_action_index": failed_index,
                "original_actions": len(canonical),
                "official_evaluation": "not_run",
                "failed_action_error": failed_trace[-1]["result"],
            }
        sort_menu_visual = None
        visual = selection.get("sort_menu_visual_by_task", {}).get(str(task_id))
        if task_id == 29 and visual:
            if not additional:
                raise ValueError("task 29 sort-menu visual requires geometry receipt")
            geometry = json.loads(Path(additional[0]["path"]).read_text())
            images = {}
            for lane, key in (("before", "moli_e688_1_1_6"), ("after", "moli_fixed_af808")):
                spec = visual[lane]
                image = reviewed_screenshot(spec)
                if image["sha256"] != geometry["results"][key]["observations"]["29"][
                    "screenshot_sha256"
                ]:
                    raise ValueError(f"task 29 {lane} visual differs from geometry receipt")
                images[lane] = image
            sort_menu_visual = images
        failed_replay = None
        if item.get("failed_physical_replay"):
            failed_path = Path(item["failed_physical_replay"])
            failed_receipt = json.loads(failed_path.read_text())
            failed_binary = (failed_receipt.get("input_sha256") or {}).get("moli_binary")
            if failed_receipt.get("task_id") != task_id or failed_binary != EXPECTED_BINARY_SHA256:
                raise ValueError(f"task {task_id}: failed replay identity mismatch")
            failed_trace = evidence_path(
                failed_path, "trace.json", failed_receipt["evidence_sha256"]["trace.json"]
            )
            failed_trace_rows = json.loads(failed_trace.read_text())
            failed_row = next(
                (row for row in failed_trace_rows if row.get("tool_status") == "error"), None
            )
            if failed_row is None:
                raise ValueError(f"task {task_id}: missing failed action in red trace")
            failed_replay = {
                "receipt": ref(failed_path),
                "action_evidence": ref(failed_trace),
                "status": failed_receipt["status"],
                "qualified_action_count": item["frozen_original_action_count"],
                "completed_qualified_actions": sum(
                    row.get("tool_status", row.get("status")) == "success"
                    for row in failed_trace_rows
                ),
                "failed_action_index": failed_row["index"],
                "failed_action_error": failed_row["result"],
            }
            if failed_replay["status"] != "diagnostic_incomplete":
                raise ValueError(f"task {task_id}: expected incomplete red evidence")
            if task_id == 243 and receipt.get(
                "original_red_test_actions_sha256"
            ) != failed_receipt.get("original_calls_sha256"):
                raise ValueError("task 243 corrected receipt does not bind original red calls")
        revised_attempts = []
        for revised_spec in selection.get("revised_attempts_by_task", {}).get(str(task_id), []):
            revised_path = Path(revised_spec["receipt"])
            revised = json.loads(revised_path.read_text())
            revised_binary = (revised.get("input_sha256") or {}).get("moli_binary")
            if revised.get("task_id") != task_id or revised_binary != EXPECTED_BINARY_SHA256:
                raise ValueError(f"task {task_id}: revised attempt identity mismatch")
            revised_trace = evidence_path(
                revised_path, "trace.json", revised["evidence_sha256"]["trace.json"]
            )
            trace_rows = json.loads(revised_trace.read_text())
            if not isinstance(trace_rows, list):
                raise ValueError(f"task {task_id}: revised trace must be a list")
            revised_attempts.append(
                {
                    "revision_kind": revised_spec["revision_kind"],
                    "receipt": ref(revised_path),
                    "action_evidence": ref(revised_trace),
                    "status": revised["status"],
                    "qualified_action_count": item["frozen_original_action_count"],
                    "completed_qualified_actions": sum(
                        row.get("tool_status", row.get("status")) == "success" for row in trace_rows
                    ),
                    "runner_guard_error": revised.get("error"),
                    "official_evaluated": bool(revised.get("official_score") is not None),
                }
            )
        timeline = []
        timeline_ref = None
        if task_id == 465:
            if len(additional) != 1:
                raise ValueError("task 465 requires independent persistence sidecar")
            sidecar = json.loads(Path(additional[0]["path"]).read_text())
            if sidecar.get("task_id") != 465:
                raise ValueError("task 465 sidecar task mismatch")
            source_hashes = sidecar["sha256"]
            linked = {
                "physical_receipt": ref(receipt_path)["sha256"],
                "har": retained["network.har"]["sha256"],
                "official_evaluation": evaluation_ref["sha256"],
                "post_sql": ref(Path(selection["task465_post_sql"]))["sha256"],
                "post_pod": ref(Path(selection["task465_post_pod"]))["sha256"],
            }
            if any(source_hashes.get(name) != digest for name, digest in linked.items()):
                raise ValueError("task 465 sidecar evidence mismatch")
            gate = sidecar["gate_preflight"]
            outcome = sidecar["physical_result"]
            if not (
                sidecar["original_actions_completed"] == 8
                and sidecar["write_action_attempts"] == 1
                and gate["customer_data_amd_defined_before"] is False
                and gate["deferred_state_before"] == "pending"
                and gate["deferred_state_after"] == "resolved"
                and outcome["official_score"] == 1
                and outcome["wishlist_post_status"] == 302
                and outcome["product_id"] == 71337
                and outcome["customer_id"] == 27
            ):
                raise ValueError("task 465 sidecar does not establish stated outcome")
            timeline_ref = additional[0]
            timeline = [
                {
                    "label_zh": "站点数据尚未就绪",
                    "label_en": "Site data not yet ready",
                    "value_zh": "模块未定义，等待状态 pending",
                    "value_en": "Module undefined; Deferred pending",
                },
                {
                    "label_zh": "数据就绪",
                    "label_en": "Site data ready",
                    "value_zh": f"Deferred 在 {gate['elapsed_ms']} 毫秒后 resolved",
                    "value_en": f"Deferred resolved after {gate['elapsed_ms']} ms",
                },
                {
                    "label_zh": "一次点击触发写入",
                    "label_en": "One click triggered mutation",
                    "value_zh": f"wishlist POST 返回 {outcome['wishlist_post_status']}",
                    "value_en": f"Wishlist POST returned {outcome['wishlist_post_status']}",
                },
                {
                    "label_zh": "结果持久化并通过官方评分",
                    "label_en": "Persistent result and official pass",
                    "value_zh": (
                        f"商品 {outcome['product_id']} 已加入愿望清单；"
                        f"客户 {outcome['customer_id']}；"
                        f"官方评分 {outcome['official_score']}"
                    ),
                    "value_en": (
                        f"Product {outcome['product_id']} persisted in customer "
                        f"{outcome['customer_id']}'s wishlist; official score "
                        f"{outcome['official_score']}"
                    ),
                },
            ]
        identity_neutral_ui = None
        if task_id == 243 and item.get("identity_neutral_ui_receipt"):
            ui_path = Path(item["identity_neutral_ui_receipt"])
            ui_receipt_ref = ref(ui_path)
            ui_receipt = json.loads(ui_path.read_text())
            grid_path = ui_path.parent / "customer-grid-observation.json"
            grid_ref = ref(grid_path)
            grid = json.loads(grid_path.read_text())
            derivation = ui_receipt.get("ui_source_derivation") or {}
            if not (
                ui_receipt.get("schema") == "task243_revised_ui_diagnostic/1"
                and ui_receipt.get("status") == "diagnostic_complete"
                and ui_receipt.get("task_id") == 243
                and ui_receipt.get("engine") == "moli"
                and ui_receipt.get("physical_execution_count") == 1
                and ui_receipt.get("ui_contract_verdict") == "pass"
                and ui_receipt.get("official_score_effect") == "none"
                and ui_receipt.get("identity_verified") is False
                and ui_receipt.get("promotable") is False
                and ui_receipt.get("input_hashes_stable") is True
                and ui_receipt.get("runner_stable") is True
                and len(ui_receipt.get("steps") or []) == 14
                and all(step.get("tool_status") == "success" for step in ui_receipt["steps"])
                and ui_receipt.get("customer_grid", {}).get("evidence_sha256")
                == grid_ref["sha256"]
                and grid.get("total") == grid.get("rowCount") == 70
                and grid.get("activeFilters") == []
                and grid.get("tableIndex") == 1
                and len(derivation.get("exact_name_matches") or []) == 1
                and derivation.get("author_identity_claimed") is False
                and derivation["exact_name_matches"][0].get("name") == "Hannah Lim"
                and derivation["exact_name_matches"][0].get("email")
                == ui_receipt.get("revised_answer_from_ui")
            ):
                raise ValueError("task 243 identity-neutral UI evidence differs")
            identity_neutral_ui = {
                "receipt": ui_receipt_ref,
                "grid": grid_ref,
                "qualified_actions": 14,
                "customer_rows": 70,
                "exact_name_matches": 1,
                "author_identity_claimed": False,
                "official_score_effect": "none",
            }
        response_origin = receipt.get("response_origin")
        canonical_replayed = item.get("canonical_actions_replayed")
        if not isinstance(canonical_replayed, bool):
            raise ValueError(f"task {task_id}: declare canonical replay status")
        if canonical_replayed and (not action_ref or trace_completed != trace_count):
            raise ValueError(f"task {task_id}: incomplete replay trace")
        if receipt.get("canonical_actions_replayed") is False and canonical_replayed:
            raise ValueError(f"task {task_id}: receipt says canonical actions not replayed")
        semantic = dict(
            item.get("semantic_verification", selection["defaults"]["semantic_verification"])
        )
        if semantic["status"] not in {"verified", "limited", "unverified"}:
            raise ValueError(f"task {task_id}: invalid semantic status")
        semantic.setdefault(
            "reason_zh", selection["defaults"]["semantic_verification"]["reason_zh"]
        )
        semantic.setdefault(
            "reason_en", selection["defaults"]["semantic_verification"]["reason_en"]
        )
        cause = dict(item.get("cause", selection["defaults"]["cause"]))
        if cause_audit:
            cause = dict(cause_audit["cause"])
        exact_interpretation = selection.get("exact_action_interpretation_by_task", {}).get(
            str(task_id)
        )
        if exact_interpretation:
            if exact_original is None or not all(
                exact_interpretation.get(key) for key in ("zh", "en")
            ):
                raise ValueError(f"task {task_id}: exact-action interpretation lacks evidence")
            if not cause_audit:
                cause["fix_zh"] = exact_interpretation["zh"]
                cause["fix_en"] = exact_interpretation["en"]
        source_location = selection.get("source_locations_by_task", {}).get(str(task_id))
        if source_location:
            if not all(source_location.get(key) for key in ("zh", "en")):
                raise ValueError(f"task {task_id}: source location must be bilingual")
            if not cause_audit:
                cause["source_location_zh"] = source_location["zh"]
                cause["source_location_en"] = source_location["en"]
        if not cause.get("category") or not cause.get("code_or_process"):
            raise ValueError(f"task {task_id}: cause must be explicit, even if unresolved")
        for key in ("summary_zh", "summary_en", "fix_zh", "fix_en"):
            cause.setdefault(key, selection["defaults"]["cause"][key])
        evaluation_mode = item.get("evaluation_mode", "actual_trajectory_output")
        if evaluation_mode not in {"actual_trajectory_output", "source_derived_synthetic"}:
            raise ValueError(f"task {task_id}: invalid evaluation mode")
        if (
            evaluation_mode == "source_derived_synthetic"
            and receipt.get("official_score_for_synthetic_source_derived_response") is None
        ):
            raise ValueError(f"task {task_id}: synthetic input not disclosed in receipt")
        reference_validity = item.get("reference_validity")
        if task_id == 243:
            if reference_validity != {
                "status": "unverifiable",
                "affected_configurations": ["chromium", "moli-1.1.3"],
                "diagnostic_answer_eligible": False,
                "historical_official_scores_unchanged": True,
            } or semantic["status"] != "unverified":
                raise ValueError("task 243 source-derived answer cannot validate guest identity")
        elif reference_validity is not None:
            raise ValueError(f"task {task_id}: undeclared reference-validity exception")
        output_tasks.append(
            {
                "task_id": task_id,
                "task_type": item.get("task_type", selection["defaults"]["task_type"]),
                "receipt": ref(receipt_path),
                "action_evidence": action_ref,
                "evaluation": evaluation_ref,
                "evidence": retained,
                "additional_evidence": additional,
                "qualified_action_count": trace_count,
                "completed_qualified_actions": trace_completed,
                "supplemental_trace_actions": extra_count,
                "frozen_original_action_count": item["frozen_original_action_count"],
                "canonical_actions_replayed": canonical_replayed,
                "action_completion": (
                    "complete_qualified"
                    if action_ref and trace_count == trace_completed
                    else "not_replayed"
                    if not action_ref
                    else "incomplete"
                ),
                "official_score": official,
                "receipt_status": receipt.get("status"),
                "response_origin": response_origin,
                "evaluation_mode": evaluation_mode,
                "reference_validity": reference_validity,
                "menu_target_proof": menu_proof,
                "identity_neutral_ui": identity_neutral_ui,
                "sort_menu_visual": sort_menu_visual,
                "exact_original_action_diagnostic": exact_original,
                "failed_original_action_diagnostic": failed_original,
                "input_revision": {
                    key: receipt[key]
                    for key in (
                        "source_candidate_id",
                        "source_calls_sha256",
                        "calls_binding_sha256",
                        "canonical_calls_sha256",
                        "rebound_calls_sha256",
                        "executed_calls_sha256",
                        "candidate_sha256",
                        "plan_sha256",
                    )
                    if receipt.get(key) is not None
                },
                "semantic_verification": semantic,
                "cause": cause,
                "event_timeline": timeline,
                "timeline_evidence_sha256": timeline_ref["sha256"] if timeline_ref else None,
                "public_screenshot": reviewed_screenshot(
                    item.get("public_screenshot")
                    or selection.get("public_screenshots", {}).get(str(task_id))
                ),
                "failed_replay": failed_replay,
                "revised_attempts": revised_attempts,
            }
        )
    return {
        "schema": SCHEMA,
        "promotable": False,
        "formal_release": False,
        "cohort_certified": False,
        "official_scores_modified": False,
        "predecessor": ref(predecessor_path),
        "original_scope": ref(original_path),
        "frozen_trajectories": ref(trajectories_path),
        "remote_site_map": site_map_ref,
        "selection": ref(selection_path),
        "cause_audits": cause_audits_ref,
        "binary": binary_ref,
        "source_binding": ref(source_binding_path),
        "source_head": source_head,
        "moli_binary_sha256": EXPECTED_BINARY_SHA256,
        "original_failed_task_ids": expected_ids,
        "tasks": output_tasks,
        "counts": {
            "original_failures": len(expected_ids),
            "official_one": sum(row["official_score"] == 1 for row in output_tasks),
            "official_one_on_actual_trajectory_output": sum(
                row["official_score"] == 1 and row["evaluation_mode"] == "actual_trajectory_output"
                for row in output_tasks
            ),
            "official_one_on_source_derived_synthetic": sum(
                row["official_score"] == 1 and row["evaluation_mode"] == "source_derived_synthetic"
                for row in output_tasks
            ),
            "complete_qualified_replays": sum(
                row["action_completion"] == "complete_qualified" for row in output_tasks
            ),
            "exact_canonical_replays": sum(
                row["canonical_actions_replayed"] for row in output_tasks
            ),
            "exact_original_action_diagnostics_complete": sum(
                row["exact_original_action_diagnostic"] is not None for row in output_tasks
            ),
            "failed_original_action_diagnostics": sum(
                row["failed_original_action_diagnostic"] is not None for row in output_tasks
            ),
            "semantic_verified": sum(
                row["semantic_verification"]["status"] == "verified" for row in output_tasks
            ),
            "unverifiable_references": sum(
                (row["reference_validity"] or {}).get("status") == "unverifiable"
                for row in output_tasks
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cause-audits", type=Path)
    args = parser.parse_args()
    output = build(args.selection, args.predecessor, args.original, args.cause_audits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=args.output.parent,
        prefix=".original36-index-",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, args.output)


if __name__ == "__main__":
    main()
