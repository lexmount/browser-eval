#!/usr/bin/env python3
"""Validate and package the current trajectory-replay report for Artifact Site."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from harness.trajectory_replay.report_compaction import validate_compact_report
from harness.trajectory_replay.webarena_report import require_all_reference_validity

if __package__:
    from scripts.build_webarena_replay_report import _bound_dataset_path
    from scripts.check_webarena_replay_report import check as check_report
    from scripts.check_webarena_replay_visual import check as check_visual
else:
    from build_webarena_replay_report import _bound_dataset_path
    from check_webarena_replay_report import check as check_report
    from check_webarena_replay_visual import check as check_visual

PROFILE = Path("evaluation/trajectory-replay/webarena-verified/current-run.json")
REVIEW = Path("research/trajectory-replay-failure-review.json")
ARCHIVE = Path("publish/trajectory-replay-artifact-site.zip")
ATTESTATION = Path("publish/trajectory-replay-artifact-site.attestation.json")
VISUAL_RECEIPT = Path("publish/trajectory-replay-visual-qa.json")
MAX_FILES = 2_000
MAX_ARCHIVE_BYTES = 300_000_000
SKIP_PARTS = {".git", "node_modules", "__MACOSX"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_release(report: Path, visual_receipt: Path) -> dict[str, int]:
    summary = check_report(report)
    try:
        visual = json.loads(visual_receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        visual = {}
    if (
        visual.get("schema") != "lex_webarena_replay_visual_qa/3"
        or visual.get("report_tree_sha256") != tree_sha256(report)
        or visual.get("failures")
        or int(visual.get("states_checked") or 0) <= 0
    ):
        check_visual(report, visual_receipt)
    return summary


def package(
    report: Path,
    evidence: Path,
    output: Path,
    visual_receipt: Path,
    *,
    package_report: Path | None = None,
    profile_path: Path | None = None,
    review_path: Path | None = None,
    attestation_path: Path | None = None,
) -> dict:
    packaged = package_report or report
    profile_path = profile_path or PROFILE
    review_path = review_path or REVIEW
    attestation_path = attestation_path or ATTESTATION
    if not evidence.is_file():
        raise ValueError(f"missing replay evidence: {evidence}")
    summary = validate_release(report, visual_receipt)
    report_manifest = json.loads((report / "report-manifest.json").read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    dataset_path = _bound_dataset_path(profile)
    if (profile.get("scoring") or {}).get("require_all_reference_validity") is True:
        require_all_reference_validity(
            profile,
            json.loads(evidence.read_text(encoding="utf-8")),
            json.loads(dataset_path.read_text(encoding="utf-8")),
        )
    if (
        report_manifest.get("dataset_sha256") != sha256(dataset_path)
        or report_manifest.get("results_sha256") != sha256(evidence)
    ):
        raise ValueError("report and publication inputs refer to different result or dataset bytes")
    if report_manifest.get("diagnostic_closure_sha256"):
        raise ValueError("diagnostic-only appendices cannot enter the three-method release")
    if packaged != report:
        compact = validate_compact_report(packaged, max_files=MAX_FILES)
        if compact["task_routes"] != report_manifest["task_count"] or compact[
            "result_routes"
        ] != report_manifest["logical_results"]:
            raise ValueError("compact publication routes differ from the validated report")
    files = sorted(path for path in packaged.rglob("*") if path.is_file())
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"Artifact Site file count is outside 1..{MAX_FILES}: {len(files)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(packaged)
            if path.is_symlink() or SKIP_PARTS.intersection(relative.parts):
                raise ValueError(f"unsafe publication path: {relative}")
            if any(part.startswith(".") for part in relative.parts):
                raise ValueError(f"dot-leading publication path is unsupported: {relative}")
            archive.write(path, relative.as_posix())
    if output.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("publication package exceeds Artifact Site staged-upload limit")
    attestation = {
        "schema": "lex_webarena_replay_publication_attestation/1",
        "archive": str(output),
        "archive_sha256": sha256(output),
        "archive_bytes": output.stat().st_size,
        "report": str(report),
        "report_tree_sha256": tree_sha256(report),
        "packaged_report": str(packaged),
        "packaged_report_tree_sha256": tree_sha256(packaged),
        "file_count": len(files),
        "profile_sha256": sha256(profile_path),
        "dataset_sha256": sha256(dataset_path),
        "review_sha256": sha256(review_path),
        "evidence": str(evidence),
        "evidence_sha256": sha256(evidence),
        **summary,
    }
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    attestation_path.write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return attestation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/trajectory-replay"))
    parser.add_argument("--package-report", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ARCHIVE)
    parser.add_argument("--visual-receipt", type=Path, default=VISUAL_RECEIPT)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--review", type=Path, default=REVIEW)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION)
    args = parser.parse_args()
    print(
        json.dumps(
            package(
                args.report,
                args.evidence,
                args.output,
                args.visual_receipt,
                package_report=args.package_report,
                profile_path=args.profile,
                review_path=args.review,
                attestation_path=args.attestation,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
