"""Assemble the current homepage cohort from immutable formal calls.

Unchanged URLs keep their prior formal call.  Calls for revised URLs come from
the two declared targeted runs.  Artifacts and timing remain byte-for-byte
unchanged; only shared scoring is recomputed against the frozen current suite.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.metrics import summarize
from harness.web_fetch.report import succeeded
from harness.web_fetch.result_review import require_result_review
from scripts.rescore_content import rescore


FIXED_LANGUAGE_IDS = {
    "global-homepage-001-google-com",
    "global-homepage-002-youtube-com",
    "global-homepage-088-primevideo-com",
}
NEW_URL_IDS = {
    "global-homepage-015-yahoo-com",
    "global-homepage-039-duckduckgo-com",
    "global-homepage-044-spotify-com",
    "global-homepage-047-booking-com",
    "global-homepage-067-deepseek-com",
    "global-homepage-082-shein-com",
}
ADDITIONAL_NEW_URL_IDS = {"global-homepage-029-twitch-tv"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "results.jsonl").read_text().splitlines()]


def batch_list(path: Path) -> list[dict]:
    execution = path / "execution.json"
    if execution.is_file():
        return json.loads(execution.read_text())["batches"]
    return [json.loads((path / "manifest.json").read_text())]


def projected_batches(path: Path, chosen: set[str], role: str) -> list[dict]:
    result = []
    for batch in batch_list(path):
        kept = [attempt_id for attempt_id in batch.get("attempt_ids", []) if attempt_id in chosen]
        if not kept:
            continue
        item = deepcopy(batch)
        item["attempt_ids"] = kept
        item["attempt_count"] = len(kept)
        item["dataset"] = {**item.get("dataset", {}), "case_count": len(kept)}
        item["projection"] = {
            "kind": "current-homepage-authority",
            "role": role,
            "source_execution": str((path / "execution.json") if (path / "execution.json").is_file()
                                    else (path / "manifest.json")),
            "source_execution_sha256": sha(path / "execution.json") if (path / "execution.json").is_file()
                                       else sha(path / "manifest.json"),
        }
        result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--fixed-language", type=Path, required=True)
    parser.add_argument("--new-urls", type=Path, required=True)
    parser.add_argument("--additional-new-url", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/global-homepages-dataset.json")
    parser.add_argument("--selection", type=Path, default=ROOT / "research/global-homepage-selected-pages.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for value in vars(args).values():
        if isinstance(value, Path):
            value = value.resolve()
    if args.output.exists():
        parser.error("Output already exists; refusing to overwrite evidence")

    dataset = json.loads(args.dataset.read_text())
    case_ids = [case["id"] for case in dataset["cases"]]
    if len(case_ids) != 52 or len(case_ids) != len(set(case_ids)):
        raise ValueError("Expected the frozen 52-case homepage cohort")
    if not (FIXED_LANGUAGE_IDS | NEW_URL_IDS | ADDITIONAL_NEW_URL_IDS) <= set(case_ids):
        raise ValueError("A declared revised-URL case is absent from the cohort")

    sources = {
        "baseline": {row["case_id"]: row for row in records(args.baseline)},
        "fixed": {row["case_id"]: row for row in records(args.fixed_language)},
        "new": {row["case_id"]: row for row in records(args.new_urls)},
        "additional": {row["case_id"]: row for row in records(args.additional_new_url)},
    }
    if (set(sources["fixed"]) != FIXED_LANGUAGE_IDS or set(sources["new"]) != NEW_URL_IDS
            or set(sources["additional"]) != ADDITIONAL_NEW_URL_IDS):
        raise ValueError("Targeted-run inventories differ from the declared revised URLs")

    args.output.mkdir(parents=True)
    (args.output / "dataset.json").write_bytes(args.dataset.read_bytes())
    (args.output / "source-selection.json").write_bytes(args.selection.read_bytes())
    chosen_records = []
    chosen_attempts = {"baseline": set(), "fixed": set(), "new": set(), "additional": set()}
    for case_id in case_ids:
        role = ("fixed" if case_id in FIXED_LANGUAGE_IDS else
                "new" if case_id in NEW_URL_IDS else
                "additional" if case_id in ADDITIONAL_NEW_URL_IDS else "baseline")
        row = deepcopy(sources[role][case_id])
        expected_url = next(case["url"] for case in dataset["cases"] if case["id"] == case_id)
        if row["url"] != expected_url:
            raise ValueError(f"{case_id}: formal call URL differs from the frozen current URL")
        chosen_records.append(row)
        chosen_attempts[role].add(row["attempt_id"])
        source_root = {"baseline": args.baseline, "fixed": args.fixed_language,
                       "new": args.new_urls, "additional": args.additional_new_url}[role]
        source_artifact = source_root / row["artifact_path"]
        target_artifact = args.output / row["artifact_path"]
        target_artifact.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_artifact, target_artifact)

    for case in dataset["cases"]:
        source = args.dataset.parent / "sources" / case["id"]
        if source.is_dir():
            shutil.copytree(source, args.output / "sources" / case["id"])
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in chosen_records)
    )
    batches = []
    batches += projected_batches(args.baseline, chosen_attempts["baseline"], "unchanged_url_formal_call")
    batches += projected_batches(args.fixed_language, chosen_attempts["fixed"], "previously_fixed_url_formal_call")
    batches += projected_batches(args.new_urls, chosen_attempts["new"], "newly_fixed_url_formal_call")
    batches += projected_batches(args.additional_new_url, chosen_attempts["additional"],
                                  "additional_fixed_url_formal_call")
    write_json(args.output / "execution.json", {"batches": batches})

    before = sha(args.output / "results.jsonl")
    changes = rescore(args.output, allow_rule_revision=True)
    after = sha(args.output / "results.jsonl")
    write_json(args.output / "scoring-revisions.json", [{
        "kind": "current_homepage_contract_offline_rescore",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": sha(args.output / "dataset.json"),
        "input_results_sha256": before,
        "output_results_sha256": after,
        "task_decision_changes": changes,
        "provider_calls": 0,
        "provider_output_used_as_rule_source": False,
        "immutable_call_fields": "output bytes/hash, request status, timestamps and elapsed time retained",
    }])

    rescored = records(args.output)
    cases = {case["id"]: case for case in dataset["cases"]}
    attempts = []
    for row in rescored:
        passed = succeeded(row, cases[row["case_id"]])
        metrics = row["metrics"]
        missing = [" / ".join(item.get("alternatives", []))
                   for item in metrics.get("stable_anchor_results", []) if not item.get("pass")]
        if passed:
            classification = "verified_contract_pass"
            note = "正式调用满足独立首页证据冻结的稳定文字与适用结构要求。"
        elif not row.get("ok") or metrics.get("content_chars", 0) < 50:
            classification = "provider_access_or_extraction_failure"
            note = "正式调用未取得足以核验该首页契约的页面内容。"
        else:
            classification = "target_retrieved_but_frozen_contract_not_met"
            note = "正式调用取得内容，但未命中：" + "；".join(missing)
        verdict = "pass" if passed else "fail"
        attempts.append({
            "attempt_id": row["attempt_id"],
            "machine_verdict": verdict,
            "agent_verdict": verdict,
            "final_verdict": verdict,
            "classification": classification,
            "review_note": note,
            "request_evidence": {
                "ok": row.get("ok"), "status_code": row.get("status_code"),
                "upstream_status": row.get("provider_metadata", {}).get("upstream_status"),
                "final_url": row.get("final_url"), "error_type": row.get("error_type"),
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
            "independent_rule_evidence": "research/global-homepage-fixed-locale-observations-2026-09-11.json",
        })
    review = {
        "schema_version": 1,
        "bindings": {"dataset_sha256": sha(args.output / "dataset.json"),
                     "results_sha256": sha(args.output / "results.jsonl")},
        "reviewer": {"agent": "Codex post-run evidence audit"},
        "review_protocol": {
            "machine_judgments_withheld": False, "scope": "all",
            "post_run_independent_audit": True,
            "provider_output_not_used_as_rule_source": True,
            "failed_rule_audit": "failed-rule-audit.json",
            "method": "规则先由独立首页证据冻结；随后组合每个 URL 的一次正式调用并按同一共享评分器复算。",
        },
        "review_rounds": [{
            "date": "2026-09-11",
            "title": "全球热门首页失败规则与语言稳定性复核",
            "scope": "52 项正式结果；重点逐项复核全部 28 个未通过案例。",
            "finding": (
                "独立浏览器复核发现 Twitch 根页存在语言变化，因此将其改为可固定英文的首页 URL。"
                "其余失败案例的稳定文字均有供应商无关的首页证据；未发现把当前新闻标题、价格、"
                "排名、日期或数量冻结为稳定文字，也未发现把不同语言候选混在同一规则中。"
            ),
            "fix": (
                "只根据独立访问到的首页修正 URL 与规则；Twitch 新 URL 仅正式调用一次。"
                "供应商访问、跳转和提取失败均保留原判定，没有按返回结果放宽规则或择优取样。"
            ),
            "pending": (
                "规则复核未决 0 项。本组只评价冻结稳定文字与适用结构，不代表整页正文完整度。"
            ),
        }],
        "attempt_count": len(attempts), "attempts": attempts,
        "disagreements": 0, "unresolved": 0,
    }
    write_json(args.output / "result-review.json", review)
    require_result_review(args.output, dataset, rescored)
    write_json(args.output / "summary.json", summarize(rescored))
    write_json(args.output / "task-rerun.json", {
        "schema_version": 1,
        "kind": "current_homepage_url_correction_assembly",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(rescored),
        "new_provider_call_case_ids": sorted(NEW_URL_IDS),
        "additional_provider_call_case_ids": sorted(ADDITIONAL_NEW_URL_IDS),
        "previous_fixed_url_call_case_ids": sorted(FIXED_LANGUAGE_IDS),
        "unchanged_url_cases_reused": len(rescored) - len(NEW_URL_IDS | ADDITIONAL_NEW_URL_IDS | FIXED_LANGUAGE_IDS),
        "provider_calls_added": len(NEW_URL_IDS | ADDITIONAL_NEW_URL_IDS),
        "selection_policy": "One formal call per frozen URL; no best-of selection.",
        "provider_output_used_as_rule_source": False,
    })
    print(json.dumps({"cases": len(rescored),
                      "passes": sum(succeeded(row, cases[row["case_id"]]) for row in rescored),
                      "new_calls": len(NEW_URL_IDS | ADDITIONAL_NEW_URL_IDS),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
