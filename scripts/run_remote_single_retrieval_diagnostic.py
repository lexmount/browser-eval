"""Run one source-reviewed retrieval candidate on a live remote site (diagnostic)."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from webarena_verified.api import WebArenaVerified
from webarena_verified.types.config import WebArenaVerifiedConfig

from harness.trajectory_replay.remote_runtime import (
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
)
from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_replay import _json_sha256

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_remote_candidate_cohort_diagnostic import (  # noqa: E402
    _browser_command,
    _run_task,
    _wait_cdp,
    _write,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--engine", choices=("moli", "chromium"), required=True)
    parser.add_argument("--site-map", required=True, type=Path)
    parser.add_argument("--cowagent", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("diagnostic output exists")
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    task_id = candidate["task_id"]
    validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
    sites = load_remote_sites(args.site_map)
    benchmark = WebArenaVerified(
        config=WebArenaVerifiedConfig(
            test_data_file=args.dataset,
            environments=remote_benchmark(sites).config.environments,
        )
    )
    if str(args.cowagent) not in sys.path:
        sys.path.insert(0, str(args.cowagent))
    os.umask(0o077)
    args.output.mkdir(parents=True, mode=0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    origins = {f"{urlsplit(url).scheme}://{urlsplit(url).netloc}" for url in sites.values()}
    row = {
        "actions": rebind_remote_calls(
            candidate["tool_calls"], sites, {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
        ),
        "readiness": [],
    }
    row["bound_calls_sha256"] = _json_sha256(row["actions"])
    with tempfile.TemporaryDirectory(prefix="single-retrieval-browser-") as profile:
        process = subprocess.Popen(
            _browser_command(args, port, profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_cdp(endpoint, process)
            result = _run_task(
                task_id=task_id,
                row=row,
                benchmark=benchmark,
                endpoint=endpoint,
                output=args.output,
                run_id=args.output.name,
                origins=origins,
                sites=sites,
                catalog=load_corrections(),
                candidate_override_path=args.candidate,
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    _write(args.output / "result.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "evidence_sha256"}))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
