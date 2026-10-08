"""Independently audit WebMainBench evidence without importing its official scorer."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.record_io import load_jsonl

PROVIDERS = ("kitesurf", "lightpanda", "obscura", "moli", "moli-1.1.7", "chromium")
DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"
SCORER_COMMIT = "9d991bdc00c57b57521499494d96be85c31317ba"
METRIC_SOURCE_MANIFEST_SHA256 = "d1a503f7a475da2e118f008f9a63c49cba6fb2378b08de01fb2c3c4c1b96dc31"
CORE = ("text_edit", "code_edit", "table_edit", "table_TEDS", "formula_edit")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lines(path: Path) -> list[dict]:
    return load_jsonl(path, skip_blank=True)


def normalized_edit(predicted: str, reference: str) -> float | None:
    longest = max(len(predicted), len(reference))
    return None if longest == 0 else 1.0 - Levenshtein.distance(predicted, reference) / longest


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def independent_code(text: str) -> str:
    blocks = []
    for match in re.finditer(r"(```[\s\S]*?```)", text):
        content = "\n".join(match.group(0).split("\n")[1:-1])
        if content:
            blocks.append(content)
    pattern = r"(?:\n\s*\n)((?:(?: {4,}|\t+)[^\n]*(?:\n|$)){2,})(?=\n\s*\n|$)"
    for match in re.finditer(pattern, text, re.MULTILINE):
        raw = match.group(1);nonempty = [line.strip() for line in raw.split("\n") if line.strip()]
        if len(nonempty) < 2 or not all(line.startswith("    ") or line.startswith("\t") or not line.strip() for line in raw.split("\n") if line.strip()):
            continue
        if any(re.match(r"^[-•*]\s", line) or re.match(r"^\d+\.\s", line) or re.search(r"\$[\d,]", line) or re.search(r"\b(million|billion|thousand)\b", line, re.I) for line in nonempty):
            continue
        cleaned = [line[4:] if line.startswith("    ") else line[1:] if line.startswith("\t") else line for line in raw.split("\n") if line.strip()]
        if cleaned:
            blocks.append("\n".join(cleaned))
    return "\n".join(blocks)


def independent_formula(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", "", text)
    found = []
    for pattern in (r"(?<!\\)\$\$(.*?)(?<!\\)\$\$", r"(?<!\\)\\\[(.*?)(?<!\\)\\\]", r"(?<!\\)\$(.*?)(?<!\\)\$", r"(?<!\\)\\\((.*?)(?<!\\)\\\)"):
        found.extend(match.group(1).strip() for match in re.finditer(pattern, text, re.DOTALL) if match.group(1).strip())
    return "\n".join(found)


def close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-12


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--single-shard", type=Path,
                        help="Use this immutable shard path when reviewing exactly one provider")
    parser.add_argument(
        "--providers",
        default=",".join(PROVIDERS),
        help="Comma-separated provider shards; the default is the publishable matrix",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Review output exists; refusing to overwrite")
    if digest(args.dataset) != DATASET_SHA256:
        raise ValueError("Dataset hash mismatch")
    sample = json.loads(args.sample_manifest.read_text(encoding="utf-8"))
    expected_ids = {row.get("track_id") for row in sample.get("cases", [])}
    expected_count = sample.get("sampling", {}).get("size")
    sample_sha256 = digest(args.sample_manifest)
    if (sample.get("schema_version") != 1
            or sample.get("parent", {}).get("sha256") != DATASET_SHA256
            or expected_count != len(sample.get("cases", []))
            or None in expected_ids or len(expected_ids) != expected_count):
        raise ValueError("Invalid WebMainBench sample manifest")
    providers = tuple(value.strip() for value in args.providers.split(",") if value.strip())
    if not providers or len(set(providers)) != len(providers) or any(value not in PROVIDERS for value in providers):
        raise ValueError("Providers must be a unique, non-empty subset of the declared provider matrix")
    if args.single_shard and len(providers) != 1:
        raise ValueError("--single-shard requires exactly one provider")
    source = {row["track_id"]: row for row in lines(args.dataset)}
    failures = []
    counts = {"logical": 0, "physical": 0, "artifacts": 0, "text_recomputed": 0,
              "code_recomputed": 0, "formula_recomputed": 0, "aggregate_recomputed": 0,
              "network_isolation_probes": 0, "scorer_bindings": 0}
    provider_notes = {}
    for provider in providers:
        root = args.single_shard if args.single_shard else args.source_root / provider
        logical = lines(root / "scored-results.jsonl")
        physical = lines(root / "physical-attempts.jsonl")
        summary = json.loads((root / "score-summary.json").read_text(encoding="utf-8"))
        execution = json.loads((root / "execution.json").read_text(encoding="utf-8"))
        shard_dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
        selection = shard_dataset.get("selection_manifest", {})
        selection_sha = selection.get("sha256") or selection.get("sample_manifest_sha256")
        execution_selection = execution.get("selection_manifest") or execution.get("report_selection") or {}
        execution_selection_sha = execution_selection.get("sha256") or execution_selection.get("sample_manifest_sha256")
        if (selection_sha != sample_sha256 or execution_selection_sha != sample_sha256
                or set(shard_dataset.get("case_ids", [])) != expected_ids
                or shard_dataset.get("case_count") != expected_count):
            failures.append(f"{provider}: sample selection binding")
        scorer = summary.get("scorer", {})
        if (scorer.get("commit") != SCORER_COMMIT
                or scorer.get("metric_config") != {"use_llm": False}
                or canonical_digest(scorer.get("metric_source_sha256", {}))
                    != METRIC_SOURCE_MANIFEST_SHA256):
            failures.append(f"{provider}: scorer source binding")
        else:
            counts["scorer_bindings"] += 1
        probe = execution.get("network_isolation_probe", {})
        if provider == "kitesurf":
            if probe.get("mode") != "cloudflare_reject_external_resource_types":
                failures.append("kitesurf: external resource rejection probe")
        else:
            ca_artifact = root / str(execution.get("fixture_ca_artifact", ""))
            if (probe.get("mode") != "macos_sandbox_exec_loopback_only"
                    or probe.get("external_connect_errno") not in (1, 13)
                    or not ca_artifact.is_file()
                    or digest(ca_artifact) != probe.get("fixture_ca_sha256")):
                failures.append(f"{provider}: network isolation probe")
            else:
                first = source[logical[0]["track_id"]]
                if probe.get("loopback_body_sha256") != hashlib.sha256(first["html"].encode()).hexdigest():
                    failures.append(f"{provider}: loopback probe body hash")
        counts["network_isolation_probes"] += 1
        counts["logical"] += len(logical);counts["physical"] += len(physical)
        if (len(logical) != expected_count
                or {row["track_id"] for row in logical} != expected_ids
                or {row.get("provider") for row in logical} != {provider}):
            failures.append(f"{provider}: incomplete logical matrix")
            continue
        by_id = {}
        for attempt in physical:
            by_id.setdefault(attempt["track_id"], []).append(attempt)
        if set(by_id) != expected_ids or {row.get("provider") for row in physical} != {provider}:
            failures.append(f"{provider}: physical attempt matrix")
        for row in logical:
            track_id = row["track_id"];record = source[track_id]
            artifact = root / row["artifact_path"]
            if digest(artifact) != row["output_sha256"]:
                failures.append(f"{provider}/{track_id}: artifact hash")
                continue
            counts["artifacts"] += 1
            if hashlib.sha256(record["html"].encode()).hexdigest() != row["input_html_sha256"]:
                failures.append(f"{provider}/{track_id}: input hash")
            if hashlib.sha256(record["groundtruth_content"].encode()).hexdigest() != row["reference_sha256"]:
                failures.append(f"{provider}/{track_id}: reference hash")
            attempts = by_id.get(track_id, [])
            expected_attempts = list(range(1, len(attempts) + 1))
            if (not attempts or len(attempts) > 2
                    or len(attempts) != row.get("physical_attempt_count")
                    or [item.get("physical_attempt") for item in attempts] != expected_attempts
                    or any(item.get("ok") for item in attempts[:-1])
                    or row.get("retry_selection") != "final_physical_attempt_is_authoritative"
                    or attempts[-1]["ok"] != row["ok"]
                    or attempts[-1].get("error_type") != row.get("error_type")
                    or attempts[-1].get("output_sha256") != row.get("output_sha256")
                    or any(item.get("input_html_sha256") != row["input_html_sha256"] for item in attempts)
                    or any(item.get("reference_sha256") != row["reference_sha256"] for item in attempts)):
                failures.append(f"{provider}/{track_id}: final retry selection")
            if not close(sum(item["elapsed_ms"] for item in attempts), row["total_elapsed_ms"]):
                failures.append(f"{provider}/{track_id}: retry latency total")
            if not row["ok"]:
                if row.get("metrics"):
                    failures.append(f"{provider}/{track_id}: failed request has quality metrics")
                continue
            predicted = artifact.read_text(encoding="utf-8")
            text_score = normalized_edit(predicted, record["groundtruth_content"])
            observed = row["metrics"]["text_edit"]
            if text_score is None or not observed["success"] or not close(text_score, observed["score"]):
                failures.append(f"{provider}/{track_id}: independent text_edit")
            counts["text_recomputed"] += 1
            for metric, extractor, key in (("code_edit", independent_code, "code_recomputed"), ("formula_edit", independent_formula, "formula_recomputed")):
                expected = normalized_edit(extractor(predicted), extractor(record["groundtruth_content"]))
                observed = row["metrics"][metric]
                if expected is None:
                    if observed["success"]:
                        failures.append(f"{provider}/{track_id}: {metric} should be unavailable")
                elif not observed["success"] or not close(expected, observed["score"]):
                    failures.append(f"{provider}/{track_id}: independent {metric}")
                counts[key] += 1
        totals = {name: 0.0 for name in CORE};denominators = {name: 0 for name in CORE}
        for row in logical:
            for name in CORE:
                metric = row.get("metrics", {}).get(name, {})
                if metric.get("success"):
                    totals[name] += metric["score"];denominators[name] += 1
        scores = {name: totals[name] / denominators[name] if denominators[name] else 0.0 for name in CORE}
        scores["overall"] = sum(scores.values()) / len(CORE)
        official = summary["official_aggregate"]
        if denominators != official["denominators"] or any(not close(value, official["scores"][name]) for name, value in scores.items()):
            failures.append(f"{provider}: independent aggregate")
        counts["aggregate_recomputed"] += 1
        provider_notes[provider] = {
            "request_failures": sum(not row["ok"] for row in logical),
            "physical_attempts": len(physical),
            "metric_denominators": denominators,
        }
    expected_logical = expected_count * len(providers)
    checks = [
        {"id": "matrix", "status": "passed" if counts["logical"] == expected_logical else "failed", "detail": f"{counts['logical']}/{expected_logical} logical results"},
        {"id": "artifacts", "status": "passed" if counts["artifacts"] == counts["logical"] else "failed", "detail": f"{counts['artifacts']} artifact hashes matched"},
        {"id": "retry-accounting", "status": "passed" if not any("retry" in item for item in failures) else "failed", "detail": f"{counts['physical']} physical attempts checked against final-result selection and total latency"},
        {"id": "network-isolation", "status": "passed" if not any("isolation" in item or "loopback probe" in item or "resource rejection" in item for item in failures) else "failed", "detail": f"{counts['network_isolation_probes']}/{len(providers)} execution-bound isolation probes checked"},
        {"id": "official-scorer-binding", "status": "passed" if counts["scorer_bindings"] == len(providers) else "failed", "detail": f"{counts['scorer_bindings']}/{len(providers)} summaries bind commit {SCORER_COMMIT[:9]}, use_llm=false and the pinned metric-source manifest"},
        {"id": "independent-text-edit", "status": "passed" if not any("text_edit" in item for item in failures) else "failed", "detail": f"{counts['text_recomputed']} successful outputs recomputed without importing WebMainBench"},
        {"id": "independent-code-formula", "status": "passed" if not any("code_edit" in item or "formula_edit" in item for item in failures) else "failed", "detail": f"{counts['code_recomputed']} code and {counts['formula_recomputed']} formula metric cases recomputed"},
        {"id": "aggregate", "status": "passed" if not any("aggregate" in item for item in failures) else "failed", "detail": f"{counts['aggregate_recomputed']}/{len(providers)} provider aggregates and denominators recomputed"},
    ]
    result = {
        "schema_version": 1,
        "status": "passed" if not failures and all(check["status"] == "passed" for check in checks) else "failed",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": "independent deterministic audit; official scorer is not imported for text, code or formula recomputation; table metrics are source-bound and aggregate-checked",
        "providers": list(providers),
        "checks": checks,
        "provider_notes": provider_notes,
        "failures": failures[:200],
        "review_script_sha256": digest(Path(__file__)),
        "dataset_sha256": DATASET_SHA256,
        "sample_manifest_sha256": sample_sha256,
        "sample_case_count": expected_count,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "failures": len(failures), **counts}, ensure_ascii=False))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
