"""Generate one evidence-bounded Astra repair candidate for Chromium validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.browser_use.config import load_runtime_env
from harness.browser_use.repair_planner import (
    api_configuration,
    build_repair_packet,
    build_responses_request,
    candidate_to_proposal,
    planner_record,
    request_repair_candidate,
)
from scripts.build_webarena_repair_probe import build_probe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    attempt = json.loads(args.attempt.read_text(encoding="utf-8"))
    attempt_dir = args.attempt.parent
    packet = build_repair_packet(suite, attempt, attempt_dir)
    screenshot = attempt_dir / "final.png"
    request_body = build_responses_request(
        packet,
        model=args.model,
        screenshot_path=screenshot if screenshot.is_file() else None,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "repair-packet.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "task_id": packet["task_id"]}))
        return

    load_runtime_env(args.env_file)
    api_key, api_base = api_configuration()
    candidate, response = request_repair_candidate(
        request_body,
        api_key=api_key,
        api_base=api_base,
        timeout_seconds=args.timeout_seconds,
    )
    proposal = candidate_to_proposal(packet["task_id"], candidate)
    # Schema-load the resulting one-task probe before recording a usable proposal.
    probe = build_probe(suite, proposal)
    (args.output / "proposal.json").write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output / "probe-suite.json").write_text(
        json.dumps(probe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    record = planner_record(packet=packet, request_body=request_body, response=response)
    if screenshot.is_file():
        import hashlib

        record["screenshot_sha256"] = hashlib.sha256(screenshot.read_bytes()).hexdigest()
    (args.output / "planner-record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "candidate_generated",
                "task_id": packet["task_id"],
                "response_id": response.get("id"),
                "model": response.get("model"),
                "usage": response.get("usage") or {},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
