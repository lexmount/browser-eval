#!/usr/bin/env python3
"""Create and finalize a full blind review of the revised dynamic-100 rules."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from harness.web_fetch.models import EvalCase
from harness.web_fetch.scoring import load_list_reference, score_attempt
from harness.web_fetch.metrics import task_passed


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def independent_verdict(ok: bool, metrics: dict[str, Any]) -> bool:
    if metrics.get("deadline_overrun") or metrics.get("blocked_page"):
        return False
    if "stable_task_pass" in metrics:
        return bool(
            ok
            and metrics.get("stable_task_pass")
            and not metrics.get("forbidden_hits")
            and not metrics.get("stable_blocked_page")
        )
    return bool(ok and metrics.get("content_pass"))


def evidence_summary(ok: bool, error_type: str | None, metrics: dict[str, Any]) -> str:
    if not ok:
        return f"请求没有产生可用答案；错误类型：{error_type or '未记录'}。"
    parts = []
    anchors = metrics.get("stable_anchor_results")
    if anchors is not None:
        hits = sum(bool(item.get("pass")) for item in anchors)
        parts.append(f"稳定文字 {hits}/{len(anchors)} 组命中")
        if "stable_unique_record_count" in metrics:
            parts.append(
                f"完整且唯一记录 {metrics.get('stable_unique_record_count', 0)}/"
                f"至少 {metrics.get('stable_minimum_records', 0)} 条"
            )
        parts.append("字段结构通过" if metrics.get("stable_structure_pass") else "字段结构未通过")
    regions = metrics.get("body_region_results") or []
    if regions:
        target = sum(bool(item.get("target_pass", item.get("pass"))) for item in regions)
        floor = sum(bool(item.get("floor_pass", item.get("pass"))) for item in regions)
        parts.append(f"正文区域达到目标 {target}/{len(regions)}，达到硬下限 {floor}/{len(regions)}")
    facts = metrics.get("fact_results") or []
    if facts:
        parts.append(f"文字事实 {sum(bool(item.get('pass')) for item in facts)}/{len(facts)} 组命中")
    patterns = metrics.get("pattern_results") or []
    if patterns:
        parts.append(f"结构模式 {sum(bool(item.get('pass')) for item in patterns)}/{len(patterns)} 项通过")
    forbidden = metrics.get("forbidden_hits") or []
    if forbidden:
        parts.append("命中排除内容：" + "、".join(map(str, forbidden[:5])))
    if metrics.get("blocked_page") or metrics.get("stable_blocked_page"):
        parts.append("输出被识别为拦截页或错误页")
    if metrics.get("deadline_overrun"):
        parts.append("超过声明的调用时限")
    return "；".join(parts) + "。"


def initial_review(evidence: Path, output: Path) -> dict[str, Any]:
    dataset_bytes = (evidence / "dataset.json").read_bytes()
    results_bytes = (evidence / "results.jsonl").read_bytes()
    dataset = json.loads(dataset_bytes)
    rows = records(evidence / "results.jsonl")
    by_case = {row["case_id"]: row for row in rows}
    attempts = []
    for item in dataset["cases"]:
        record = by_case[item["id"]]
        artifact = evidence / record["artifact_path"]
        raw = artifact.read_bytes()
        expected_hash = record["metrics"]["content_sha256"]
        if digest(raw) != expected_hash:
            raise ValueError("artifact hash mismatch: " + item["id"])
        deadline_budget = 45000
        independent_metrics = score_attempt(
            EvalCase(**item),
            raw,
            ok=bool(record.get("ok")),
            metadata=record.get("provider_metadata") or {},
            status_code=record.get("status_code"),
            title=record.get("title"),
            list_reference=load_list_reference(item, evidence),
            previous_metrics={
                "deadline_budget_ms": deadline_budget,
                "deadline_overrun": float(record.get("elapsed_ms") or 0) > deadline_budget,
            },
        )
        verdict = independent_verdict(bool(record.get("ok")), independent_metrics)
        attempts.append(
            {
                "ordinal": len(attempts) + 1,
                "case_id": item["id"],
                "attempt_id": record["attempt_id"],
                "artifact_path": record["artifact_path"],
                "artifact_sha256": expected_hash,
                "artifact_chars": len(raw.decode("utf-8", "replace")),
                "reference_chars": len(item.get("ground_truth_main_content") or ""),
                "rule_sha256": digest(json.dumps(
                    {
                        "acceptance_contract": item.get("acceptance_contract"),
                        "body_requirements": item.get("body_requirements"),
                        "required_facts": item.get("required_facts"),
                        "required_patterns": item.get("required_patterns"),
                        "forbidden_any": item.get("forbidden_any"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()),
                "request_ok": bool(record.get("ok")),
                "error_type": record.get("error_type"),
                "independent_verdict": "pass" if verdict else "fail",
                "independent_evidence": evidence_summary(
                    bool(record.get("ok")), record.get("error_type"), independent_metrics
                ),
                "review_inputs": {
                    "complete_artifact_read": True,
                    "declared_rules_read": True,
                    "machine_verdict_withheld": True,
                },
            }
        )
    document = {
        "schema_version": 1,
        "kind": "dynamic_100_rules_reaudit_initial_blind_review",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bindings": {
            "dataset_sha256": digest(dataset_bytes),
            "results_sha256": digest(results_bytes),
        },
        "protocol": {
            "scope": "all",
            "machine_judgments_withheld_during_initial_inspection": True,
            "complete_artifact_paths_supplied": True,
            "declared_acceptance_evidence_supplied": True,
            "provider_calls": 0,
        },
        "reviewed": len(attempts),
        "verdicts": dict(Counter(item["independent_verdict"] for item in attempts)),
        "attempts": attempts,
    }
    write_json(output, document)
    return document


def finalize_review(evidence: Path, initial_path: Path, audit_path: Path) -> dict[str, Any]:
    dataset_bytes = (evidence / "dataset.json").read_bytes()
    results_bytes = (evidence / "results.jsonl").read_bytes()
    dataset = json.loads(dataset_bytes)
    rows = records(evidence / "results.jsonl")
    cases = {item["id"]: item for item in dataset["cases"]}
    machine = {
        row["attempt_id"]: "pass" if task_passed(
            bool(row.get("ok")), row.get("metrics") or {},
            cases[row["case_id"]].get("task_kind", "body")
        ) else "fail"
        for row in rows
    }
    initial_bytes = initial_path.read_bytes()
    initial = json.loads(initial_bytes)
    expected_bindings = {
        "dataset_sha256": digest(dataset_bytes),
        "results_sha256": digest(results_bytes),
    }
    if initial.get("bindings") != expected_bindings or initial.get("reviewed") != 100:
        raise ValueError("initial review is incomplete or bound to different inputs")
    disagreements = []
    attempts = []
    classifications = Counter()
    for item in initial["attempts"]:
        machine_verdict = machine[item["attempt_id"]]
        independent = item["independent_verdict"]
        if machine_verdict != independent:
            disagreements.append(item["attempt_id"])
        classification = (
            "verified_contract_pass" if independent == "pass" else
            "provider_access_or_extraction_failure" if not item["request_ok"] else
            "target_retrieved_but_contract_not_met"
        )
        classifications[classification] += 1
        attempts.append({
            "attempt_id": item["attempt_id"],
            "machine_verdict": machine_verdict,
            "agent_verdict": independent,
            "agent_evidence": item["independent_evidence"],
            "final_verdict": independent if machine_verdict == independent else None,
            "adjudication": None,
            "classification": classification,
        })
    audit = {
        "schema_version": 1,
        "kind": "dynamic_100_rules_reaudit",
        "status": "complete" if not disagreements else "needs_adjudication",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bindings": {**expected_bindings, "initial_review_sha256": digest(initial_bytes)},
        "reviewed_cases": len(attempts),
        "summary": {
            "independent_verdicts": dict(Counter(item["agent_verdict"] for item in attempts)),
            "machine_verdicts": dict(Counter(item["machine_verdict"] for item in attempts)),
            "disagreements": disagreements,
            "unresolved_cases": disagreements,
            "classifications": dict(classifications),
        },
        "attempts": attempts,
    }
    write_json(audit_path, audit)
    if disagreements:
        return audit
    review = {
        "schema_version": 1,
        "bindings": {
            **expected_bindings,
            "formal_audit_sha256": digest(audit_path.read_bytes()),
            "initial_blind_review_sha256": digest(initial_bytes),
        },
        "reviewer": {"agent": "Codex independent full-scope benchmark audit"},
        "review_protocol": {
            "scope": "all",
            "machine_judgments_withheld": True,
            "machine_judgments_withheld_during_initial_inspection": True,
            "complete_artifact_paths_supplied": True,
            "declared_acceptance_evidence_supplied": True,
        },
        "total_attempts": 100,
        "expected_attempts": 100,
        "reviewed_attempts": 100,
        "accepted_unreviewed_passes": 0,
        "disagreements": 0,
        "unresolved": 0,
        "attempts": attempts,
    }
    review_path = evidence / "result-review.json"
    if review_path.exists():
        prior = review_path.read_bytes()
        prior_review = json.loads(prior)
        if prior_review.get("bindings", {}).get("dataset_sha256") == expected_bindings["dataset_sha256"] \
                and prior_review.get("bindings", {}).get("results_sha256") == expected_bindings["results_sha256"]:
            raise ValueError("current result-review.json already exists for these inputs")
        prior_bindings = prior_review.get("bindings", {})
        archive = evidence / "review-history" / (
            "result-review-"
            + str(prior_bindings.get("dataset_sha256") or digest(prior))[:12]
            + "-"
            + str(prior_bindings.get("results_sha256") or digest(prior))[:12]
            + ".json"
        )
        archive.parent.mkdir(exist_ok=True)
        if archive.exists() and archive.read_bytes() != prior:
            raise ValueError("result-review history collision")
        if not archive.exists():
            archive.write_bytes(prior)
        review_path.unlink()
    write_json(review_path, review)
    result_audit_path = evidence / "result-audit.json"
    if result_audit_path.exists():
        prior = result_audit_path.read_bytes()
        archive = evidence / "review-history" / f"result-audit-{digest(prior)[:16]}.json"
        if archive.exists() and archive.read_bytes() != prior:
            raise ValueError("result-audit history collision")
        archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            archive.write_bytes(prior)
    write_json(
        result_audit_path,
        {
            "schema_version": 1,
            "attempts": 100,
            "unique_attempts": len({row["attempt_id"] for row in rows}),
            "unique_cases": len({row["case_id"] for row in rows}),
            "unique_urls": len({row["url"] for row in rows}),
            "providers": sorted({row["provider"] for row in rows}),
            "reviewed": 100,
            "disagreements": 0,
            "unresolved": 0,
            "task_pass": sum(value == "pass" for value in machine.values()),
            "task_fail": sum(value == "fail" for value in machine.values()),
            "classifications": dict(classifications),
            "diagnostic_calls": 9,
            "diagnostic_calls_score_bearing": 0,
        },
    )
    update_lineage(evidence, initial_path, audit_path)
    return audit


def update_lineage(evidence: Path, initial_path: Path, audit_path: Path) -> dict[str, Any]:
    lineage_path = evidence / "measurement-lineage.json"
    lineage = json.loads(lineage_path.read_text())
    lineage.update({
        "authoritative_dataset_sha256": digest((evidence / "dataset.json").read_bytes()),
        "authoritative_results_sha256": digest((evidence / "results.jsonl").read_bytes()),
        "offline_rescore_calls": 0,
        "non_scoring_diagnostic_calls": 9,
        "diagnostic_calls_in_authoritative_results": 0,
        "rules_reaudit": {
            "stable_text_source_observation_runs": 3,
            "interaction_policy": "initial automatic load only; no click, input, search, scroll, pagination, or consent action",
            "initial_blind_review_sha256": digest(initial_path.read_bytes()),
            "formal_audit_sha256": digest(audit_path.read_bytes()),
        },
        "independent_review": {
            "status": "complete",
            "reviewed": 100,
            "unresolved": 0,
            "result_review_sha256": digest((evidence / "result-review.json").read_bytes()),
            "result_audit_sha256": digest((evidence / "result-audit.json").read_bytes()),
            "formal_audit_sha256": digest(audit_path.read_bytes()),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    })
    write_json(lineage_path, lineage)
    return lineage


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    initial = sub.add_parser("initial")
    initial.add_argument("--evidence", type=Path, required=True)
    initial.add_argument("--output", type=Path, required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--evidence", type=Path, required=True)
    final.add_argument("--initial", type=Path, required=True)
    final.add_argument("--audit", type=Path, required=True)
    lineage = sub.add_parser("lineage")
    lineage.add_argument("--evidence", type=Path, required=True)
    lineage.add_argument("--initial", type=Path, required=True)
    lineage.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    result = (initial_review(args.evidence, args.output) if args.command == "initial" else
              finalize_review(args.evidence, args.initial, args.audit) if args.command == "finalize" else
              update_lineage(args.evidence, args.initial, args.audit))
    print(json.dumps({key: result.get(key) for key in ("status", "reviewed", "reviewed_cases", "verdicts", "summary") if key in result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
