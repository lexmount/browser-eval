"""Fail closed on report checks before a publisher performs any remote write."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check_report_release(source: Path, profile: Path,
                         *, run=subprocess.run) -> None:
    source = source.resolve()
    profile = profile.resolve()
    if not source.is_dir() or not profile.is_file():
        raise ValueError("Report or current profile is missing")
    if not profile.is_relative_to(ROOT / "evaluation"):
        raise ValueError("Report profile must be under evaluation/")
    for script, args in (
        ("check_report.py", [str(source), "--profile", str(profile)]),
        ("check_report_links.py", [str(source)]),
        ("check_report_revision.py", [str(source)]),
    ):
        run([sys.executable, str(ROOT / "scripts" / script), *args], check=True)
