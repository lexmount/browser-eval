"""Supersede complete WebArena task/browser scopes with audited correction runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.browser_use.diagnostics import diagnose_summary
from harness.browser_use.metrics import summarize_attempts
from harness.browser_use.report import write_latest_report
from harness.browser_use.result_audit import audit_result_set


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("task_id") or ""), str(row.get("engine") or "")


def _validate_attempt_scope(
    rows: list[dict[str, Any]], key: tuple[str, str], *, max_retries: int
) -> None:
    if not 1 <= len(rows) <= max_retries + 1:
        raise ValueError(f"{key}: correction exceeds the declared retry limit")
    for index, row in enumerate(rows, 1):
        if int(row.get("attempt") or 0) != index:
            raise ValueError(f"{key}: correction attempt numbering is invalid")
        if bool(row.get("is_retry")) != (index > 1):
            raise ValueError(f"{key}: correction retry flag is invalid")
        if index > 1 and str(rows[index - 2].get("status")) == "pass":
            raise ValueError(f"{key}: correction retried after a passing result")


def supersede_rows(
    base_rows: list[dict[str, Any]],
    correction_rows: list[dict[str, Any]],
    expected_keys: set[tuple[str, str]],
    *,
    max_retries: int = 1,
) -> list[dict[str, Any]]:
    correction_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in correction_rows:
        correction_by_key.setdefault(_key(row), []).append(row)
    if set(correction_by_key) != expected_keys:
        raise ValueError(
            "correction scope mismatch: "
            f"expected={sorted(expected_keys)}, actual={sorted(correction_by_key)}"
        )
    base_keys = {_key(row) for row in base_rows}
    if not expected_keys <= base_keys:
        raise ValueError(f"correction keys absent from base: {sorted(expected_keys - base_keys)}")
    for key, rows in correction_by_key.items():
        rows.sort(key=lambda row: int(row.get("attempt") or 0))
        _validate_attempt_scope(rows, key, max_retries=max_retries)
        base_contracts = {
            str(row.get("task_contract_sha256") or "")
            for row in base_rows
            if _key(row) == key
        }
        correction_contracts = {
            str(row.get("task_contract_sha256") or "") for row in rows
        }
        if len(base_contracts) != 1 or correction_contracts != base_contracts:
            raise ValueError(f"{key}: task contract changed during correction")

    merged: list[dict[str, Any]] = []
    inserted: set[tuple[str, str]] = set()
    for row in base_rows:
        key = _key(row)
        if key not in expected_keys:
            merged.append(row)
        elif key not in inserted:
            merged.extend(correction_by_key[key])
            inserted.add(key)
    return merged


def _validate_run_binding(
    base_manifest: dict[str, Any], correction_manifest: dict[str, Any], *, engine: str
) -> None:
    if correction_manifest.get("suite_sha256") != base_manifest.get("suite_sha256"):
        raise ValueError("correction suite differs from the base run")
    base_protocol = base_manifest.get("protocol") or {}
    correction_protocol = correction_manifest.get("protocol") or {}
    for field in (
        "sample_interval_ms",
        "post_reset_settle_ms",
        "task_timeout_ms",
        "cpus_per_browser",
        "memory_per_browser",
    ):
        if correction_protocol.get(field) != base_protocol.get(field):
            raise ValueError(f"correction protocol differs for {field}")
    base_environment = base_manifest.get("environment") or {}
    correction_environment = correction_manifest.get("environment") or {}
    base_image = ((base_environment.get("images") or {}).get(engine) or {}).get("id")
    correction_image = ((correction_environment.get("images") or {}).get(engine) or {}).get("id")
    if not base_image or correction_image != base_image:
        raise ValueError(f"correction {engine} image differs from the base run")
    base_version = ((base_environment.get("browser_versions") or {}).get(engine))
    correction_version = ((correction_environment.get("browser_versions") or {}).get(engine))
    if not base_version or correction_version != base_version:
        raise ValueError(f"correction {engine} version differs from the base run")


def build(
    base_run: Path,
    correction_run: Path,
    output_root: Path,
    expected_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    if output_root.exists():
        raise ValueError(f"output already exists: {output_root}")
    base_manifest_path = base_run / "manifest.json"
    correction_manifest_path = correction_run / "manifest.json"
    base_attempts_path = base_run / "attempts.jsonl"
    correction_attempts_path = correction_run / "attempts.jsonl"
    base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    correction_manifest = json.loads(correction_manifest_path.read_text(encoding="utf-8"))
    engines = {engine for _, engine in expected_keys}
    if len(engines) != 1:
        raise ValueError("one correction run must target exactly one browser engine")
    engine = next(iter(engines))
    _validate_run_binding(base_manifest, correction_manifest, engine=engine)

    correction_rows = _read_rows(correction_attempts_path)
    local_evidence_root = correction_run / "tasks"
    for row in correction_rows:
        evidence = (
            local_evidence_root
            / str(row["task_id"])
            / str(row["engine"])
            / f'attempt-{int(row["attempt"])}'
        )
        for name in ("attempt.json", "network.har", "final.png", "final-page.json"):
            if not (evidence / name).is_file():
                raise ValueError(f"correction evidence is missing: {evidence / name}")
        row["evidence_directory"] = str(evidence.resolve())

    base_protocol = base_manifest.get("protocol") or {}
    max_retries = int(base_protocol.get("max_failure_retries", 0) or 0)
    merged = supersede_rows(
        _read_rows(base_attempts_path),
        correction_rows,
        expected_keys,
        max_retries=max_retries,
    )
    declared_engines = tuple(str(value) for value in base_protocol.get("engines") or ())
    summary = summarize_attempts(merged, engines=declared_engines)
    diagnose_summary(summary, use_llm=False)
    manifest = deepcopy(base_manifest)
    correction_hash = _sha256(correction_attempts_path)
    manifest["run_id"] = f'{base_manifest.get("run_id", "run")}-c{correction_hash[:8]}'
    manifest["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["derived_from"] = {
        "kind": "complete_task_browser_scope_supersession",
        "base_attempts_sha256": _sha256(base_attempts_path),
        "correction_attempts_sha256": correction_hash,
        "replaced_scopes": [
            {"task_id": task_id, "engine": engine}
            for task_id, engine in sorted(expected_keys)
        ],
        "reason": "environment login navigation race corrected before complete rerun",
    }
    audit_result_set(manifest, summary)

    run = output_root / "run"
    reports = output_root / "reports"
    run.mkdir(parents=True)
    attempts_bytes = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in merged
    ).encode("utf-8")
    summary_bytes = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    (run / "attempts.jsonl").write_bytes(attempts_bytes)
    (run / "summary.json").write_bytes(summary_bytes)
    manifest["artifact_provenance"] = {
        "artifact_directory": str(run.resolve()),
        "attempts_sha256": hashlib.sha256(attempts_bytes).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "correction": manifest["derived_from"],
    }
    (run / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_latest_report(reports, manifest=manifest, summary=summary)
    return {
        "run_id": manifest["run_id"],
        "attempts": len(merged),
        "replaced_scopes": len(expected_keys),
        "functional": summary["functional"],
        "resource_intersection": summary["resource_comparison"]["intersection_attempts"],
        "attempts_sha256": manifest["artifact_provenance"]["attempts_sha256"],
        "summary_sha256": manifest["artifact_provenance"]["summary_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-run", type=Path, required=True)
    parser.add_argument("--correction-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--replace",
        action="append",
        required=True,
        metavar="TASK_ID/ENGINE",
    )
    args = parser.parse_args()
    keys: set[tuple[str, str]] = set()
    for value in args.replace:
        task_id, separator, engine = value.partition("/")
        if not separator or not task_id or not engine:
            parser.error(f"invalid --replace value: {value}")
        keys.add((task_id, engine))
    result = build(args.base_run.resolve(), args.correction_run.resolve(), args.output, keys)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
