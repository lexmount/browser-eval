#!/usr/bin/env python3
"""Package the complete browser-agent report for Artifact Hub."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from scripts.check_browser_agent_report import check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/browser-agent"))
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("publish/browser-agent-sites.zip")
    )
    args = parser.parse_args()
    check(
        args.report,
        args.evidence,
        Path("evaluation/browser-agent/webarena-verified-pilot.json"),
        require_complete=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.report.rglob("*")):
            if path.is_file():
                if path.is_symlink():
                    raise ValueError(f"publication cannot contain symlinks: {path}")
                archive.write(path, path.relative_to(args.report))
    print(args.output)


if __name__ == "__main__":
    main()
