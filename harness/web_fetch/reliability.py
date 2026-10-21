"""Coverage and adjudication completeness for a publishable benchmark."""
from __future__ import annotations

from collections import Counter

from lexbench_webfetch.metrics import task_measured


def implementation_inventory(records: list[dict]) -> dict[str, dict[str, int]]:
    """Disclose retained and rerun build populations without requiring new calls."""
    implementations = {}
    for row in records:
        fingerprint = (row.get("provider_metadata") or {}).get("binary_sha256")
        if not fingerprint:
            metadata = row.get("provider_metadata") or {}
            version = metadata.get("reported_version") or metadata.get("browser_version")
            fingerprint = "reported-version:" + str(version) if version else "unknown"
        counts = implementations.setdefault(row.get("provider"), {})
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
    return {provider: dict(sorted(counts.items()))
            for provider, counts in implementations.items()}


def assess_reliability(dataset: dict, records: list[dict]) -> dict:
    """Require exactly one judged result for every planned provider/case pair."""
    cases = {case["id"]: case for case in dataset.get("cases", [])}
    plan = dataset.get("coverage_plan") or {}
    expected = {
        (provider, case_id)
        for case_id, case in cases.items()
        for provider in plan.get(case.get("track", "web"), [])
    }
    counts = Counter((row.get("provider"), row.get("case_id")) for row in records)
    present = set(counts)
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    duplicates = sorted(pair for pair, count in counts.items() if count != 1)
    unjudged = sorted(
        (row.get("provider"), row.get("case_id"))
        for row in records
        if row.get("case_id") in cases
        and not task_measured(row.get("metrics", {}), cases[row["case_id"]].get("task_kind", "body"))
    )
    implementations = implementation_inventory(records)
    errors = []
    if not cases:
        errors.append("评测集没有案例")
    if not plan:
        errors.append("评测集没有覆盖计划")
    if missing:
        errors.append(f"缺少 {len(missing)} 个计划调用")
    if extra:
        errors.append(f"存在 {len(extra)} 个计划外调用")
    if duplicates:
        errors.append(f"存在 {len(duplicates)} 个重复或多次选入的调用")
    if unjudged:
        errors.append(f"存在 {len(unjudged)} 个无法判定通过或失败的调用")
    complete = bool(expected) and not errors
    return {
        "schema_version": 1,
        "status": "reliable" if complete else "unreliable",
        "complete": complete,
        "expected_calls": len(expected),
        "present_calls": len(expected & present),
        "judged_calls": len(expected) - len(missing) - len(set(unjudged) & expected),
        "missing": [{"provider": p, "case_id": c} for p, c in missing],
        "extra": [{"provider": p, "case_id": c} for p, c in extra],
        "duplicates": [{"provider": p, "case_id": c, "count": counts[(p, c)]} for p, c in duplicates],
        "unjudged": [{"provider": p, "case_id": c} for p, c in unjudged],
        "implementation_inventory": implementations,
        "errors": errors,
        "publication_policy": "all_planned_provider_case_pairs_must_exist_once_and_be_judged",
    }


def require_reliable(dataset: dict, records: list[dict]) -> dict:
    result = assess_reliability(dataset, records)
    if not result["complete"]:
        raise ValueError("Benchmark is not publishable: " + "; ".join(result["errors"]))
    return result
