"""Replay one frozen layout cohort inside the captured Linux runtime image."""

import argparse
import hashlib
import json
import pathlib
import subprocess

p = argparse.ArgumentParser()
p.add_argument("--repo", required=True, type=pathlib.Path)
p.add_argument("--output", required=True, type=pathlib.Path)
p.add_argument("--image", required=True, help="Captured runtime image identity shown in the report")
p.add_argument(
    "--node-modules", required=True, type=pathlib.Path, help="Provisioned Linux Node dependencies"
)
p.add_argument(
    "--agent-browser", required=True, type=pathlib.Path, help="Linux ARM64 agent-browser executable"
)
p.add_argument(
    "--execute", action="store_true", help="Execute after verification; otherwise print the command"
)
p.add_argument("--mode", required=True, choices=["off", "on"])
p.add_argument("--phase", required=True, choices=["baseline", "engine"])
a = p.parse_args()
repo = a.repo.resolve()
out = a.output.resolve()
modules = a.node_modules.resolve()
agent = a.agent_browser.resolve()
c = json.loads(pathlib.Path(__file__).with_name("cohort.json").read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert sha(repo / "manifest.json") == c["manifest_sha256"]
assert sha(repo / "build_artifacts/moli/bin/moli") == c["moli_sha256"]
for tasks in c["strata"].values():
    for task in tasks:
        assert sha(repo / task["path"]) == task["sha256"]
contract = json.loads(pathlib.Path(__file__).with_name("protocol.json").read_text())
for name, digest in contract["inputs"].items():
    assert sha(repo / name) == digest, name
assert sha(modules / ".package-lock.json") == contract["node_modules_lock"]
assert sha(agent) == contract["agent_browser_sha256"]
assert a.image == contract["image"], "Use the exact captured image identity"
name = a.mode + "_" + a.phase
args = [
    "run",
    "--engines",
    "moli",
    "--moli-layout",
    a.mode,
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
    a.phase,
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
if a.phase == "engine":
    args += [
        "--resource-calibration-baseline",
        "/results/" + a.mode + "_baseline",
        "--resource-max-observer-effect-pct",
        "10",
    ]
for task in c["ordered_task_ids"]:
    args += ["--task", task]
assert "--try-layout" not in args
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
    a.image,
    "runner/run.py",
] + args
print(json.dumps(command, indent=2))
if a.execute:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True)
