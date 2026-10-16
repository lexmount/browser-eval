"""Auditable repairs for browser evaluation run metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.record_io import load_jsonl, same_json_record
from harness.web_fetch.safety import unlinked_path

from .diagnostics import diagnose_summary
from .metrics import summarize_attempts
from .report import write_latest_report
from .result_audit import audit_result_set


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return load_jsonl(path, skip_blank=True)


def _sha256(path: Path) -> str:
    path = unlinked_path(path)
    if not path.is_file():
        raise ValueError(f"source evidence is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("task_id") or ""), str(row.get("engine") or "")


def _validate_attempt_scope(
    rows: list[dict[str, Any]], key: tuple[str, str], *, max_retries: int
) -> None:
    if not 1 <= len(rows) <= max_retries + 1:
        raise ValueError(f"{key}: correction exceeds the declared retry limit")
    for index, row in enumerate(rows, 1):
        if type(row.get("attempt")) is not int or row["attempt"] != index:
            raise ValueError(f"{key}: correction attempt numbering is invalid")
        if type(row.get("is_retry")) is not bool or row["is_retry"] != (index > 1):
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
    if not expected_keys:
        raise ValueError("correction scope must be nonempty")
    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("retry limit must be a nonnegative integer")
    base_by_key = {}
    for row in base_rows:
        base_by_key.setdefault(_key(row), []).append(row)
    for key, rows in base_by_key.items():
        _validate_attempt_scope(rows, key, max_retries=max_retries)
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
            str(row.get("task_contract_sha256") or "") for row in base_rows if _key(row) == key
        }
        correction_contracts = {str(row.get("task_contract_sha256") or "") for row in rows}
        if (
            len(base_contracts) != 1
            or "" in base_contracts
            or correction_contracts != base_contracts
        ):
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
    return deepcopy(merged)


def validate_correction_run_binding(
    base_manifest: dict[str, Any], correction_manifest: dict[str, Any], *, engine: str
) -> None:
    if not base_manifest.get("suite_sha256") or correction_manifest.get(
        "suite_sha256"
    ) != base_manifest.get("suite_sha256"):
        raise ValueError("correction suite differs from the base run")
    base_protocol = base_manifest.get("protocol") or {}
    correction_protocol = correction_manifest.get("protocol") or {}
    for field in (
        "sample_interval_ms",
        "post_reset_settle_ms",
        "task_timeout_ms",
        "cpus_per_browser",
        "memory_per_browser",
        "required_attempt_evidence",
        "network_isolation",
        "execution_order",
    ):
        if correction_protocol.get(field) != base_protocol.get(field):
            raise ValueError(f"correction protocol differs for {field}")
    base_environment = base_manifest.get("environment") or {}
    correction_environment = correction_manifest.get("environment") or {}
    base_image = ((base_environment.get("images") or {}).get(engine) or {}).get("id")
    correction_image = ((correction_environment.get("images") or {}).get(engine) or {}).get("id")
    if not base_image or correction_image != base_image:
        raise ValueError(f"correction {engine} image differs from the base run")
    base_version = (base_environment.get("browser_versions") or {}).get(engine)
    correction_version = (correction_environment.get("browser_versions") or {}).get(engine)
    if not base_version or correction_version != base_version:
        raise ValueError(f"correction {engine} version differs from the base run")


def _attempt_source_bindings(root: Path, row: dict[str, Any]) -> dict[Path, str]:
    task, engine = safe_identifier(row["task_id"]), safe_identifier(row["engine"])
    number = row.get("attempt")
    if type(number) is not int or number < 1:
        raise ValueError("source attempt number must be a positive integer")
    directory = contained_path(
        root, f"tasks/{task}/{engine}/attempt-{number}", reject_symlinks=True
    )
    if unlinked_path(Path(str(row.get("evidence_directory") or ""))) != directory:
        raise ValueError("source attempt evidence directory differs from the declared run")
    attempt = directory / "attempt.json"
    bound = {attempt: _sha256(attempt)}
    if not same_json_record(json.loads(attempt.read_bytes()), row):
        raise ValueError("source attempt projection differs from immutable raw evidence")
    if "source_attempt_path" in row or "source_attempt_sha256" in row:
        source = contained_path(root, row.get("source_attempt_path", ""), reject_symlinks=True)
        bound[source] = _sha256(source)
        if bound[source] != row.get("source_attempt_sha256"):
            raise ValueError("derived attempt predecessor hash differs")
        original = json.loads(source.read_bytes())
        location_fields = {"evidence_directory", "source_attempt_path", "source_attempt_sha256"}
        if not same_json_record(
            {key: value for key, value in row.items() if key not in location_fields},
            {key: value for key, value in original.items() if key not in location_fields},
        ):
            raise ValueError("derived attempt changed measured fields")
    har = directory / "network.har"
    unlinked_path(har)
    if (row.get("network_har") or {}).get("status") != "error" or har.exists():
        bound[har] = _sha256(har)
    final = row.get("final_page_evidence") or {}
    for key, name in (("screenshot", "final.png"), ("page", "final-page.json")):
        path = unlinked_path(directory / name)
        metadata = final.get(key)
        if metadata is not None:
            if not isinstance(metadata, dict) or metadata.get("path") != name:
                raise ValueError("source final-page evidence path is invalid")
            bound[path] = _sha256(path)
            if metadata.get("sha256") != bound[path]:
                raise ValueError("source final-page evidence hash differs")
        elif final.get("status") == "complete":
            raise ValueError("complete source attempt lacks bound final-page evidence")
        elif path.exists():
            bound[path] = _sha256(path)
    return bound


def build_supersession(
    base_run: Path,
    correction_run: Path,
    output_root: Path,
    expected_keys: set[tuple[str, str]],
    *,
    reason: str,
) -> dict[str, Any]:
    base_run, correction_run, output_root = map(
        unlinked_path, (base_run, correction_run, output_root)
    )
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("supersession requires an evidenced correction reason")
    if output_root.exists():
        raise ValueError(f"output already exists: {output_root}")
    if any(output_root.is_relative_to(source) for source in (base_run, correction_run)):
        raise ValueError("supersession output must be outside both source runs")
    base_manifest_path = base_run / "manifest.json"
    correction_manifest_path = correction_run / "manifest.json"
    base_attempts_path = base_run / "attempts.jsonl"
    correction_attempts_path = correction_run / "attempts.jsonl"
    sources = {
        path: _sha256(path)
        for path in (
            base_manifest_path,
            correction_manifest_path,
            base_attempts_path,
            correction_attempts_path,
        )
    }
    base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    correction_manifest = json.loads(correction_manifest_path.read_text(encoding="utf-8"))
    engines = {engine for _, engine in expected_keys}
    if len(engines) != 1:
        raise ValueError("one correction run must target exactly one browser engine")
    engine = next(iter(engines))
    validate_correction_run_binding(base_manifest, correction_manifest, engine=engine)

    base_rows = _read_rows(base_attempts_path)
    correction_rows = _read_rows(correction_attempts_path)
    for root, manifest, path, rows in (
        (base_run, base_manifest, base_attempts_path, base_rows),
        (correction_run, correction_manifest, correction_attempts_path, correction_rows),
    ):
        if (manifest.get("artifact_provenance") or {}).get("attempts_sha256") != sources[path]:
            raise ValueError("source attempt file differs from its run manifest")
        provenance = manifest.get("artifact_provenance") or {}
        if "source_inputs_path" in provenance or "source_inputs_sha256" in provenance:
            prior_index = contained_path(
                root, provenance.get("source_inputs_path", ""), reject_symlinks=True
            )
            sources[prior_index] = _sha256(prior_index)
            if sources[prior_index] != provenance.get("source_inputs_sha256"):
                raise ValueError("prior supersession source index hash differs")
            index = json.loads(prior_index.read_bytes())
            if index.get("schema") != "webarena_supersession_sources/1" or not index.get("files"):
                raise ValueError("prior supersession source index is invalid")
            for binding in index["files"]:
                retained_path = contained_path(root, binding["retained_path"], reject_symlinks=True)
                sources[retained_path] = _sha256(retained_path)
                if sources[retained_path] != binding.get("sha256"):
                    raise ValueError("retained historical source hash differs")
        for row in rows:
            sources.update(_attempt_source_bindings(root, row))

    base_protocol = base_manifest.get("protocol") or {}
    max_retries = base_protocol.get("max_failure_retries", 0)
    merged = supersede_rows(
        base_rows,
        correction_rows,
        expected_keys,
        max_retries=max_retries,
    )
    originals = {}
    for row in merged:
        original_directory = unlinked_path(Path(row["evidence_directory"]))
        originals[_key(row) + (row["attempt"],)] = original_directory
        raw_path = original_directory / "attempt.json"
        source_label = "correction" if raw_path.is_relative_to(correction_run) else "base"
        source_root = correction_run if source_label == "correction" else base_run
        row["source_attempt_path"] = (
            Path("source-evidence") / source_label / raw_path.relative_to(source_root)
        ).as_posix()
        row["source_attempt_sha256"] = sources[raw_path]
        row["evidence_directory"] = str(
            output_root / "run/tasks" / row["task_id"] / row["engine"] / f"attempt-{row['attempt']}"
        )
    declared_engines = tuple(str(value) for value in base_protocol.get("engines") or ())
    summary = summarize_attempts(merged, engines=declared_engines)
    diagnose_summary(summary, use_llm=False)
    manifest = deepcopy(base_manifest)
    correction_hash = sources[correction_attempts_path]
    manifest["run_id"] = f"{base_manifest.get('run_id', 'run')}-c{correction_hash[:8]}"
    manifest["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["derived_from"] = {
        "kind": "complete_task_browser_scope_supersession",
        "base_attempts_sha256": sources[base_attempts_path],
        "correction_attempts_sha256": correction_hash,
        "replaced_scopes": [
            {"task_id": task_id, "engine": engine} for task_id, engine in sorted(expected_keys)
        ],
        "reason": reason.strip(),
    }
    audit_result_set(manifest, summary)

    output_root.parent.mkdir(parents=True, exist_ok=True)
    attempts_bytes = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in merged
    ).encode("utf-8")
    summary_bytes = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    retained = {}
    for path in sources:
        label = "correction" if path.is_relative_to(correction_run) else "base"
        source_root = correction_run if label == "correction" else base_run
        retained[path] = Path("source-evidence") / label / path.relative_to(source_root)
    source_bytes = (
        json.dumps(
            {
                "schema": "webarena_supersession_sources/1",
                "files": [
                    {"path": str(path), "sha256": sha, "retained_path": retained[path].as_posix()}
                    for path, sha in sources.items()
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode()
    manifest["artifact_provenance"] = {
        "artifact_directory": str(output_root / "run"),
        "attempts_sha256": hashlib.sha256(attempts_bytes).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "correction": manifest["derived_from"],
        "source_inputs_path": "source-inputs.json",
        "source_inputs_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    with tempfile.TemporaryDirectory(prefix=".supersession-", dir=output_root.parent) as work:
        staging = Path(work) / "result"
        run = staging / "run"
        run.mkdir(parents=True)
        for path, relative in retained.items():
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != sources[path]:
                raise ValueError("source evidence changed during retention")
            destination = run / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        for row in merged:
            source = originals[_key(row) + (row["attempt"],)]
            relative = Path(row["evidence_directory"]).relative_to(output_root / "run")
            destination = run / relative
            destination.mkdir(parents=True)
            for name in ("network.har", "final.png", "final-page.json"):
                original = source / name
                if original in retained:
                    (destination / name).write_bytes((run / retained[original]).read_bytes())
            (destination / "attempt.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        (run / "attempts.jsonl").write_bytes(attempts_bytes)
        (run / "summary.json").write_bytes(summary_bytes)
        (run / "source-inputs.json").write_bytes(source_bytes)
        (run / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_latest_report(staging / "reports", manifest=manifest, summary=summary)
        if any(_sha256(path) != sha for path, sha in sources.items()):
            raise ValueError("source evidence changed during supersession")
        unlinked_path(output_root)
        if output_root.exists():
            raise ValueError("supersession output appeared during staging")
        staging.rename(output_root)
    return {
        "run_id": manifest["run_id"],
        "attempts": len(merged),
        "replaced_scopes": len(expected_keys),
        "functional": summary["functional"],
        "resource_intersection": summary["resource_comparison"]["intersection_attempts"],
        "attempts_sha256": manifest["artifact_provenance"]["attempts_sha256"],
        "summary_sha256": manifest["artifact_provenance"]["summary_sha256"],
    }


def main_supersession(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-run", type=Path, required=True)
    parser.add_argument("--correction-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--replace",
        action="append",
        required=True,
        metavar="TASK_ID/ENGINE",
    )
    args = parser.parse_args(argv)
    keys: set[tuple[str, str]] = set()
    for value in args.replace:
        task_id, separator, engine = value.partition("/")
        if not separator or not task_id or not engine:
            parser.error(f"invalid --replace value: {value}")
        if (task_id, engine) in keys:
            parser.error(f"duplicate --replace value: {value}")
        keys.add((task_id, engine))
    result = build_supersession(
        args.base_run, args.correction_run, args.output, keys, reason=args.reason
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
