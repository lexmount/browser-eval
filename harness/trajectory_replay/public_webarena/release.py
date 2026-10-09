"""Build, validate, and optionally publish the canonical trajectory report."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .publication import build, check_report_bundle, stage_report


def _run_json(command: list[str], project_root: Path) -> dict:
    completed = subprocess.run(command, cwd=project_root, text=True, capture_output=True)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"release helper failed ({completed.returncode}): {detail}")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("release helper returned no result")
    return json.loads(lines[-1])


def _verification_command(publication: dict, archive: Path, project_root: Path) -> list[str]:
    return [
        sys.executable,
        str(project_root / "scripts/verify_artifact_hub.py"),
        "--slug",
        str(publication["site_slug"]),
        "--base-url",
        str(publication["base_url"]),
        "--expected-archive",
        str(archive),
    ]


def _update_profile(profile: Path, version: str, files: int) -> None:
    current = json.loads(profile.read_text(encoding="utf-8"))
    current["publication"]["version_id"] = version
    current["publication"]["submitted_files_verified"] = files
    temporary = profile.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(profile)


def main(project_root: Path) -> None:
    ROOT = project_root.resolve()
    CURRENT = ROOT / "evaluation/browser-use-public/current-report.json"
    ARCHIVE = ROOT / "publish/webarena-fixed-trajectory.zip"
    ATTESTATION = ROOT / "publish/webarena-fixed-trajectory-attestation.json"
    RECEIPT = ROOT / "publish/webarena-fixed-trajectory-site-receipt.json"
    DEFAULT_FORMAL = (
        Path.home() / ".browser-eval/evidence/current/formal/webarena-public-trajectories"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, default=DEFAULT_FORMAL)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    publication = current["publication"]

    with tempfile.TemporaryDirectory(prefix="webarena-trajectory-release-") as temporary:
        staged = Path(temporary) / "formal"
        stage_report(args.formal_root.resolve(), staged)
        semantic_checks = check_report_bundle(staged)
        attestation = build(staged, ARCHIVE, ATTESTATION, authority=CURRENT)
        print(
            "FAST GATES PASS: comparison semantics, report binding, internal links, "
            f"privacy scan, archive hash; {semantic_checks['metrics']} metrics, "
            f"{attestation['files']} files"
        )
        print(
            "AUDIT MODE: presentation-only fast gates. If the dataset, runner, scorer, "
            "evidence, denominator, or claimed result changed, first run "
            "`make public-webarena-report-audit`."
        )
        if not args.publish:
            print(
                "LOCAL ONLY: no network write. "
                "Run `make public-webarena-report-release` to publish."
            )
            return

        with tempfile.TemporaryDirectory(prefix="webarena-trajectory-receipt-") as receipt_dir:
            if not RECEIPT.is_file():
                raise ValueError(
                    "the untracked Artifact Hub edit receipt is missing; restore or authorize "
                    "the receipt for the site in current-report.json before publishing"
                )
            pending_receipt = Path(receipt_dir) / "receipt.json"
            shutil.copy2(RECEIPT, pending_receipt)
            published = _run_json(
                [
                    sys.executable,
                    str(ROOT / "scripts/publish_webarena_trajectory_artifact_hub.py"),
                    "--update",
                    "--expected-version",
                    str(publication["version_id"]),
                    "--archive",
                    str(ARCHIVE),
                    "--attestation",
                    str(ATTESTATION),
                    "--receipt",
                    str(pending_receipt),
                    "--base-url",
                    str(publication["base_url"]),
                ],
                ROOT,
            )
            if published.get("slug") != publication["site_slug"]:
                raise ValueError("publisher returned a different Artifact Hub site")
            verified = _run_json(_verification_command(publication, ARCHIVE, ROOT), ROOT)
            if verified.get("version") != published.get("version"):
                raise ValueError("published and exported Artifact Hub versions differ")
            pending_receipt.replace(RECEIPT)
            _update_profile(CURRENT, str(published["version"]), int(verified["files"]))
            print(
                json.dumps(
                    {
                        "status": "published_and_verified",
                        "version": published["version"],
                        "files": verified["files"],
                        "url": publication["share_url"],
                    },
                    ensure_ascii=False,
                )
            )
