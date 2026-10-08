"""Reproduce the frozen Lexbench layout cohort in its declared Linux image."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from harness.web_fetch.safety import contained_path, safe_identifier


def sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular input file: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_hash(path: Path, expected: str) -> None:
    if sha(path) != expected:
        raise ValueError(f"Frozen input changed: {path.name}")


def validate_scope(cohort: dict, protocol: dict) -> None:
    tasks = cohort.get("ordered_task_ids")
    records = [task for group in cohort.get("strata", {}).values() for task in group]
    if (
        cohort.get("schema") != "moli_historical_failures_resource/1"
        or protocol.get("schema") != "moli_011_failure_retest/1"
        or not isinstance(tasks, list)
        or len(tasks) != 372
        or len(set(tasks)) != 372
        or len(records) != 372
        or {task["task_id"] for task in records} != set(tasks)
        or protocol.get("task_ids") != tasks
        or set(protocol.get("tasks", {})) != set(tasks)
        or cohort.get("k_runs") != 3
        or protocol.get("k") != 3
        or cohort.get("jobs") != 1
        or protocol.get("jobs") != 1
        or not isinstance(cohort.get("seed"), str)
        or not cohort["seed"]
        or protocol.get("seed") != cohort["seed"]
        or cohort.get("try_layout") is not False
        or protocol.get("try_layout") is not False
        or cohort.get("resource_sample_interval_ms") != 500
        or protocol.get("sample_interval_ms") != 500
        or any(
            document.get("chrome_baseline") != "off" or document.get("score_mode") != "independent"
            for document in (cohort, protocol)
        )
        or not protocol.get("inputs")
    ):
        raise ValueError("Invalid or incomplete frozen layout cohort")
    for task in tasks:
        safe_identifier(task)
    for item in records:
        bound = protocol["tasks"][item["task_id"]]
        if any(item.get(key) != bound.get(key) for key in ("path", "sha256")):
            raise ValueError("Cohort task differs from the frozen protocol")
        if protocol["inputs"].get(item["path"]) != item["sha256"]:
            raise ValueError("Task is absent from frozen input bindings")


def command(
    repo: Path,
    out: Path,
    image: str,
    modules: Path,
    agent: Path,
    mode: str,
    phase: str,
    *,
    cohort: Path,
    protocol: Path,
) -> list[str]:
    if mode not in {"off", "on"} or phase not in {"baseline", "engine"}:
        raise ValueError("Invalid layout mode or measurement phase")
    if repo.is_symlink() or modules.is_symlink() or out.is_symlink():
        raise ValueError("Reproduction roots must not be symlinks")
    c = json.loads(contained_path(cohort.parent, cohort.name, reject_symlinks=True).read_bytes())
    contract = json.loads(
        contained_path(protocol.parent, protocol.name, reject_symlinks=True).read_bytes()
    )
    validate_scope(c, contract)
    require_hash(contained_path(repo, "manifest.json", reject_symlinks=True), c["manifest_sha256"])
    require_hash(
        contained_path(repo, "build_artifacts/moli/bin/moli", reject_symlinks=True),
        c["moli_sha256"],
    )
    for tasks in c["strata"].values():
        for task in tasks:
            require_hash(contained_path(repo, task["path"], reject_symlinks=True), task["sha256"])
    for name, digest in contract["inputs"].items():
        require_hash(contained_path(repo, name, reject_symlinks=True), digest)
    require_hash(
        contained_path(modules, ".package-lock.json", reject_symlinks=True),
        contract["node_modules_lock"],
    )
    require_hash(agent, contract["agent_browser_sha256"])
    if image != contract["image"]:
        raise ValueError("Use the exact captured image identity")
    repo, out, modules, agent = (path.resolve() for path in (repo, out, modules, agent))
    name = mode + "_" + phase
    args = [
        "run",
        "--engines",
        "moli",
        "--moli-layout",
        mode,
        "--k",
        str(c["k_runs"]),
        "--jobs",
        "1",
        "--seed",
        c["seed"],
        "--chrome-baseline",
        "off",
        "--score-mode",
        "independent",
        "--resource-profile",
        phase,
        "--resource-sample-interval-ms",
        "500",
        "--host-telemetry",
        "on",
        "--host-sample-interval-s",
        "0.5",
        "--run-id",
        name,
        "--run-id-conflict",
        "error",
        "--out",
        "/results",
        "--no-report",
        "--no-progress",
        "--provenance-level",
        "minimal",
    ]
    if phase == "engine":
        args += [
            "--resource-calibration-baseline",
            "/results/" + mode + "_baseline",
            "--resource-max-observer-effect-pct",
            "10",
        ]
    for task in c["ordered_task_ids"]:
        args += ["--task", task]
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--mount",
        f"type=bind,src={repo},dst=/work,readonly",
        "--mount",
        f"type=bind,src={modules},dst=/work/node_modules,readonly",
        "--mount",
        f"type=bind,src={agent},dst=/work/node_modules/agent-browser/bin/agent-browser-linux-arm64,readonly",
        "--mount",
        f"type=bind,src={out},dst=/results",
        "-w",
        "/work",
        "-e",
        "NODE_PATH=/work/node_modules",
        "-e",
        "PATH=/work/node_modules/.bin:/home/evaluator/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin",
        "-e",
        "AGENT_BROWSER_SOCKET_DIR=/tmp/ab",
        "-e",
        "ALL_PROXY=",
        "-e",
        "HTTP_PROXY=",
        "-e",
        "HTTPS_PROXY=",
        "-e",
        "http_proxy=",
        "-e",
        "https_proxy=",
        "-e",
        "NO_PROXY=*",
        "--entrypoint",
        "python3",
        image,
        "runner/run.py",
    ] + args
    return command


def main(argv=None, *, default_directory: Path | None = None):
    directory = default_directory or Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--node-modules", required=True, type=Path)
    parser.add_argument("--agent-browser", required=True, type=Path)
    parser.add_argument("--cohort", type=Path, default=directory / "cohort.json")
    parser.add_argument("--protocol", type=Path, default=directory / "protocol.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--mode", required=True, choices=["off", "on"])
    parser.add_argument("--phase", required=True, choices=["baseline", "engine"])
    options = parser.parse_args(argv)
    bindings = {path: sha(path) for path in (options.cohort, options.protocol)}

    def prepare():
        return command(
            options.repo,
            options.output,
            options.image,
            options.node_modules,
            options.agent_browser,
            options.mode,
            options.phase,
            cohort=options.cohort,
            protocol=options.protocol,
        )

    invocation = prepare()
    print(json.dumps(invocation, indent=2))
    if options.execute:
        run = options.output / f"{options.mode}_{options.phase}"
        if run.exists() or run.is_symlink():
            raise ValueError("Run already exists; preserve the earlier evidence")
        options.output.mkdir(parents=True, exist_ok=True)
        subprocess.run(invocation, check=True)
        for path, expected in bindings.items():
            require_hash(path, expected)
        prepare()


if __name__ == "__main__":
    main()
