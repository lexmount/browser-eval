#!/usr/bin/env python3
"""Run one non-scoring Moli diagnostic for every persisted empty output.

The diagnostic batch is deliberately separate from the benchmark result.  It
reuses the original URL, provider route and timeout, retains complete public
call logs, and records an explicit link to the original scored attempt.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any


EXPECTED_VERSION = "moli 1.1.5"
EXPECTED_SHA256 = "4a947148f9af8726b619558fb72c05def75e1c347464c64af178e2f4ddfa71a5"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def empty_output_parents(path: Path) -> dict[str, dict[str, Any]]:
    parents: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        if record.get("error_type") != "empty_output":
            continue
        case_id = str(record["case_id"])
        if case_id in parents:
            raise ValueError(f"duplicate empty-output parent for {case_id}")
        parents[case_id] = record
    if not parents:
        raise ValueError("the source run contains no empty-output attempts")
    return parents


def diagnostic_record(record: dict[str, Any], parent: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(record)
    annotated["diagnostic_only"] = True
    annotated["score_bearing"] = False
    annotated["corresponding_original_attempt_id"] = parent["attempt_id"]
    annotated["corresponding_original_run_id"] = parent.get("run_id")
    annotated["diagnostic_interpretation"] = (
        "not_reproduced" if record.get("ok") else "reproduced_or_other_failure"
    )
    return annotated


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_call(*, binary: Path, url: str, timeout: float) -> dict[str, Any]:
    timeout_ms = str(max(1, int(timeout * 1000)))
    command = [
        str(binary),
        "fetch",
        "--dump",
        "markdown",
        "--wait-until",
        "done",
        "--timeout",
        timeout_ms,
        "--block-private-networks",
        url,
    ]
    started_at = utc_now()
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        stdout, stderr = process.communicate()
    completed_at = utc_now()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    decoded_stdout = stdout.decode("utf-8", "replace")
    decoded_stderr = stderr.decode("utf-8", "replace")
    ok = not timed_out and process.returncode == 0 and bool(decoded_stdout.strip())
    error_type = (
        None
        if ok
        else "timeout"
        if timed_out
        else "empty_output"
        if process.returncode == 0
        else "tool_error"
    )
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": elapsed_ms,
        "ok": ok,
        "error_type": error_type,
        "exit_code": process.returncode,
        "stdout": decoded_stdout,
        "stderr": decoded_stderr,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "command_public": ["moli", *command[1:-1], url],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing diagnostic batch: {args.output}")

    binary_sha256 = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    if binary_sha256 != EXPECTED_SHA256:
        raise SystemExit(f"unexpected Moli binary SHA-256: {binary_sha256}")

    parents = empty_output_parents(args.source_run / "results.jsonl")
    raw_dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    selected_items = [item for item in raw_dataset["cases"] if item["id"] in parents]
    selected_ids = {item["id"] for item in selected_items}
    missing = sorted(set(parents) - selected_ids)
    if missing:
        raise SystemExit(f"empty-output cases missing from dataset: {', '.join(missing)}")

    args.output.mkdir(parents=True)
    write_json(
        args.output / "diagnostic-input-dataset.json",
        {
            **raw_dataset,
            "id": f"{raw_dataset['id']}--empty-output-diagnostic",
            "name": f"{raw_dataset['name']}（空输出诊断，不计分）",
            "cases": selected_items,
            "diagnostic_only": True,
            "score_bearing": False,
        },
    )
    artifacts = args.output / "artifacts"
    logs = args.output / "call-logs"
    artifacts.mkdir()
    logs.mkdir()
    annotated = []
    batch_started_at = utc_now()
    for item in selected_items:
        case_id = item["id"]
        parent = parents[case_id]
        if item["url"] != parent["url"]:
            raise SystemExit(f"URL changed for {case_id}; refusing diagnostic")
        call = run_call(binary=args.binary, url=item["url"], timeout=args.timeout)
        attempt_id = f"moli-webfetch-diagnostic__{case_id}__r001"
        artifact_rel = Path("artifacts") / f"{case_id}.txt"
        log_rel = Path("call-logs") / f"{case_id}.json"
        (args.output / artifact_rel).write_text(call["stdout"], encoding="utf-8")
        content_sha256 = hashlib.sha256(call["stdout"].encode()).hexdigest()
        public_log = {
            "schema_version": 1,
            "diagnostic_only": True,
            "score_bearing": False,
            "identity": {
                "attempt_id": attempt_id,
                "case_id": case_id,
                "corresponding_original_attempt_id": parent["attempt_id"],
            },
            "timing": {
                "started_at": call["started_at"],
                "completed_at": call["completed_at"],
                "elapsed_ms": call["elapsed_ms"],
                "deadline_budget_ms": args.timeout * 1000,
            },
            "request": {"url": item["url"], "command": call["command_public"]},
            "process": {
                "exit_code": call["exit_code"],
                "stdout": call["stdout"],
                "stderr": call["stderr"],
                "stdout_bytes": call["stdout_bytes"],
                "stderr_bytes": call["stderr_bytes"],
            },
            "output": {
                "path": artifact_rel.as_posix(),
                "bytes": call["stdout_bytes"],
                "sha256": content_sha256,
                "empty": call["stdout_bytes"] == 0,
            },
            "error": {
                "type": call["error_type"],
                "message": None if call["ok"] else call["stderr"] or "Moli returned no usable output",
            },
            "events": [
                {"stage": "call_started", "at": call["started_at"]},
                {
                    "stage": "process_completed" if call["error_type"] != "timeout" else "deadline_exceeded",
                    "at": call["completed_at"],
                },
                {
                    "stage": "output_validated",
                    "at": call["completed_at"],
                    "outcome": "usable" if call["ok"] else call["error_type"],
                },
            ],
        }
        write_json(args.output / log_rel, public_log)
        record = diagnostic_record(
            {
                "attempt_id": attempt_id,
                "provider": "moli-webfetch-diagnostic",
                "provider_type": "moli_webfetch",
                "case_id": case_id,
                "case_name": item["name"],
                "url": item["url"],
                "started_at": call["started_at"],
                "completed_at": call["completed_at"],
                "elapsed_ms": call["elapsed_ms"],
                "ok": call["ok"],
                "error_type": call["error_type"],
                "error_message": public_log["error"]["message"],
                "artifact_path": artifact_rel.as_posix(),
                "content_sha256": content_sha256,
                "call_log_path": log_rel.as_posix(),
            },
            parent,
        )
        annotated.append(record)
        print(
            f"{'USABLE' if call['ok'] else 'EMPTY/FAIL':10} {case_id:42} "
            f"{call['elapsed_ms']:9.2f} ms {call['error_type'] or ''}",
            flush=True,
        )
    (args.output / "results.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=True) + "\n" for record in annotated),
        encoding="utf-8",
    )
    manifest_path = args.output / "manifest.json"
    manifest = {
            "schema_version": 1,
            "diagnostic_only": True,
            "score_bearing": False,
            "diagnostic_reason": "complete missing diagnostics for original empty outputs",
            "started_at": batch_started_at,
            "completed_at": utc_now(),
            "source_run": args.source_run.name,
            "source_attempt_ids": [parents[item["id"]]["attempt_id"] for item in selected_items],
            "provider": {
                "name": "moli-webfetch-diagnostic",
                "reported_version": EXPECTED_VERSION,
                "binary_sha256": EXPECTED_SHA256,
            },
            "parameters": {
                "timeout": args.timeout,
                "runs": 1,
                "retry_count": 0,
                "case_count": len(selected_items),
                "execution": "serial",
            },
            "attempt_ids": [record["attempt_id"] for record in annotated],
        }
    write_json(manifest_path, manifest)
    write_json(
        args.output / "diagnostic-lineage.json",
        {
            "schema_version": 1,
            "diagnostic_only": True,
            "score_bearing": False,
            "interpretation": {
                "successful_call": "not_reproduced; original result remains unchanged",
                "failed_call": "failure was reproduced or another failure occurred; inspect call log",
            },
            "calls": [
                {
                    "case_id": record["case_id"],
                    "original_attempt_id": record["corresponding_original_attempt_id"],
                    "diagnostic_attempt_id": record["attempt_id"],
                    "diagnostic_run_id": record.get("run_id"),
                    "diagnostic_ok": record.get("ok"),
                    "diagnostic_error_type": record.get("error_type"),
                    "diagnostic_interpretation": record["diagnostic_interpretation"],
                    "call_log_path": record.get("call_log_path"),
                }
                for record in annotated
            ],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
