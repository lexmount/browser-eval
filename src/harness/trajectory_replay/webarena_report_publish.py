#!/usr/bin/env python3
"""Create or atomically update the current replay report on Artifact Site."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.record_io import file_sha256
from harness.reporting.publication import (
    PublicationClient,
    PublicationSnapshot,
    atomic_write,
    bind_publication_files,
    recover_binding,
)
from harness.reporting.publication import multipart_archive as build_multipart_archive
from harness.reporting.publish import BoundTrack, publish_bound_release
from harness.reporting.visual import tree_sha256 as _tree_sha256
from harness.trajectory_replay.report_inputs import bound_dataset_path

ARCHIVE = Path("publish/trajectory-replay-artifact-site.zip")
ATTESTATION = Path("publish/trajectory-replay-artifact-site.attestation.json")
RECEIPT = Path("publish/trajectory-replay-artifact-site-receipt.json")
PROFILE: Path | None = None
REVIEW = Path("research/trajectory-replay-failure-review.json")
BINDING_JOURNAL = Path("publish/trajectory-replay-artifact-site-binding-pending.json")
OPERATION_JOURNAL = Path("publish/trajectory-replay-artifact-site-operation-pending.json")
PUBLIC_REPORT_MARKERS = (
    "<title>浏览器轨迹重放</title>",
    "<title>Browser Trajectory Replay</title>",
)
# A staged file PUT may need longer to finish on slower upstream connections.
CLIENT = PublicationClient(
    "Trajectory-Replay-Artifact-Site-Publisher/1.0",
    put_timeout=1_200,
    return_http_errors=True,
    authorization_first=False,
)


def recover_publication_binding() -> None:
    recover_binding(
        PROFILE,
        ATTESTATION,
        BINDING_JOURNAL,
        schema="lex_webarena_replay_publication_binding/2",
        legacy_schema="lex_webarena_replay_publication_binding/1",
        writer=atomic_write,
    )


def tree_sha256(root: Path) -> str:
    return _tree_sha256(root, framed_paths=False)


def request(
    base: str,
    path: str,
    data: bytes | None = None,
    content_type: str | None = None,
    *,
    method: str | None = None,
    authenticated: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    return CLIENT.request(
        base,
        path,
        data,
        content_type,
        method=method,
        authenticated=authenticated,
        extra_headers=extra_headers,
    )


def validate_package() -> dict:
    recover_publication_binding()
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated replay package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    report = Path(str(attestation.get("report") or ""))
    packaged_report = Path(str(attestation.get("packaged_report") or report))
    evidence = Path(str(attestation.get("evidence") or ""))
    if PROFILE is None:
        raise ValueError("Select an external report instance with --profile")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    dataset_path = bound_dataset_path(profile)
    if (profile.get("scoring") or {}).get("require_all_reference_validity") is True:
        from harness.trajectory_replay.webarena_report import require_all_reference_validity

        require_all_reference_validity(
            profile,
            json.loads(evidence.read_text(encoding="utf-8")),
            json.loads(dataset_path.read_text(encoding="utf-8")),
        )
    checks = {
        "schema": attestation.get("schema") == "lex_webarena_replay_publication_attestation/1",
        "archive": file_sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir() and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "packaged_report": packaged_report.is_dir()
        and tree_sha256(packaged_report)
        == attestation.get("packaged_report_tree_sha256", attestation.get("report_tree_sha256")),
        "profile": file_sha256(PROFILE) == attestation.get("profile_sha256"),
        "dataset": file_sha256(dataset_path) == attestation.get("dataset_sha256"),
        "review": file_sha256(REVIEW) == attestation.get("review_sha256"),
        "evidence": (
            evidence.is_file() and file_sha256(evidence) == attestation.get("evidence_sha256")
        ),
    }
    report_manifest = report / "report-manifest.json"
    built = (
        json.loads(report_manifest.read_text(encoding="utf-8")) if report_manifest.is_file() else {}
    )
    checks["report_dataset"] = built.get("dataset_sha256") == attestation.get("dataset_sha256")
    checks["report_evidence"] = built.get("results_sha256") == attestation.get("evidence_sha256")
    checks["no_diagnostic_closure"] = not any(
        value
        for value in (
            built.get("diagnostic_closure_sha256"),
            attestation.get("diagnostic_closure_sha256"),
            attestation.get("diagnostic_closure"),
        )
    )
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError("publication package is stale: " + ", ".join(failed))
    return attestation


def multipart_archive(title: str, *, archive: Path | None = None) -> tuple[bytes, str]:
    archive = archive if archive is not None else ARCHIVE
    return build_multipart_archive(
        archive,
        title,
        boundary_prefix="----trajectoryreplay",
        filename="browser-trajectory-replay.zip",
    )


def preview_has_report_marker(body: bytes) -> bool:
    text = body.decode("utf-8", errors="replace")
    return any(marker in text for marker in PUBLIC_REPORT_MARKERS)


def bind_publication(
    profile: dict,
    *,
    slug: str,
    share_url: str,
    version: str,
    release: PublicationSnapshot | None = None,
) -> None:
    if not version.startswith("ver_"):
        raise ValueError("publication response omitted a valid version id")
    if json.loads(PROFILE.read_bytes()) != profile:
        raise ValueError("publication profile changed before binding")
    bind_publication_files(
        PROFILE,
        ATTESTATION,
        BINDING_JOURNAL,
        publication={
            "site_slug": slug,
            "share_url": share_url,
            "expected_version": version,
            "state": "verified",
        },
        schema="lex_webarena_replay_publication_binding/2",
        expected=release.sources if release is not None else None,
        writer=atomic_write,
    )


def check_preview(status: int, body: bytes) -> None:
    if status != 200 or not preview_has_report_marker(body):
        raise RuntimeError("deployed preview is unavailable or missing report marker")


def main(*, create: bool) -> None:
    if PROFILE is None:
        raise ValueError("Select an external report instance with --profile")
    publish_bound_release(
        BoundTrack(
            label="replay",
            default_title="Browser Trajectory Replay",
            archive=ARCHIVE,
            profile=PROFILE,
            receipt=RECEIPT,
            operation_journal=OPERATION_JOURNAL,
            inputs=(ATTESTATION, PROFILE, REVIEW),
            operation_prefix="trajectory-replay-",
            operation_schema="lex_webarena_replay_artifact_operation/1",
            version_field="version",
            request=request,
            public_request=lambda base, path, **kwargs: request(
                base, path, authenticated=False, **kwargs
            ),
            multipart=lambda title, archive: multipart_archive(title, archive=archive),
            validate_package=validate_package,
            check_preview=check_preview,
            recover_binding=recover_publication_binding,
            bind=bind_publication,
            staged_key_prefix="trajectory-staged-",
        ),
        create=create,
    )


def cli() -> None:
    global ARCHIVE, ATTESTATION, RECEIPT, PROFILE, REVIEW, BINDING_JOURNAL, OPERATION_JOURNAL
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--review", type=Path, default=REVIEW)
    parser.add_argument("--binding-journal", type=Path, default=BINDING_JOURNAL)
    parser.add_argument("--operation-journal", type=Path, default=OPERATION_JOURNAL)
    args = parser.parse_args()
    ARCHIVE = args.archive
    ATTESTATION = args.attestation
    RECEIPT = args.receipt
    PROFILE = args.profile
    REVIEW = args.review
    BINDING_JOURNAL = args.binding_journal
    OPERATION_JOURNAL = args.operation_journal
    main(create=args.create)
