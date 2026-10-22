"""Validate a WebArena catalog report profile and its frozen local artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from harness.trajectory_replay.catalog_action_audit import validate_catalog_action_audit
from harness.trajectory_replay.catalog_contracts import (
    repository_root,
    validate_catalog_comparison_release,
    validate_catalog_report_profile,
)
from harness.trajectory_replay.webarena_report import validate_catalog_acceptance_report
from harness.trajectory_replay.webarena_runtime_source import verify_webarena_runtime_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--data-home",
        type=Path,
        default=Path(os.environ.get("BROWSER_EVAL_DATA_HOME", Path.home() / ".browser-eval"))
        / "evidence/current",
    )
    parser.add_argument(
        "--action-overrides",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified-full/action-overrides.json"),
    )
    parser.add_argument(
        "--action-overrides-audit",
        type=Path,
        default=Path(
            "evaluation/trajectory-replay/webarena-verified-full/action-overrides-audit.json"
        ),
    )
    parser.add_argument(
        "--comparison-release",
        type=Path,
        required=True,
    )
    args = parser.parse_args()
    verify_webarena_runtime_source()
    profile = validate_catalog_report_profile(args.profile, args.data_home)
    root = repository_root(args.profile)
    action_overrides = (
        args.action_overrides
        if args.action_overrides.is_absolute()
        else root / args.action_overrides
    )
    action_overrides_audit = (
        args.action_overrides_audit
        if args.action_overrides_audit.is_absolute()
        else root / args.action_overrides_audit
    )
    evidence_root = args.data_home / profile["evidence_root"]
    validate_catalog_action_audit(
        action_overrides_audit,
        evidence_root / profile["accepted_catalog"]["path"],
        evidence_root / profile["accepted_portable_suite"]["path"],
        action_overrides,
    )
    validate_catalog_acceptance_report(
        evidence_root / Path(profile["report_manifest_path"]).parent,
        evidence_root / profile["report_archive_path"],
        profile=profile,
    )
    comparison_release = (
        args.comparison_release
        if args.comparison_release.is_absolute()
        else root / args.comparison_release
    )
    comparison = validate_catalog_comparison_release(comparison_release, args.data_home)
    print(comparison["publication"]["share_url"])


if __name__ == "__main__":
    main()
