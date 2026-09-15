"""Build the checked-in 179-task WebArena human-trace catalog from official inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.webarena import build_trace_catalog, parse_drive_folder_html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder-html", type=Path, required=True)
    parser.add_argument("--webarena-dataset", type=Path, required=True)
    parser.add_argument("--webarena-commit", required=True)
    parser.add_argument("--verified-dataset", type=Path, required=True)
    parser.add_argument("--verified-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    files = parse_drive_folder_html(args.folder_html.read_text(encoding="utf-8"))
    payload = build_trace_catalog(
        files,
        original_tasks=json.loads(args.webarena_dataset.read_text(encoding="utf-8")),
        verified_tasks=json.loads(args.verified_dataset.read_text(encoding="utf-8")),
        original_commit=args.webarena_commit,
        verified_commit=args.verified_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
