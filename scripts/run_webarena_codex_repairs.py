"""Run bounded, read-only Codex jobs that produce repair proposals, never verdicts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from harness.browser_use.qualification import task_contract_sha256
from harness.browser_use.suite import load_suite
from scripts.build_webarena_repair_packet import (
    PACKET_SCHEMA,
    _redacted_evaluator_contract,
)
from scripts.build_webarena_repair_probe import build_probe

MODEL = "gpt-6-astra"
REASONING_EFFORT = "low"
RUN_SCHEMA = "lex_webarena_codex_repair_run/1"
MAX_CONCURRENCY = 8


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _load_and_validate_packet(path: Path, suite: dict[str, Any]) -> dict[str, Any]:
    packet = json.loads(path.read_text(encoding="utf-8"))
    if packet.get("schema") != PACKET_SCHEMA:
        raise ValueError(f"invalid repair packet schema: {path}")
    bindings = packet.get("bindings") or {}
    task = packet.get("task") or {}
    task_id = str(bindings.get("task_id") or "")
    if not task_id or str(task.get("id")) != task_id:
        raise ValueError(f"repair packet task binding is invalid: {path}")
    matches = [row for row in suite.get("tasks") or [] if str(row.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite does not uniquely contain repair packet task: {task_id}")
    source_task = matches[0]
    if bindings.get("task_sha256") != _json_sha256(source_task):
        raise ValueError(f"repair packet task sha256 mismatch: {task_id}")
    if bindings.get("task_contract_sha256") != task_contract_sha256(source_task):
        raise ValueError(f"repair packet task contract sha256 mismatch: {task_id}")
    evaluator_contracts, redactions = _redacted_evaluator_contract(source_task)
    expected_safe_task = {
        "id": source_task.get("id"),
        "title": source_task.get("title"),
        "difficulty": source_task.get("difficulty"),
        "tags": source_task.get("tags") or [],
        "trajectory": source_task.get("trajectory"),
        "evaluator": source_task.get("evaluator"),
        "environment": source_task.get("environment"),
        "evaluator_contracts": evaluator_contracts,
    }
    if task != expected_safe_task:
        raise ValueError(f"repair packet sanitized task payload mismatch: {task_id}")
    if ((packet.get("redactions") or {}).get("paths") or []) != redactions:
        raise ValueError(f"repair packet redaction inventory mismatch: {task_id}")

    references = packet.get("evidence_references") or {}
    expected_files = {
        "attempt": bindings.get("attempt_sha256"),
        "screenshot": ((bindings.get("final_page") or {}).get("screenshot") or {}).get("sha256"),
        "final_page": ((bindings.get("final_page") or {}).get("page") or {}).get("sha256"),
        "network_har": (bindings.get("network_har") or {}).get("sha256"),
    }
    for key, expected in expected_files.items():
        evidence_path = Path(str(references.get(key) or ""))
        if not evidence_path.is_file() or _sha256(evidence_path) != expected:
            raise ValueError(f"repair packet {key} evidence binding mismatch: {task_id}")
    bound_paths = {
        "screenshot": ((bindings.get("final_page") or {}).get("screenshot") or {}).get("path"),
        "final_page": ((bindings.get("final_page") or {}).get("page") or {}).get("path"),
        "network_har": (bindings.get("network_har") or {}).get("path"),
    }
    for key, bound_path in bound_paths.items():
        if Path(str(references[key])).resolve() != Path(str(bound_path)).resolve():
            raise ValueError(f"repair packet {key} path binding mismatch: {task_id}")
    return packet


def _prompt(packet_path: Path) -> str:
    return f"""Produce one candidate deterministic WebArena trajectory repair.

Read only the repair packet at {packet_path} and the evidence files explicitly listed in its
evidence_references. The AgentResponseEvaluator reference answer has intentionally been removed;
do not search the benchmark suite or other files for it. Infer actions and observed response fields
only from the task instruction, recorded trajectory, final visible page, screenshot, and HAR.

Return exactly one JSON object satisfying lex_browser_use_repair_proposal/1. Keep task_id unchanged.
Prefer the smallest reproducible action change. This is only a proposal: do not claim that it
passed qualification, and do not edit files.
"""


def _validate_proposal(
    suite: dict[str, Any], packet: dict[str, Any], proposal_path: Path
) -> dict[str, Any]:
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Codex did not return a JSON repair proposal") from exc
    task_id = str((packet.get("bindings") or {}).get("task_id") or "")
    if str(proposal.get("task_id") or "") != task_id:
        raise ValueError("Codex repair proposal changed the bound task ID")
    probe = build_probe(suite, proposal)
    with tempfile.TemporaryDirectory(prefix="webarena-repair-validation-") as directory:
        probe_path = Path(directory) / "probe.json"
        probe_path.write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")
        load_suite(probe_path)
    return proposal


def _command(
    codex_binary: str, task_directory: Path, screenshot: Path, output_path: Path
) -> list[str]:
    return [
        codex_binary,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "-s",
        "read-only",
        "-m",
        MODEL,
        "-c",
        f"model_reasoning_effort='{REASONING_EFFORT}'",
        "-C",
        str(task_directory),
        "-i",
        str(screenshot),
        "-o",
        str(output_path),
        "-",
    ]


def run_one(
    suite: dict[str, Any],
    packet_path: Path,
    output_root: Path,
    *,
    codex_binary: str = "codex",
    timeout_seconds: int = 900,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    packet = _load_and_validate_packet(packet_path.resolve(), suite)
    task_id = str(packet["bindings"]["task_id"])
    task_directory = output_root.resolve() / task_id
    task_directory.mkdir(parents=True, exist_ok=False)
    packet_copy = task_directory / "packet.json"
    _write_new(packet_copy, json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
    raw_output = task_directory / "proposal.raw.json"
    screenshot = Path(packet["evidence_references"]["screenshot"])
    command = _command(codex_binary, task_directory, screenshot, raw_output)
    command_record = {
        "binary": codex_binary,
        "argv": command,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "sandbox": "read-only",
        "ephemeral": True,
        "json_events": True,
        "timeout_seconds": timeout_seconds,
        "packet_sha256": _sha256(packet_copy),
        "qualification_effect": "none",
    }
    _write_new(
        task_directory / "command.json",
        json.dumps(command_record, indent=2, ensure_ascii=False) + "\n",
    )
    launch_error: str | None = None
    try:
        completed = run_subprocess(
            command,
            input=_prompt(packet_copy),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        returncode = None
    except OSError as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
        returncode = None
        launch_error = str(exc)
    _write_new(task_directory / "events.jsonl", stdout)
    _write_new(task_directory / "stderr.log", stderr)

    status = "codex_failed"
    error: str | None = None
    proposal: dict[str, Any] | None = None
    if launch_error is not None:
        status = "codex_failed"
        error = f"Codex could not be started: {launch_error}"
    elif returncode is None:
        status = "timeout"
        error = f"Codex exceeded {timeout_seconds} seconds"
    elif returncode != 0:
        error = f"Codex exited with status {returncode}"
    else:
        try:
            proposal = _validate_proposal(suite, packet, raw_output)
            status = "proposal_valid"
            _write_new(
                task_directory / "proposal.json",
                json.dumps(proposal, indent=2, ensure_ascii=False) + "\n",
            )
        except ValueError as exc:
            status = "proposal_invalid"
            error = str(exc)
    result = {
        "schema": RUN_SCHEMA,
        "task_id": task_id,
        "status": status,
        "error": error,
        "returncode": returncode,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "packet_sha256": _sha256(packet_copy),
        "proposal_sha256": _json_sha256(proposal) if proposal is not None else None,
        "qualification_effect": "none",
    }
    _write_new(
        task_directory / "result.json",
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    )
    return result


def run_repairs(
    suite_path: Path,
    packet_paths: list[Path],
    output_root: Path,
    *,
    max_workers: int,
    codex_binary: str = "codex",
    timeout_seconds: int = 900,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not 1 <= max_workers <= MAX_CONCURRENCY:
        raise ValueError(f"max_workers must be between 1 and {MAX_CONCURRENCY}")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    if not packet_paths:
        raise ValueError("at least one repair packet is required")
    suite_file = suite_path.resolve()
    suite = json.loads(suite_file.read_text(encoding="utf-8"))
    suite_sha256 = _sha256(suite_file)
    packets = [
        (path.resolve(), _load_and_validate_packet(path.resolve(), suite)) for path in packet_paths
    ]
    task_ids = [str(packet["bindings"]["task_id"]) for _, packet in packets]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("repair packets must bind unique task IDs")
    if any(packet["bindings"].get("suite_sha256") != suite_sha256 for _, packet in packets):
        raise ValueError("repair packet suite sha256 mismatch")
    output_root.mkdir(parents=True, exist_ok=False)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(packets))) as executor:
        futures = {
            executor.submit(
                run_one,
                suite,
                path,
                output_root,
                codex_binary=codex_binary,
                timeout_seconds=timeout_seconds,
                run_subprocess=run_subprocess,
            ): str(packet["bindings"]["task_id"])
            for path, packet in packets
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: task_ids.index(str(row["task_id"])))
    summary = {
        "schema": "lex_webarena_codex_repair_batch/1",
        "suite_sha256": suite_sha256,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "max_workers": max_workers,
        "qualification_effect": "none",
        "tasks": results,
        "status": (
            "complete"
            if all(row["status"] == "proposal_valid" for row in results)
            else "incomplete"
        ),
    }
    _write_new(
        output_root / "summary.json",
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--packet", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--codex-binary", default="codex")
    args = parser.parse_args()
    summary = run_repairs(
        args.suite,
        args.packet,
        args.output_root,
        max_workers=args.max_workers,
        codex_binary=args.codex_binary,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False))
    if summary["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
