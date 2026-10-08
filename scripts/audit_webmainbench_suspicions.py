"""Flag WebMainBench evidence-chain anomalies without changing measured scores.

Signals prioritize human investigation. They are not provider verdicts or proof
of a defective reference, runner, or scorer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from harness.record_io import load_jsonl
from harness.web_fetch.safety import contained_path
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE

QUERY_NAVIGATION = re.compile(r"(?:window\.)?location\.search\s*(?:\+?=)")
ORIGIN_NAVIGATION = re.compile(r"(?:window\.)?location\.href\s*=\s*(?:[A-Za-z_$][\w$]*|['\"](?:https?:)?//)")
SCORE_CLIFF_LOW = .05
SCORE_CLIFF_PEER = .5


def read_jsonl(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan(evidence: Path, source_dataset: Path) -> dict:
    dataset = json.loads((evidence / "dataset.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in dataset["cases"]}
    source = {case["track_id"]: case for case in read_jsonl(source_dataset) if case["track_id"] in cases}
    if set(source) != set(cases):
        raise ValueError("Source dataset does not cover the frozen report sample")
    for case_id, case in cases.items():
        original = source[case_id]
        if (digest(original["html"]) != case["input_html_sha256"]
                or digest(original["groundtruth_content"]) != case["reference_sha256"]):
            raise ValueError("Source/reference binding changed for " + case_id)
    records = read_jsonl(evidence / "results.jsonl")
    physical = read_jsonl(evidence / "physical-attempts.jsonl")
    by_case: dict[str, list[dict]] = defaultdict(list)
    signals: dict[str, set[str]] = defaultdict(set)
    short_outputs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        case_id = record["case_id"]
        if case_id not in cases:
            raise ValueError("Unexpected result case " + case_id)
        by_case[case_id].append(record)
        artifact = contained_path(evidence, record["artifact_path"])
        output = artifact.read_text(encoding="utf-8", errors="replace")
        attempt_id = record["attempt_id"]
        if not record["ok"]:
            signals[attempt_id].add("conversion-undelivered")
            if record.get("provider_metadata", {}).get("upstream_status") is None:
                signals[attempt_id].add("failure-without-upstream-status")
        elif not output.strip():
            signals[attempt_id].add("delivered-empty-output")
        if ERROR_PAGE.search(output):
            signals[attempt_id].add("error-page-counted-as-output")
        if record["ok"] and len(output.strip()) <= 512 and output.strip():
            short_outputs[(record["provider"], re.sub(r"\s+", " ", output).strip())].append(record)
        for metric, item in record.get("metrics", {}).items():
            if metric == "overall" or item.get("success") or not item.get("error"):
                continue
            if ("Both predicted and groundtruth are empty" not in item["error"]
                    and "Skipped due to table_edit failure" not in item["error"]):
                signals[attempt_id].add("component-scorer-error")
        teds = record.get("metrics", {}).get("table_TEDS", {})
        details = teds.get("details", {})
        if all(key in details for key in ("predicted_nodes", "groundtruth_nodes", "edit_distance")):
            minimum_edits = abs(details["predicted_nodes"] - details["groundtruth_nodes"])
            if details["edit_distance"] + 1e-9 < minimum_edits:
                signals[attempt_id].add("teds-distance-below-node-count-lower-bound")
    for rows in short_outputs.values():
        if len({row["case_id"] for row in rows}) > 1:
            for row in rows:
                signals[row["attempt_id"]].add("identical-short-output-across-pages")
    for case_id, rows in by_case.items():
        html = source[case_id]["html"]
        has_404 = any("error-page-counted-as-output" in signals.get(row["attempt_id"], ())
                      and "404" in contained_path(evidence, row["artifact_path"]).read_text(encoding="utf-8", errors="replace")
                      for row in rows)
        if has_404 and QUERY_NAVIGATION.search(html):
            for row in rows:
                signals[row["attempt_id"]].add("fixture-query-navigation-candidate")
        if ORIGIN_NAVIGATION.search(html) and any(not row["ok"] or "error-page-counted-as-output" in signals.get(row["attempt_id"], ()) for row in rows):
            for row in rows:
                signals[row["attempt_id"]].add("frozen-page-origin-navigation-candidate")
        if len({row["ok"] for row in rows}) > 1:
            for row in rows:
                signals[row["attempt_id"]].add("same-page-delivery-divergence")
        scored = [(row, row.get("metrics", {}).get("text_edit", {}).get("score")) for row in rows]
        if any(score is not None and score >= SCORE_CLIFF_PEER for _, score in scored):
            for row, score in scored:
                if score is not None and score <= SCORE_CLIFF_LOW:
                    signals[row["attempt_id"]].add("text-score-cliff-versus-peer")
        if len(html) >= 1_000_000:
            for row in rows:
                if row["provider"] == "kitesurf" and not row["ok"]:
                    signals[row["attempt_id"]].add("large-inline-html-remote-error")
    result_ids = {row["attempt_id"] for row in records}
    if len(result_ids) != len(records):
        raise ValueError("Duplicate logical attempt IDs")
    if not physical or not {row["track_id"] for row in physical}.issubset(cases):
        raise ValueError("Physical-call inventory is missing or outside sample")
    counts: dict[str, int] = defaultdict(int)
    for values in signals.values():
        for value in values:
            counts[value] += 1
    flagged = [
        {"attempt_id": row["attempt_id"], "case_id": row["case_id"], "provider": row["provider"],
         "machine_delivery": bool(row["ok"]), "signals": sorted(signals[row["attempt_id"]])}
        for row in records if signals.get(row["attempt_id"])
    ]
    return {"schema_version": 1, "kind": "investigation_signals_not_verdicts",
            "sample_pages": len(cases), "logical_results": len(records), "physical_calls": len(physical),
            "flagged_results": len(flagged), "signal_counts": dict(sorted(counts.items())),
            "results": flagged}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = scan(args.evidence, args.source_dataset)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise ValueError("Refusing to overwrite an anomaly scan")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("sample_pages", "logical_results", "physical_calls", "flagged_results", "signal_counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
