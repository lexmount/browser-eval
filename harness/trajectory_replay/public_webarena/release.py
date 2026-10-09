"""Build, validate, and optionally publish the canonical trajectory report."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.reporting.publication import (
    atomic_write,
    bind_publication_files,
    json_bytes,
    recover_binding,
)

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


def main(project_root: Path) -> None:
    ROOT = project_root.resolve()
    CURRENT = ROOT / "evaluation/browser-use-public/current-report.json"
    ARCHIVE = ROOT / "publish/webarena-fixed-trajectory.zip"
    ATTESTATION = ROOT / "publish/webarena-fixed-trajectory-attestation.json"
    RECEIPT = ROOT / "publish/webarena-fixed-trajectory-site-receipt.json"
    OPERATION = ROOT / "publish/webarena-fixed-trajectory-operation.json"
    BINDING = ROOT / "publish/webarena-fixed-trajectory-binding.json"
    DEFAULT_FORMAL = (
        Path.home() / ".browser-eval/evidence/current/formal/webarena-public-trajectories"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, default=DEFAULT_FORMAL)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.publish:
        recover_binding(CURRENT, ATTESTATION, BINDING, schema="public_webarena_binding/1")
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    publication = current["publication"]
    prior = json.loads(RECEIPT.read_bytes()) if args.publish and RECEIPT.is_file() else {}
    pending = OPERATION.exists() or prior.get("submission_state") == "submitted_unverified"

    with tempfile.TemporaryDirectory(prefix="webarena-trajectory-release-") as temporary:
        staged = Path(temporary) / "formal"
        stage_report(args.formal_root, staged)
        semantic_checks = check_report_bundle(staged)
        if args.publish and pending:
            attestation = json.loads(ATTESTATION.read_bytes())
        else:
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

        binding_inputs = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (CURRENT, ATTESTATION)
        }

        if args.publish:
            if not RECEIPT.is_file():
                raise ValueError(
                    "the untracked Artifact Hub edit receipt is missing; restore or authorize "
                    "the receipt for the site in current-report.json before publishing"
                )
            published = _run_json(
                [
                    sys.executable,
                    str(ROOT / "scripts/publish_public_webarena_report.py"),
                    "--profile",
                    str(CURRENT),
                    "--formal-root",
                    str(staged),
                    "--journal",
                    str(OPERATION),
                    "--expected-version",
                    str(publication["version_id"]),
                    "--archive",
                    str(ARCHIVE),
                    "--attestation",
                    str(ATTESTATION),
                    "--receipt",
                    str(RECEIPT),
                    "--base-url",
                    str(publication["base_url"]),
                ],
                ROOT,
            )
            if published.get("slug") != publication["site_slug"]:
                raise ValueError("publisher returned a different Artifact Hub site")
            submitted_receipt = RECEIPT.read_bytes()
            verified = _run_json(_verification_command(publication, ARCHIVE, ROOT), ROOT)
            if verified.get("version") != published.get("version"):
                raise ValueError("published and exported Artifact Hub versions differ")
            if verified.get("files") != attestation["files"]:
                raise ValueError("export verification does not cover the complete package")
            if RECEIPT.read_bytes() != submitted_receipt:
                raise ValueError("publication receipt changed during export verification")
            bind_publication_files(
                CURRENT,
                ATTESTATION,
                BINDING,
                publication={
                    "version_id": str(published["version"]),
                    "share_url": str(published["url"]),
                    "submitted_files_verified": int(verified["files"]),
                },
                schema="public_webarena_binding/1",
                expected=binding_inputs,
            )
            receipt = json.loads(RECEIPT.read_bytes())
            receipt["submission_state"] = "verified"
            receipt["verified"] = True
            atomic_write(RECEIPT, json_bytes(receipt))
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
