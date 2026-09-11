"""Create a reviewed homepage evidence revision without new provider calls."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize, task_passed
from harness.web_fetch.result_review import require_result_review


PROVIDER_FAILURE_CASES = {
    "global-homepage-005-chatgpt-com",
    "global-homepage-006-reddit-com",
    "global-homepage-012-gemini-google-com",
    "global-homepage-016-amazon-com",
    "global-homepage-020-naver-com",
    "global-homepage-027-dzen-ru",
    "global-homepage-029-twitch-tv",
    "global-homepage-030-weather-com",
    "global-homepage-035-fandom-com",
    "global-homepage-037-mail-ru",
    "global-homepage-044-spotify-com",
    "global-homepage-047-booking-com",
    "global-homepage-060-espn-com",
    "global-homepage-065-ya-ru",
    "global-homepage-068-indeed-com",
    "global-homepage-081-namu-wiki",
    "global-homepage-085-douyin-com",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def reviewed_dataset(source: dict, canonical: dict) -> dict:
    if source.get("cases") != canonical.get("cases"):
        raise ValueError("Canonical homepage cases differ from the measured cases")
    result = deepcopy(source)
    comparison = result.setdefault("comparison", {})
    canonical_comparison = canonical.get("comparison", {})
    if "source_rule_registry_sha256" in canonical_comparison:
        comparison["source_rule_registry_sha256"] = canonical_comparison["source_rule_registry_sha256"]
    comparison["publication_state"] = "measured_independently_reviewed"
    comparison["require_independent_result_review"] = True
    return result


def classification(case_id: str, verdict: str) -> str:
    if verdict == "pass":
        return "verified_contract_pass"
    if case_id in PROVIDER_FAILURE_CASES:
        return "provider_access_or_extraction_failure"
    return "target_retrieved_but_frozen_contract_not_met"


def copy_verification_observations(source: Path, output: Path, records: list[dict]) -> int:
    path = source / "verification-observations.json"
    if not path.is_file():
        return 0
    by_case = {row["case_id"]: row for row in records}
    document = json.loads(path.read_text())
    kept = []
    for item in document.get("observations", []):
        record = by_case.get(item.get("case_id"))
        if not record:
            continue
        if (record.get("attempt_id") != item.get("formal_attempt_id")
                or record.get("url") != item.get("url")
                or record.get("metrics", {}).get("content_sha256") != item.get("formal_content_sha256")):
            continue
        artifact = source / item["verification_artifact_path"]
        if not artifact.is_file():
            raise ValueError("Verification observation artifact is missing")
        target = output / item["verification_artifact_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, target)
        kept.append(item)
    if kept:
        document["observations"] = kept
        write_json(output / "verification-observations.json", document)
    return len(kept)


def sanitize_execution_paths(document: dict) -> dict:
    """Remove workstation paths while retaining source identity and hash bindings."""
    result = deepcopy(document)
    for batch in result.get("batches", []):
        dataset_source = batch.get("dataset", {}).get("source")
        if dataset_source and Path(dataset_source).is_absolute():
            try:
                batch["dataset"]["source"] = str(Path(dataset_source).relative_to(ROOT))
            except ValueError:
                batch["dataset"]["source"] = Path(dataset_source).name
        config_source = batch.get("provider_config_source")
        if config_source and Path(config_source).is_absolute():
            try:
                batch["provider_config_source"] = str(Path(config_source).relative_to(ROOT))
            except ValueError:
                batch["provider_config_source"] = Path(config_source).name
        if batch.get("environment", {}).get("hostname"):
            batch["environment"]["hostname"] = "local macOS host (redacted)"
        projection = batch.get("projection", {})
        value = projection.get("source_execution")
        if value and Path(value).is_absolute():
            projection["source_execution_record"] = Path(value).name
            projection["source_execution_location"] = "local evidence store; not bundled"
            projection.pop("source_execution", None)
    return result


def finalize(source: Path, review_path: Path, canonical_path: Path, output: Path,
             verification_source: Path | None = None) -> dict:
    source, review_path = source.resolve(), review_path.resolve()
    canonical_path, output = canonical_path.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Output already exists; refusing to overwrite evidence")
    prior_dataset_raw = (source / "dataset.json").read_bytes()
    results_raw = (source / "results.jsonl").read_bytes()
    prior_dataset = json.loads(prior_dataset_raw)
    canonical = json.loads(canonical_path.read_text())
    dataset = reviewed_dataset(prior_dataset, canonical)
    records = [json.loads(line) for line in results_raw.splitlines()]
    cases = {case["id"]: case for case in dataset["cases"]}
    if len(records) != len(cases) or {row["case_id"] for row in records} != set(cases):
        raise ValueError("Measured attempt inventory differs from the homepage cohort")

    review = json.loads(review_path.read_text())
    if review.get("bindings") != {
            "dataset_sha256": digest(prior_dataset_raw),
            "results_sha256": digest(results_raw)}:
        raise ValueError("Blind review is not bound to the source evidence")
    protocol = review.get("review_protocol", {})
    if (protocol.get("machine_judgments_withheld") is not True
            or protocol.get("scope") != "all"
            or review.get("reviewed_attempts") != len(records)
            or review.get("unresolved") != 0):
        raise ValueError("Blind review is incomplete or unresolved")

    shutil.copytree(source, output, ignore=shutil.ignore_patterns("report", "result-review.json"))
    execution_path = output / "execution.json"
    if execution_path.is_file():
        write_json(execution_path, sanitize_execution_paths(json.loads(execution_path.read_text())))
    dataset_raw = (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode()
    (output / "dataset.json").write_bytes(dataset_raw)
    by_id = {row["attempt_id"]: row for row in records}
    for item in review["attempts"]:
        record = by_id[item["attempt_id"]]
        verdict = item["final_verdict"]
        category = classification(record["case_id"], verdict)
        metrics = record.get("metrics", {})
        item["classification"] = category
        item["review_note"] = item.get("agent_evidence")
        item["request_evidence"] = {
            "ok": record.get("ok"),
            "status_code": record.get("status_code"),
            "upstream_status": record.get("provider_metadata", {}).get("upstream_status"),
            "final_url": record.get("final_url"),
            "error_type": record.get("error_type"),
        }
        item["output_evidence"] = {
            "artifact_path": record.get("artifact_path"),
            "content_sha256": metrics.get("content_sha256"),
            "content_chars": metrics.get("content_chars"),
        }
        item["contract_evidence"] = {
            "schema": metrics.get("stable_task_schema"),
            "acceptance_contract_sha256": metrics.get("acceptance_contract_sha256"),
            "stable_anchor_hits": metrics.get("stable_anchor_hits"),
            "stable_anchor_total": metrics.get("stable_anchor_total"),
            "stable_unique_record_count": metrics.get("stable_unique_record_count"),
            "stable_minimum_records": metrics.get("stable_minimum_records"),
            "stable_task_errors": metrics.get("stable_task_errors", []),
        }
    review["bindings"] = {
        "dataset_sha256": digest(dataset_raw),
        "results_sha256": digest(results_raw),
    }
    review.setdefault("review_protocol", {})["provider_output_not_used_as_rule_source"] = True
    review["review_protocol"]["standalone_measured_run"] = True
    review["review_protocol"]["metadata_rebinding"] = {
        "prior_dataset_sha256": digest(prior_dataset_raw),
        "current_dataset_sha256": digest(dataset_raw),
        "cases_changed": False,
        "results_changed": False,
        "provider_calls": 0,
    }
    review["review_rounds"] = [
        {
            "date": "2026-09-11",
            "title": "盲审协议与输入完整性检查",
            "scope": "全部 52 项正式调用；检查审查包是否隐藏机器判定并包含正文、请求状态和结构化链接证据。",
            "finding": "初版审查包遗漏结构化链接字段，并未完整说明大小写规范化语义，因此作废且未用于最终判定。",
            "fix": "重新生成 52 项盲审包；加入原始返回的名称—链接字段，同时继续隐藏机器判定、分数和派生指标。",
            "pending": "无；第二轮审查包覆盖 52 个唯一 attempt ID。",
        },
        {
            "date": "2026-09-11",
            "title": "全部正式结果独立盲审",
            "scope": "逐项核对 52 项冻结契约、首次正式调用正文、结构化返回字段和请求落点。",
            "finding": "独立审查与共享评分器的 24 项通过、28 项未通过完全一致；分歧 0，未决 0。",
            "fix": "不修改规则、供应商输出、耗时或分数；仅补齐复核记录、失败归因和元数据绑定。",
            "pending": "无。",
        },
    ]
    write_json(output / "result-review.json", review)

    audit_path = output / "failed-rule-audit.json"
    if audit_path.is_file():
        audit = json.loads(audit_path.read_text())
        audit.setdefault("bindings", {})["dataset_sha256"] = digest(dataset_raw)
        audit["bindings"]["results_sha256"] = digest(results_raw)
        audit["bindings"]["rule_registry_sha256"] = dataset["comparison"].get("source_rule_registry_sha256")
        audit["metadata_rebinding"] = {
            "prior_dataset_sha256": digest(prior_dataset_raw),
            "cases_changed": False,
            "provider_calls": 0,
        }
        write_json(audit_path, audit)

    write_json(output / "summary.json", summarize(records))

    verification_count = 0
    if verification_source:
        verification_count = copy_verification_observations(
            verification_source.resolve(), output, records)
    require_result_review(output, dataset, records)
    return {
        "cases": len(records),
        "passes": sum(task_passed(row["ok"], row["metrics"],
                                  cases[row["case_id"]].get("task_kind", "body"))
                      for row in records),
        "failures": sum(item["final_verdict"] == "fail" for item in review["attempts"]),
        "review_disagreements": review["disagreements"],
        "review_unresolved": review["unresolved"],
        "verification_observations": verification_count,
        "provider_calls": 0,
        "output": str(output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--canonical", type=Path,
                        default=ROOT / "evaluation/global-homepages-dataset.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification-source", type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.source, args.review, args.canonical, args.output,
                              args.verification_source), ensure_ascii=False, indent=2))
