#!/usr/bin/env python3
"""Bind independent source captures to a completed or retained partial run."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


def build_observations(run_dir: Path, captures_root: Path,
                       browser_observations: Path | None = None) -> list[dict]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    bindings = manifest.get("reference_checks", {}).get("cases", {})
    rendered = (json.loads(browser_observations.read_text())
                if browser_observations else {})
    if isinstance(rendered, list):
        rendered = {row["case_id"]: row for row in rendered}
    observations = []
    for case_id, binding in bindings.items():
        if case_id in rendered:
            row = rendered[case_id]
            checked_at = datetime.fromisoformat(row["checked_at"])
            if checked_at.tzinfo is None:
                raise ValueError("Browser observation timestamp lacks timezone: " + case_id)
            observations.append({
                "case_id": case_id,
                **row,
                "independent": True,
                "scope_identity": binding["scope_identity"],
            })
            continue
        evidence_path = captures_root / case_id / "evidence.json"
        capture = json.loads(evidence_path.read_text()) if evidence_path.is_file() else None
        if not capture or capture.get("status") != 200 or capture.get("missing_facts"):
            raise ValueError("Incomplete post-call capture: " + case_id)
        checked_at = datetime.fromisoformat(capture["checked_at"])
        if checked_at.tzinfo is None:
            raise ValueError("Capture timestamp lacks timezone: " + case_id)
        content = (captures_root / case_id / "reference.txt").read_text()
        observations.append({
            "case_id": case_id,
            "independent": True,
            "checked_at": capture["checked_at"],
            "content_text": content,
            "scope_identity": binding["scope_identity"],
            "completion": {"satisfied": True, "elapsed_ms": 0, "timeout_ms": 120000},
        })
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--browser-observations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists")
    observations = build_observations(
        args.run_dir, args.captures, args.browser_observations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(observations, ensure_ascii=False, indent=2) + "\n")
    print("Postcheck observations:", len(observations))


if __name__ == "__main__":
    main()
