"""Build, validate, and optionally publish the canonical trajectory report."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.trajectory_replay.public_webarena.report import write_latest_report  # noqa: E402
from harness.trajectory_replay.public_webarena.result_audit import audit_result_set  # noqa: E402
from scripts.check_webarena_trajectory_report import check_report_bundle  # noqa: E402
from scripts.package_webarena_trajectory_report import build  # noqa: E402

CURRENT = ROOT / "evaluation/browser-use-public/current-report.json"
DEFAULT_FORMAL = (
    Path.home() / ".browser-eval/evidence/current/formal/webarena-public-trajectories"
)
ARCHIVE = ROOT / "publish/webarena-fixed-trajectory.zip"
ATTESTATION = ROOT / "publish/webarena-fixed-trajectory-attestation.json"
RECEIPT = ROOT / "publish/webarena-fixed-trajectory-site-receipt.json"


def _run_json(command: list[str]) -> dict:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"release helper failed ({completed.returncode}): {detail}")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("release helper returned no result")
    return json.loads(lines[-1])


def _verification_command(publication: dict, archive: Path = ARCHIVE) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts/verify_artifact_hub.py"),
        "--slug",
        str(publication["site_slug"]),
        "--base-url",
        str(publication["base_url"]),
        "--expected-archive",
        str(archive),
    ]


def _stage(formal_root: Path, destination: Path) -> None:
    run = formal_root / "run"
    if not run.is_dir():
        raise ValueError(f"formal run is missing: {run}")
    shutil.copytree(run, destination / "run")
    (destination / "reports").mkdir()
    manifest = json.loads((destination / "run/manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((destination / "run/summary.json").read_text(encoding="utf-8"))
    audit_result_set(manifest, summary)
    write_latest_report(destination / "reports", manifest=manifest, summary=summary)


def _update_profile(version: str, files: int) -> None:
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    current["publication"]["version_id"] = version
    current["publication"]["submitted_files_verified"] = files
    temporary = CURRENT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CURRENT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, default=DEFAULT_FORMAL)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    publication = current["publication"]

    with tempfile.TemporaryDirectory(prefix="webarena-trajectory-release-") as temporary:
        staged = Path(temporary) / "formal"
        _stage(args.formal_root.resolve(), staged)
        semantic_checks = check_report_bundle(staged)
        attestation = build(staged, ARCHIVE, ATTESTATION)
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
                "LOCAL ONLY: no network write. Run `make public-webarena-report-release` to publish."
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
                ]
            )
            if published.get("slug") != publication["site_slug"]:
                raise ValueError("publisher returned a different Artifact Hub site")
            verified = _run_json(_verification_command(publication))
            if verified.get("version") != published.get("version"):
                raise ValueError("published and exported Artifact Hub versions differ")
            pending_receipt.replace(RECEIPT)
            _update_profile(str(published["version"]), int(verified["files"]))
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


if __name__ == "__main__":
    main()
