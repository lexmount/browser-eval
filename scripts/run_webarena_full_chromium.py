"""Sweep the full official order once on Chromium, retaining every case outcome."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_execution import _browser_command, _run_task, _wait_cdp
from harness.trajectory_replay.remote_runtime import (
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
)
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_replay import _json_sha256
from scripts.build_webarena_template_candidates import _transfer

TRUSTED_RECORDED_HOSTS = frozenset(
    {
        "onestopmarket.example.com",
        "gitlab.example.com",
        "openstreetmap.example.com",
        "ec2-3-133-227-75.us-east-2.compute.amazonaws.com",
        "ec2-3-135-39-80.us-east-2.compute.amazonaws.com",
        "host.docker.internal",
        "192.168.5.2",
        "127.0.0.1",
    }
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(row, ensure_ascii=False) + "\n")
        target.flush()
        os.fsync(target.fileno())


def _completed(path: Path, ids: list[int]) -> set[int]:
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    seen: set[int] = set()
    for row in rows:
        task_id = row.get("task_id")
        if task_id not in ids or task_id in seen:
            raise ValueError("progress contains unknown or duplicate task ID")
        seen.add(task_id)
    if [row["task_id"] for row in rows] != [task_id for task_id in ids if task_id in seen]:
        raise ValueError("progress changed the official case order")
    return seen


def _run_candidate(
    task, benchmark, endpoint: str, output: Path, sites: dict, action_timeout_ms: int = 30_000
) -> dict:
    task_id = int(task.task_id.removeprefix("webarena-"))
    actions = rebind_remote_calls(list(task.tool_calls), sites, set(TRUSTED_RECORDED_HOSTS))
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
    result = _run_task(
        task_id=task_id,
        row={"actions": actions, "readiness": []},
        benchmark=benchmark,
        endpoint=endpoint,
        output=output,
        run_id=output.name,
        origins=origins,
        sites=sites,
        catalog=load_corrections(),
        portable_task=task,
        portable_action_timeout_ms=action_timeout_ms,
    )
    if result["status"] == "unsupported":
        result["status"] = "needs_response_builder"
    return result


def _run_curated_candidate(
    candidate: dict, benchmark, endpoint: str, output: Path, sites: dict
) -> dict:
    task_id = candidate["task_id"]
    if benchmark.get_task(task_id).is_retrieve_task:
        raise ValueError("curated template transfer lacks a live retrieval response builder")
    actions = rebind_remote_calls(candidate["actions"], sites, set(TRUSTED_RECORDED_HOSTS))
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
    return _run_task(
        task_id=task_id,
        row={"actions": actions, "readiness": []},
        benchmark=benchmark,
        endpoint=endpoint,
        output=output,
        run_id=output.name,
        origins=origins,
        sites=sites,
        catalog=load_corrections(),
    )


def _template_task(source_task, candidate: dict, official):
    replacements = [(row["from"], row["to"]) for row in candidate["replacements"]]
    return replace(
        source_task,
        task_id=f"webarena-{candidate['task_id']}",
        title=str(official.intent),
        tool_calls=tuple(candidate["actions"]),
        ground_truth=_transfer(source_task.ground_truth, replacements),
        evaluator={
            "kind": "webarena_verified",
            "task_id": candidate["task_id"],
            "revision": official.revision,
        },
    )


def _validate_template_candidate(candidate: dict, source_task, official) -> None:
    if (
        candidate["source_actions_sha256"] != _json_sha256(list(source_task.tool_calls))
        or candidate["actions_sha256"] != _json_sha256(candidate["actions"])
        or candidate["intent_template_id"] != official.intent_template_id
    ):
        raise ValueError(f"template candidate changed for task {candidate['task_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--human-suite", type=Path, required=True)
    parser.add_argument("--template-candidates", type=Path)
    parser.add_argument("--curated-trajectories", type=Path)
    parser.add_argument("--authored-suite", type=Path)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--cowagent", type=Path, required=True)
    parser.add_argument("--action-timeout-ms", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action_timeout_ms is None:
        args.action_timeout_ms = 15_000 if args.template_candidates else 30_000
    if args.action_timeout_ms < 1:
        raise ValueError("action timeout must be positive")
    if str(args.cowagent) not in sys.path:
        sys.path.insert(0, str(args.cowagent))
    queue = json.loads(args.queue.read_text(encoding="utf-8"))
    if queue.get("schema") != "lex_webarena_verified_full_queue/1" or queue.get(
        "human_suite_sha256"
    ) != _hash(args.human_suite):
        raise ValueError("full queue or human source changed")
    ids = [row["task_id"] for row in queue["rows"]]
    if ids != list(range(len(ids))):
        raise ValueError("queue does not follow official order")
    suite = load_suite(args.human_suite)
    by_id = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    authored_by_id = {}
    if args.authored_suite is not None:
        if queue.get("authored_suite_sha256") != _hash(args.authored_suite):
            raise ValueError("authored trajectory source changed")
        authored = load_suite(args.authored_suite)
        authored_by_id = {
            int(task.task_id.removeprefix("webarena-")): task for task in authored.tasks
        }
        scheduled = {
            row["task_id"] for row in queue["rows"] if row.get("source") == "authored_trajectory"
        }
        if scheduled != set(authored_by_id):
            raise ValueError("authored suite differs from its scheduled official cases")
    sites = load_remote_sites(args.site_map)
    benchmark = remote_benchmark(sites)
    template_tasks = {}
    template_curated = {}
    if args.template_candidates is not None:
        proposals = json.loads(args.template_candidates.read_text(encoding="utf-8"))
        if proposals.get("schema") != "lex_webarena_template_candidates/1" or proposals.get(
            "human_suite_sha256"
        ) != _hash(args.human_suite) or queue.get("template_candidates_sha256") != _hash(
            args.template_candidates
        ):
            raise ValueError("template candidate source changed")
        curated_hash = proposals.get("curated_trajectories_sha256")
        if curated_hash != (
            _hash(args.curated_trajectories) if args.curated_trajectories else None
        ):
            raise ValueError("curated trajectory source changed")
        curated_by_id = {}
        if args.curated_trajectories is not None:
            curated = json.loads(args.curated_trajectories.read_text(encoding="utf-8"))
            curated_by_id = {row["task_id"]: row for row in curated["trajectories"]}
        for candidate in proposals["candidates"]:
            task_id = candidate["task_id"]
            if task_id in template_tasks or task_id in template_curated:
                raise ValueError("duplicate template candidate")
            if candidate.get("source_kind", "human") == "curated":
                source = curated_by_id[candidate["source_task_id"]]
                official = benchmark.get_task(task_id)
                if (
                    candidate["source_actions_sha256"] != _json_sha256(source["tool_calls"])
                    or candidate["actions_sha256"] != _json_sha256(candidate["actions"])
                    or candidate["intent_template_id"] != official.intent_template_id
                    or official.is_retrieve_task
                ):
                    raise ValueError(f"curated template candidate changed for task {task_id}")
                template_curated[task_id] = candidate
                continue
            _validate_template_candidate(
                candidate, by_id[candidate["source_task_id"]], benchmark.get_task(task_id)
            )
            template_tasks[task_id] = _template_task(
                by_id[candidate["source_task_id"]], candidate, benchmark.get_task(task_id)
            )
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    binding_path = args.output / "inputs.json"
    binding = {
        "queue_sha256": _hash(args.queue),
        "human_suite_sha256": _hash(args.human_suite),
        "site_map_sha256": _hash(args.site_map),
        "binary_sha256": _hash(args.binary),
        "site_reset_between_tasks": False,
        "run_count_per_candidate": 1,
    }
    if args.template_candidates is not None:
        binding["template_candidates_sha256"] = _hash(args.template_candidates)
        binding["runner_revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        binding["cowagent_revision"] = subprocess.check_output(
            ["git", "-C", str(args.cowagent), "rev-parse", "HEAD"], text=True
        ).strip()
    if args.curated_trajectories is not None:
        binding["curated_trajectories_sha256"] = _hash(args.curated_trajectories)
    if args.authored_suite is not None:
        binding["authored_suite_sha256"] = _hash(args.authored_suite)
    if args.action_timeout_ms != 30_000:
        binding["action_timeout_ms"] = args.action_timeout_ms
    if binding_path.exists():
        if json.loads(binding_path.read_text(encoding="utf-8")) != binding:
            raise ValueError("resumed sweep inputs changed")
    else:
        binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    progress = args.output / "progress.jsonl"
    done = _completed(progress, ids)
    pending = [row for row in queue["rows"] if row["task_id"] not in done]
    if not any(row["status"] == "pending_first_sweep" for row in pending):
        return
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    browser_args = argparse.Namespace(engine="chromium", binary=args.binary)
    with tempfile.TemporaryDirectory(prefix="webarena-full-chromium-") as profile:
        process = subprocess.Popen(
            _browser_command(browser_args, port, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_cdp(endpoint, process)
            for row in pending:
                task_id = row["task_id"]
                if row["status"] == "pending_first_sweep":
                    if row.get("source") == "template_transfer" and task_id in template_curated:
                        outcome = _run_curated_candidate(
                            template_curated[task_id], benchmark, endpoint, args.output, sites
                        )
                    else:
                        task = (
                            template_tasks[task_id]
                            if row.get("source") == "template_transfer"
                            else authored_by_id[task_id]
                            if row.get("source") == "authored_trajectory"
                            else by_id[task_id]
                        )
                        outcome = _run_candidate(
                            task,
                            benchmark,
                            endpoint,
                            args.output,
                            sites,
                            action_timeout_ms=args.action_timeout_ms,
                        )
                else:
                    outcome = dict(row)
                _append(progress, outcome)
                print(
                    json.dumps(
                        {
                            key: value
                            for key, value in outcome.items()
                            if key not in {"error", "evidence_sha256"}
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
