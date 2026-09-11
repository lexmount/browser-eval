#!/usr/bin/env python3
# ruff: noqa: E402, E501 -- repo imports follow path setup; audit prose stays whole
"""Install a complete 100-result review from 52 prior and 48 new blind reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import task_passed
from harness.web_fetch.result_review import require_result_review


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--review", type=Path, action="append", required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    prior = args.prior.resolve()
    dataset = json.loads((evidence / "dataset.json").read_text())
    records = [json.loads(line) for line in (evidence / "results.jsonl").read_text().splitlines()]
    cases = {case["id"]: case for case in dataset["cases"]}
    by_case = {row["case_id"]: row for row in records}

    prior_review = json.loads((prior / "result-review.json").read_text())
    prior_attempts = prior_review["attempts"]
    if {row["attempt_id"] for row in prior_attempts} != {row["attempt_id"] for row in records[:52]}:
        raise ValueError("Prior blind review does not cover the inherited 52 results")

    new_reviews = []
    for path in args.review:
        new_reviews.extend(json.loads(path.read_text()))
    expected_new = {row["case_id"] for row in records[52:]}
    review_ids = [row.get("case_id") for row in new_reviews]
    if len(review_ids) != len(set(review_ids)) or set(review_ids) != expected_new:
        raise ValueError("New blind reviews must cover each of the 48 new cases exactly once")

    attempts = list(prior_attempts)
    for blind in new_reviews:
        row = by_case[blind["case_id"]]
        machine = (
            "pass"
            if task_passed(
                row["ok"], row["metrics"], cases[row["case_id"]].get("task_kind", "body")
            )
            else "fail"
        )
        if blind["agent_verdict"] != machine:
            raise ValueError(
                f"Unresolved reviewer/scorer disagreement: {row['case_id']} "
                f"review={blind['agent_verdict']} machine={machine}"
            )
        metrics = row["metrics"]
        if machine == "pass":
            classification = "verified_contract_pass"
        elif not row.get("ok") or metrics.get("content_chars", 0) < 50:
            classification = "provider_access_or_extraction_failure"
        else:
            classification = "target_retrieved_but_frozen_contract_not_met"
        attempts.append(
            {
                "attempt_id": row["attempt_id"],
                "machine_verdict": machine,
                "agent_verdict": blind["agent_verdict"],
                "agent_evidence": blind["agent_evidence"],
                "final_verdict": machine,
                "adjudication": None,
                "classification": classification,
                "review_note": blind["agent_evidence"],
                "request_evidence": {
                    "ok": row.get("ok"),
                    "status_code": row.get("status_code"),
                    "upstream_status": row.get("provider_metadata", {}).get("upstream_status"),
                    "final_url": row.get("final_url"),
                    "error_type": row.get("error_type"),
                },
                "output_evidence": {
                    "artifact_path": row["artifact_path"],
                    "content_sha256": metrics.get("content_sha256"),
                    "content_chars": metrics.get("content_chars"),
                },
                "contract_evidence": {
                    "schema": metrics.get("stable_task_schema"),
                    "acceptance_contract_sha256": metrics.get("acceptance_contract_sha256"),
                    "stable_anchor_hits": metrics.get("stable_anchor_hits"),
                    "stable_anchor_total": metrics.get("stable_anchor_total"),
                    "stable_unique_record_count": metrics.get("stable_unique_record_count"),
                    "stable_minimum_records": metrics.get("stable_minimum_records"),
                    "stable_task_errors": metrics.get("stable_task_errors", []),
                },
            }
        )

    classifications = Counter(row["classification"] for row in attempts)
    review = {
        "schema_version": 1,
        "bindings": {
            "dataset_sha256": sha256(evidence / "dataset.json"),
            "results_sha256": sha256(evidence / "results.jsonl"),
        },
        "reviewer": {
            "agent": "Codex independent desktop task reviewers",
            "model": "gpt-6-astra",
            "effort": "high",
            "runtime": "Codex desktop task runtime",
        },
        "review_protocol": {
            "machine_judgments_withheld": True,
            "complete_artifact_paths_supplied": True,
            "structured_response_links_supplied": True,
            "declared_acceptance_evidence_supplied": True,
            "provider_filter": ["lexmount-global"],
            "scope": "all",
            "provider_output_not_used_as_rule_source": True,
            "inherited_review_count": 52,
            "new_blind_review_count": 48,
            "method": "Reviewers saw the frozen contracts, complete text artifacts, and only the raw structured link field needed for named-link tasks; machine verdicts, scores, and derived metrics were withheld.",
        },
        "review_rounds": prior_review.get("review_rounds", [])
        + [
            {
                "date": "2026-09-11",
                "title": "100 站增量结果独立盲审",
                "scope": "继承 52 项已绑定盲审；新增 48 项分成三个独立批次，逐项核对冻结契约、完整正文和原始名称—链接字段。",
                "finding": "仅给正文的初版新增审查输入无法核验不在正文中展开的结构化 URL，因此作废；补入原始 links 字段后重新判断，同时继续隐藏机器分数和判定。最终 100 项与共享评分器一致。",
                "fix": "没有按结果改变规则、URL、输出、耗时或分数；尾斜杠按运行前已有的共享 URL 规范化规则处理，禁止文字与页面级稳定文字照冻结契约执行。",
                "pending": "分歧 0，未决 0。",
            }
        ],
        "summary": {"counts": dict(sorted(classifications.items()))},
        "total_attempts": 100,
        "accepted_unreviewed_passes": 0,
        "expected_attempts": 100,
        "reviewed_attempts": 100,
        "disagreements": 0,
        "unresolved": 0,
        "attempts": attempts,
    }
    write_json(evidence / "result-review.json", review)
    shutil.copy2(prior / "failed-rule-audit.json", evidence / "inherited-failed-rule-audit.json")
    require_result_review(evidence, dataset, records)
    print(
        json.dumps(
            {
                "reviewed": len(attempts),
                "new": len(new_reviews),
                "disagreements": 0,
                "unresolved": 0,
                "classifications": dict(sorted(classifications.items())),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
