#!/usr/bin/env python3
"""Package the complete browser-use report for Artifact Hub."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.browser_use.failure_cause_review import review_path
from harness.browser_use.report_check import check
from harness.browser_use.report_visual import check as check_visual
from harness.browser_use.report_visual import receipt_is_current
from harness.browser_use.selection import report_selection_path
from harness.reporting.assets import bound_json_asset
from harness.reporting.links import check as check_links
from harness.reporting.publication import package_archive

ATTESTATION = Path("publish/browser-use-sites.attestation.json")
VISUAL_RECEIPT = Path("publish/browser-use-visual-qa.json")


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
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def validate_release(
    report: Path, evidence: list[Path], visual_receipt: Path,
    *, authority_root: Path | None = None, profile_path: Path | None = None,
) -> None:
    """Run every release gate before creating a publishable archive."""

    check(report, evidence, None, require_complete=True,
          authority_root=authority_root, profile_path=profile_path)
    check_links(report)
    if not receipt_is_current(report, visual_receipt):
        check_visual(report, visual_receipt)


def package(
    report: Path, evidence: list[Path], output: Path, visual_receipt: Path,
    *, authority_root: Path | None = None, attestation_path: Path = ATTESTATION,
    profile_path: Path | None = None,
) -> dict:
    authority = authority_root if authority_root is not None else Path(__file__).parents[2]
    if profile_path is None and authority_root is None:
        raise ValueError("An explicit report profile is required; pass --profile")
    profile = profile_path or authority / "evaluation/browser-use/current-report.json"
    names = ("manifest.json", "run-config.json", "attempts.jsonl", "result-review.json")
    inputs = [profile]
    profile_data = json.loads(profile.read_text())
    history_path, _ = bound_json_asset(profile_data.get("report_history"), root=profile.parent)
    if history_path is not None:
        inputs.append(history_path)
    inputs.append(report_selection_path(profile_data, authority_root=profile.parent))
    review = review_path(profile_data, authority_root=profile.parent)
    if review.is_file():
        inputs.append(review)
    frozen = profile_data.get("official_dataset")
    if frozen:
        inputs.append(Path(frozen["path"]))
    inputs.extend(path / name for path in evidence for name in names)

    def attest(archive: Path) -> dict:
        return {
            "schema": "lex_browser_use_publication_attestation/2",
            "archive": str(output), "archive_sha256": sha256(archive),
            "report": str(report), "report_tree_sha256": tree_sha256(report),
            "profile_sha256": sha256(profile),
            "evidence": [str(path) for path in evidence],
            "evidence_files": {
                str(path): {name: sha256(path / name) for name in names} for path in evidence
            },
        }

    return package_archive(
        report, output, attestation_path, inputs=inputs,
        validate=lambda: validate_release(report, evidence, visual_receipt,
                                          authority_root=authority, profile_path=profile_path),
        attest=attest,
        excluded=(".generated-report",),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/browser-use"))
    parser.add_argument("--evidence", required=True, action="append", type=Path)
    parser.add_argument("--output", type=Path, default=Path("publish/browser-use-sites.zip"))
    parser.add_argument("--visual-receipt", type=Path, default=VISUAL_RECEIPT)
    parser.add_argument("--authority-root", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION)
    args = parser.parse_args()
    package(
        args.report, args.evidence, args.output, args.visual_receipt,
        authority_root=args.authority_root, attestation_path=args.attestation,
        profile_path=args.profile,
    )
    print(args.output)


if __name__ == "__main__":
    main()
