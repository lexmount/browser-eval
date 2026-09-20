"""Locate clicks immediately following snapshots that label the target disabled.

This is a deliberately narrow, structural audit: it does not infer the state at
dispatch from an earlier snapshot and never changes scores. The supported
pattern is an exact :has-text JSON string and a matching disabled button label.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


def snapshot_readiness_gaps(trace: list[dict]) -> list[dict]:
    gaps = []
    for previous, step in zip(trace, trace[1:]):
        call = step.get("arguments", {})
        if call.get("action") != "click":
            continue
        match = re.search(r':has-text\(("(?:[^"\\]|\\.)*")\)', call.get("selector", ""))
        if not match:
            continue
        label = json.loads(match[1])
        lines = [line.strip() for line in str(previous.get("result", "")).splitlines()
                 if "button disabled:" in line and line.rstrip().endswith(": " + label)]
        if lines:
            gaps.append({"snapshot_step": previous["index"], "click_step": step["index"],
                         "selector": call["selector"], "snapshot_lines": lines,
                         "dispatch_state_known": bool(step.get("action_diagnostics"))})
    return gaps


def audit(results_path: Path) -> dict:
    content = results_path.read_bytes()
    results = json.loads(content)
    findings = []
    identities = set()
    for attempt in results["attempts"]:
        key = (attempt["participant_id"], attempt["task_id"])
        if key in identities:
            raise ValueError("duplicate method-task attempt")
        identities.add(key)
        reference = attempt["evidence"]["trace"]
        path = Path(reference["path"])
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("trace may not be a symlink")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference["sha256"]:
            raise ValueError("trace evidence changed")
        for gap in snapshot_readiness_gaps(json.loads(data)):
            findings.append({"participant_id": key[0], "task_id": key[1],
                             "historical_status": attempt["status"],
                             "trace_sha256": reference["sha256"], **gap})
    return {"schema": "lex_replay_disabled_snapshot_audit/1",
            "results_sha256": hashlib.sha256(content).hexdigest(),
            "attempts_scanned": len(identities),
            "scope": "adjacent snapshot-disabled button and exact has-text click; not exhaustive actionability review",
            "score_effect": "none", "findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.results)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"attempts_scanned": report["attempts_scanned"], "findings": len(report["findings"])}))


if __name__ == "__main__":
    main()
