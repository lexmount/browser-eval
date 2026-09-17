#!/usr/bin/env python3
"""Package the complete Browser Use report for Artifact Site."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

if __package__:
    from scripts.check_browser_use_report import check
    from scripts.check_browser_use_visual import check as check_visual
    from scripts.check_report_links import check as check_links
else:  # Direct execution adds scripts/, not the repository root.
    from check_browser_use_report import check
    from check_browser_use_visual import check as check_visual
    from check_report_links import check as check_links

ATTESTATION = Path("publish/browser-use-sites.attestation.json")
PROFILE = Path("evaluation/browser-use/current-report.json")
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


def validate_release(report: Path, evidence: list[Path], visual_receipt: Path) -> None:
    """Run every release gate before creating a publishable archive."""

    check(
        report,
        evidence,
        Path("evaluation/browser-use/webarena-verified-pilot.json"),
        require_complete=True,
    )
    check_links(report)
    try:
        visual = json.loads(visual_receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        visual = {}
    if (
        visual.get("schema") != "lex_browser_use_visual_qa/1"
        or visual.get("report_tree_sha256") != tree_sha256(report)
        or visual.get("failures")
        or int(visual.get("states_checked") or 0) <= 0
    ):
        check_visual(report, visual_receipt)


def publication_files(report: Path) -> list[Path]:
    files = []
    for path in sorted(report.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError(f"publication cannot contain symlinks: {path}")
        relative = path.relative_to(report)
        if relative.as_posix() == ".generated-report":
            continue  # Local build marker; dot-leading site paths are rejected.
        if any(part.startswith(".") or part.lower() in {"node_modules", "__macosx"} for part in relative.parts):
            raise ValueError(f"publication contains a forbidden site path: {relative}")
        files.append(path)
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/browser-use"))
    parser.add_argument("--evidence", required=True, action="append", type=Path)
    parser.add_argument("--output", type=Path, default=Path("publish/browser-use-sites.zip"))
    parser.add_argument("--visual-receipt", type=Path, default=VISUAL_RECEIPT)
    args = parser.parse_args()
    validate_release(args.report, args.evidence, args.visual_receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in publication_files(args.report):
            archive.write(path, path.relative_to(args.report))
    evidence_files = {
        str(evidence): {
            name: sha256(evidence / name)
            for name in ("manifest.json", "run-config.json", "attempts.jsonl", "result-review.json")
        }
        for evidence in args.evidence
    }
    attestation = {
        "schema": "lex_browser_use_publication_attestation/2",
        "archive": str(args.output),
        "archive_sha256": sha256(args.output),
        "report": str(args.report),
        "report_tree_sha256": tree_sha256(args.report),
        "profile_sha256": sha256(PROFILE),
        "evidence": [str(path) for path in args.evidence],
        "evidence_files": evidence_files,
    }
    ATTESTATION.write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
