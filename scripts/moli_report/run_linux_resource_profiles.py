"""Run independent Linux PSS/CPU profiles from a frozen Moli protocol.

Each configuration owns one container and one complete official resource run.
Containers execute strictly serially, so no comparison Moli process is resident
while another configuration is measured. The official profiler-off calibration
is retained only to verify observer effect; published PSS/CPU values come from
the single engine profile for each configuration.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.record_io import load_jsonl


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_protocol(protocol: dict, source: Path) -> None:
    configs = protocol.get("configs")
    tasks = protocol.get("task_ids")
    order = protocol.get("order")
    if (
        protocol.get("schema") != "moli_linux_resource_profiles/1"
        or not isinstance(configs, dict)
        or len(configs) != 4
        or not isinstance(tasks, list)
        or len(tasks) != 557
        or len(set(tasks)) != len(tasks)
        or protocol.get("attempts") != 5
        or protocol.get("jobs") != 1
        or protocol.get("sample_interval_ms") != 250
        or protocol.get("memory_metric") != "process_tree_pss"
        or protocol.get("orchestrator_sha256") != sha256(source)
    ):
        raise ValueError("invalid or incomplete Linux resource protocol")
    expected = [
        *[(method, "baseline") for method in configs],
        *[(method, "engine") for method in reversed(configs)],
    ]
    if order != [list(item) for item in expected]:
        raise ValueError("resource order must pair forward baselines with reverse profiles")
    for method, config in configs.items():
        if (
            not re.fullmatch(r"[a-z0-9_]{1,55}", method)
            or config.get("layout") not in {"off", "on"}
            or config.get("target") != "aarch64-unknown-linux-gnu"
            or config.get("profile") != "release"
            or len(config.get("sha256", "")) != 64
        ):
            raise ValueError(f"invalid configuration: {method}")


def verify_inputs(protocol: dict, runner: Path) -> None:
    for relative, digest in protocol["input_files"].items():
        if sha256(runner / relative) != digest:
            raise ValueError(f"runner input changed: {relative}")
    for config in protocol["configs"].values():
        if sha256(Path(config["binary"])) != config["sha256"]:
            raise ValueError("Moli binary changed")
    if sha256(Path(protocol["node_modules_lock"])) != protocol["node_modules_lock_sha256"]:
        raise ValueError("Node dependency lock changed")
    if sha256(Path(protocol["agent_browser_binary"])) != protocol["agent_browser_sha256"]:
        raise ValueError("agent-browser binary changed")


def active_set(method: str, config: dict) -> dict:
    return {
        "name": f"moli-linux-resource-{method}",
        "engines": {
            "moli": {
                "binary": "build_artifacts/moli/bin/moli",
                "version": config["version"],
                "sha256": config["sha256"],
                "sha256_12": config["sha256"][:12],
            }
        },
    }


def run_one(protocol: dict, output: Path, method: str, phase: str) -> dict:
    config = protocol["configs"][method]
    run_id = f"{method}_{phase}"
    run_dir = output / run_id
    if run_dir.exists():
        raise ValueError(f"run already exists: {run_dir}")
    active_path = output / f"{method}-active-set.json"
    payload = active_set(method, config)
    if active_path.exists() and json.loads(active_path.read_text()) != payload:
        raise ValueError("active set changed")
    active_path.write_text(json.dumps(payload, indent=2) + "\n")
    runner = Path(protocol["runner_root"])
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        f"{protocol['id']}-{method}-{phase}",
        "--mount",
        f"type=bind,src={runner},dst=/work,readonly",
        "--mount",
        f"type=bind,src={protocol['node_modules']},dst=/work/node_modules,readonly",
        "--mount",
        (
            f"type=bind,src={protocol['agent_browser_binary']},"
            "dst=/work/node_modules/agent-browser/bin/agent-browser-linux-arm64,readonly"
        ),
        "--mount",
        f"type=bind,src={output},dst=/results",
        "--mount",
        f"type=bind,src={active_path},dst=/work/build_artifacts/active-set.json,readonly",
        "--mount",
        (f"type=bind,src={config['binary']},dst=/work/build_artifacts/moli/bin/moli,readonly"),
        "-w",
        "/work",
        "-e",
        "NODE_PATH=/work/node_modules",
        "-e",
        "PATH=/work/node_modules/.bin:/home/evaluator/.cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin",
        "-e",
        "AGENT_BROWSER_SOCKET_DIR=/tmp/ab",
        "-e",
        "HTTP_PROXY=",
        "-e",
        "HTTPS_PROXY=",
        "-e",
        "http_proxy=",
        "-e",
        "https_proxy=",
        "-e",
        "ALL_PROXY=",
        "-e",
        "NO_PROXY=*",
        "--entrypoint",
        "python3",
        protocol["image"],
        "runner/run.py",
        "run",
        "--engines",
        "moli",
        "--moli-layout",
        config["layout"],
        "--k",
        str(protocol["attempts"]),
        "--jobs",
        str(protocol["jobs"]),
        "--seed",
        protocol["seed"],
        "--chrome-baseline",
        "off",
        "--score-mode",
        "independent",
        "--resource-profile",
        phase,
        "--resource-sample-interval-ms",
        str(protocol["sample_interval_ms"]),
        "--host-telemetry",
        "on",
        "--host-sample-interval-s",
        "0.5",
        "--run-id",
        run_id,
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
        command.extend(
            [
                "--resource-calibration-baseline",
                f"/results/{method}_baseline",
                "--resource-max-observer-effect-pct",
                str(protocol["max_observer_effect_pct"]),
            ]
        )
    for task in protocol["task_ids"]:
        command.extend(["--task", task])
    (output / f"{run_id}-command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (output / f"{run_id}.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    rows = load_jsonl(run_dir / "results.jsonl")
    expected = collections.Counter(
        (task, attempt)
        for task in protocol["task_ids"]
        for attempt in range(1, protocol["attempts"] + 1)
    )
    if (
        manifest["completion_status"] != "completed"
        or collections.Counter((row["task_id"], row["attempt"]) for row in rows) != expected
        or any(row["engine_provenance"]["binary_sha256"] != config["sha256"] for row in rows)
        or any(
            row["engine_provenance"]["layout_enabled"] != (config["layout"] == "on") for row in rows
        )
    ):
        raise ValueError("completed run does not match the protocol")
    if phase == "engine" and any(
        row["resource"].get("pss_peak_bytes") is None
        or row["resource"].get("sample_interval_ms") != protocol["sample_interval_ms"]
        for row in rows
    ):
        raise ValueError("engine profile is missing PSS measurements")
    return {
        "run_id": run_id,
        "rows": len(rows),
        "run_manifest_sha256": sha256(run_dir / "run_manifest.json"),
        "results_sha256": sha256(run_dir / "results.jsonl"),
        "host_summary_sha256": sha256(run_dir / "host_summary.json"),
    }


def execute(protocol_path: Path, output: Path) -> None:
    protocol = json.loads(protocol_path.read_text())
    validate_protocol(protocol, Path(__file__))
    verify_inputs(protocol, Path(protocol["runner_root"]))
    output.mkdir(parents=True, exist_ok=False)
    (output / "protocol.json").write_bytes(protocol_path.read_bytes())
    (output / "orchestrator.py").write_bytes(Path(__file__).read_bytes())
    results = {}
    for method, phase in protocol["order"]:
        if sha256(Path(__file__)) != protocol["orchestrator_sha256"]:
            raise ValueError("resource orchestrator changed during execution")
        print(f"START {method} {phase}", flush=True)
        results[f"{method}:{phase}"] = run_one(protocol, output, method, phase)
        verify_inputs(protocol, Path(protocol["runner_root"]))
        (output / "completed-profiles.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"VERIFIED {method} {phase}", flush=True)
    if sha256(Path(__file__)) != protocol["orchestrator_sha256"]:
        raise ValueError("resource orchestrator changed during execution")
    receipt = {
        "schema": "moli_linux_resource_profiles_receipt/1",
        "protocol_sha256": sha256(protocol_path),
        "orchestrator_sha256": sha256(Path(__file__)),
        "results": results,
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    execute(options.protocol, options.output)
