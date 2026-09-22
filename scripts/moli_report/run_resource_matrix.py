"""Serialize frozen Lexbench configurations in rotating order for every task attempt.

Uses the unchanged Lexbench driver, sampler, grader and manifests. Each child
owns its browser and output directory; the coordinator grants one call at a time.
The protocol and every physical call are retained outside Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import sys
import time
import traceback
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule(task_ids: list[str], attempts: int, methods: list[str]):
    if not task_ids or len(set(task_ids)) != len(task_ids) or attempts < 1:
        raise ValueError("invalid task population")
    if len(methods) < 2 or len(set(methods)) != len(methods):
        raise ValueError("need distinct comparison methods")
    for task_index, task_id in enumerate(task_ids):
        for attempt in range(1, attempts + 1):
            offset = (task_index * attempts + attempt - 1) % len(methods)
            for method in methods[offset:] + methods[:offset]:
                yield method, task_id, attempt


def verify_calls(protocol: dict, calls: list[dict]) -> None:
    expected = list(schedule(protocol["task_ids"], protocol["attempts"], list(protocol["methods"])))
    if [(c["method"], c["task_id"], c["attempt"]) for c in calls] != expected:
        raise ValueError("physical calls differ from the predeclared complete order")
    previous_end = 0
    for call in calls:
        if call["started_ns"] < previous_end or call["ended_ns"] < call["started_ns"]:
            raise ValueError("overlapping or invalid physical call intervals")
        previous_end = call["ended_ns"]
        if call["binary_sha256"] != protocol["methods"][call["method"]]["sha256"]:
            raise ValueError("binary identity mismatch")


def worker(channel, protocol: dict, method_id: str, output: str) -> None:
    root = Path(protocol["runner_root"])
    out = Path(output)
    method = protocol["methods"][method_id]
    try:
        with (out / f"{method_id}.log").open("w") as log:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            os.chdir(root)
            sys.path.insert(0, str(root))
            from runner import run

            meta = run.ENGINE_DEFS["moli"]
            meta.update(
                binary=Path(method["binary"]),
                version=method["version"],
                sha256=method["sha256"],
                sha256_12=method["sha256"][:12],
                cdp_port=run.find_free_port(),
            )
            run.ACTIVE_ENGINE_SET = {
                "name": protocol["id"],
                "engines": {
                    "moli": {
                        key: str(value) if isinstance(value, Path) else value
                        for key, value in meta.items()
                        if key in {"binary", "version", "sha256", "sha256_12"}
                    }
                },
            }
            original_launch = run.BrowserManager.launch
            original_attempt = run.run_driver_attempt
            launch_count = 0
            call_count = 0
            total = len(protocol["task_ids"]) * protocol["attempts"]

            def launch(manager, *args, **kwargs):
                nonlocal launch_count
                if launch_count:
                    channel.send({"kind": "ready", "index": call_count})
                    if channel.recv() != {"grant": call_count}:
                        raise ValueError("invalid call grant")
                launch_count += 1
                return original_launch(manager, *args, **kwargs)

            def attempt(*args, **kwargs):
                nonlocal call_count
                task, ordinal = args[3], args[5]
                expected = (
                    protocol["task_ids"][call_count // protocol["attempts"]],
                    call_count % protocol["attempts"] + 1,
                )
                if (task.task_id, ordinal) != expected:
                    raise ValueError("resolved task order changed")
                started = time.monotonic_ns()
                row = original_attempt(*args, **kwargs)
                event = {
                    "kind": "done",
                    "method": method_id,
                    "task_id": task.task_id,
                    "attempt": ordinal,
                    "started_ns": started,
                    "ended_ns": time.monotonic_ns(),
                    "binary_sha256": method["sha256"],
                    "row_sha256": hashlib.sha256(
                        json.dumps(row, sort_keys=True).encode()
                    ).hexdigest(),
                }
                call_count += 1
                channel.send(event)
                if call_count == total and channel.recv() != {"finish": True}:
                    raise ValueError("invalid completion grant")
                return row

            run.BrowserManager.launch = launch
            run.run_driver_attempt = attempt
            argv = [
                "run",
                *(value for task in protocol["task_ids"] for value in ("--task", task)),
                "--engines",
                "moli",
                "--moli-layout",
                method["layout"],
                "--chrome-baseline",
                "off",
                "--score-mode",
                "independent",
                "--seed",
                protocol["seed"],
                "--k",
                str(protocol["attempts"]),
                "--jobs",
                "1",
                "--host-telemetry",
                "on",
                "--host-sample-interval-s",
                "0.5",
                "--resource-profile",
                "engine",
                "--resource-sample-interval-ms",
                "250",
                "--provenance-level",
                "full",
                "--run-id-conflict",
                "error",
                "--no-progress",
                "--run-id",
                method["run_id"],
            ]
            if run.main(argv) != 0:
                raise RuntimeError("Lexbench resource run failed")
            channel.send({"kind": "finished"})
    except BaseException:
        channel.send({"kind": "error", "message": traceback.format_exc()})
        raise
    finally:
        channel.close()


def receive(connection, expected: str) -> dict:
    if not connection.poll(600):
        raise TimeoutError(f"waiting for resource worker {expected}")
    message = connection.recv()
    if message.get("kind") != expected:
        raise RuntimeError(f"expected {expected}: {message}")
    return message


def execute(protocol_path: Path, output: Path) -> None:
    protocol = json.loads(protocol_path.read_text())
    planned = list(schedule(protocol["task_ids"], protocol["attempts"], list(protocol["methods"])))
    if protocol["selected_round"] != 1:
        raise ValueError("this protocol selects exactly one round before execution")
    if sha(Path(__file__)) != protocol["orchestrator_sha256"]:
        raise ValueError("orchestrator differs from the frozen protocol")
    runner_root = Path(protocol["runner_root"])
    for relative, digest in protocol["input_files"].items():
        path = runner_root / relative
        if not path.resolve().is_relative_to(runner_root.resolve()) or sha(path) != digest:
            raise ValueError(f"frozen input changed: {relative}")
    for method in protocol["methods"].values():
        if sha(Path(method["binary"])) != method["sha256"]:
            raise ValueError("binary changed before execution")
    output.mkdir(parents=True, exist_ok=False)
    (output / "protocol.json").write_bytes(protocol_path.read_bytes())
    (output / "orchestrator.py").write_bytes(Path(__file__).read_bytes())
    context = multiprocessing.get_context("spawn")
    workers = {}
    calls = []
    try:
        for method in protocol["methods"]:
            parent, child = context.Pipe()
            process = context.Process(target=worker, args=(child, protocol, method, str(output)))
            process.start()
            child.close()
            workers[method] = (parent, process)
        ready = {method: receive(channel, "ready") for method, (channel, _) in workers.items()}
        with (output / "physical-calls.jsonl").open("w") as log:
            for index, (method, task, ordinal) in enumerate(planned):
                channel, _ = workers[method]
                grant = ready.pop(method) if method in ready else receive(channel, "ready")
                channel.send({"grant": grant["index"]})
                event = receive(channel, "done")
                if (event["method"], event["task_id"], event["attempt"]) != (method, task, ordinal):
                    raise ValueError("worker executed a different physical call")
                log.write(json.dumps(event) + "\n")
                log.flush()
                calls.append(event)
                if (index + 1) % (protocol["attempts"] * len(workers)) == 0:
                    completed = (index + 1) // (protocol["attempts"] * len(workers))
                    print(
                        f"Completed {completed}/{len(protocol['task_ids'])} tasks",
                        flush=True,
                    )
        for channel, _ in workers.values():
            channel.send({"finish": True})
        for channel, process in workers.values():
            receive(channel, "finished")
            process.join(30)
            if process.exitcode != 0:
                raise RuntimeError("resource worker did not finish cleanly")
        verify_calls(protocol, calls)
        for relative, digest in protocol["input_files"].items():
            if sha(runner_root / relative) != digest:
                raise ValueError(f"input changed during execution: {relative}")
        results = {}
        for method_id, method in protocol["methods"].items():
            if sha(Path(method["binary"])) != method["sha256"]:
                raise ValueError("binary changed during execution")
            run_dir = Path(protocol["runner_root"]) / "runs" / method["run_id"]
            manifest = json.loads((run_dir / "run_manifest.json").read_text())
            if (
                manifest["completion_status"] != "completed"
                or manifest["completed_result_rows"]
                != len(protocol["task_ids"]) * protocol["attempts"]
            ):
                raise ValueError("incomplete run manifest")
            rows = [
                json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()
            ]
            row_hashes = {
                (row["task_id"], row["attempt"]): hashlib.sha256(
                    json.dumps(row, sort_keys=True).encode()
                ).hexdigest()
                for row in rows
            }
            method_calls = [call for call in calls if call["method"] == method_id]
            if (
                len(row_hashes) != len(rows)
                or len(rows) != len(method_calls)
                or any(
                    row_hashes.get((call["task_id"], call["attempt"])) != call["row_sha256"]
                    for call in method_calls
                )
            ):
                raise ValueError("physical call receipt does not match saved results")
            results[method_id] = {
                name: sha(run_dir / name)
                for name in ("run_manifest.json", "results.jsonl", "host_summary.json")
            }
        receipt = {
            "schema": "moli_resource_matrix_receipt/1",
            "protocol_sha256": sha(protocol_path),
            "orchestrator_sha256": sha(Path(__file__)),
            "selected_round": 1,
            "physical_calls": len(calls),
            "physical_calls_sha256": sha(output / "physical-calls.jsonl"),
            "results": results,
        }
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt), flush=True)
    finally:
        for channel, process in workers.values():
            if process.is_alive():
                process.terminate()
                process.join(30)
            channel.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    execute(options.protocol, options.output)
