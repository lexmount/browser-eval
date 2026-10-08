#!/usr/bin/env python3
"""Create or atomically update the current replay report on Artifact Site."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from harness.reporting.publication import (
    PublicationOperation,
    PublicationSnapshot,
    archive_inventory,
    atomic_write,
    bind_publication_files,
    binding_receipt_matches,
    json_bytes,
    public_share_context,
    recover_binding,
    snapshot_publication,
)
from harness.reporting.visual import tree_sha256 as _tree_sha256
from harness.trajectory_replay.report_inputs import bound_dataset_path
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.publication import upload_large_archive

ARCHIVE = Path("publish/trajectory-replay-artifact-site.zip")
ATTESTATION = Path("publish/trajectory-replay-artifact-site.attestation.json")
RECEIPT = Path("publish/trajectory-replay-artifact-site-receipt.json")
PROFILE = Path("evaluation/trajectory-replay/webarena-verified/current-run.json")
REVIEW = Path("research/trajectory-replay-failure-review.json")
BINDING_JOURNAL = Path("publish/trajectory-replay-artifact-site-binding-pending.json")
OPERATION_JOURNAL = Path("publish/trajectory-replay-artifact-site-operation-pending.json")
PUBLIC_REPORT_MARKERS = (
    "<title>浏览器轨迹重放</title>",
    "<title>Browser Trajectory Replay</title>",
)
STAGED_UPLOAD_THRESHOLD = 23 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def response_json(status: int, raw: bytes, *, operation: str) -> dict:
    if not 200 <= status < 300:
        detail = raw.decode("utf-8", errors="replace").strip()[:500] or "empty response"
        raise RuntimeError(f"{operation} failed with HTTP {status}: {detail}")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{operation} returned HTTP {status} without valid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} returned non-object JSON")
    return value


def publication_target(*, require_bound: bool) -> tuple[dict, str, str, str]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    publication = profile.get("publication") or {}
    base = str(publication.get("base_url") or "").rstrip("/")
    slug = str(publication.get("site_slug") or "")
    version = str(publication.get("expected_version") or "")
    if not base.startswith("https://"):
        raise ValueError("current replay profile has no valid Artifact Site base URL")
    if require_bound and (not slug or not version.startswith("ver_")):
        raise ValueError("current replay profile has no bound Artifact Site target")
    return profile, base, slug, version


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
    headers = {"User-Agent": "Trajectory-Replay-Artifact-Site-Publisher/1.0"}
    if authenticated:
        headers["Authorization"] = "Bearer " + load_token(base_url=base)
    if content_type:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=request_timeout(method)) as response:
            return response.status, response.read(), dict(response.headers.items())
    except HTTPError as error:
        try:
            return error.code, error.read(), dict(error.headers.items())
        finally:
            error.close()


def request_timeout(method: str | None) -> int:
    """Allow a staged file PUT to finish on slower upstream connections."""
    return 1_200 if method == "PUT" else 240


def validate_package() -> dict:
    recover_publication_binding()
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated replay package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    report = Path(str(attestation.get("report") or ""))
    packaged_report = Path(str(attestation.get("packaged_report") or report))
    evidence = Path(str(attestation.get("evidence") or ""))
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
        "archive": sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir() and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "packaged_report": packaged_report.is_dir()
        and tree_sha256(packaged_report)
        == attestation.get("packaged_report_tree_sha256", attestation.get("report_tree_sha256")),
        "profile": sha256(PROFILE) == attestation.get("profile_sha256"),
        "dataset": sha256(dataset_path) == attestation.get("dataset_sha256"),
        "review": sha256(REVIEW) == attestation.get("review_sha256"),
        "evidence": evidence.is_file() and sha256(evidence) == attestation.get("evidence_sha256"),
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
    identity = hashlib.sha256()
    identity.update(title.encode())
    identity.update(b"\0")
    identity.update(sha256(archive).encode())
    boundary = "----trajectoryreplay" + identity.hexdigest()[:32]
    parts = []
    for key, value in (("mode", "zip"), ("title", title)):
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="browser-trajectory-replay.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode()
        + archive.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def staged_archive_upload(
    base: str,
    *,
    title: str,
    slug: str | None,
    expected_version: str | None,
    identity: dict,
    release: PublicationSnapshot | None = None,
) -> bytes:
    """Upload a large archive as validated files and activate it in one commit."""

    def staged_request(path, data=None, content_type=None, *, method=None):
        if release is not None:
            release.check()
        headers = None
        if (
            data is not None
            and method is None
            and (path.startswith("/api/uploads?") or "/commit" in path)
        ):
            seed = json.dumps(identity, sort_keys=True, separators=(",", ":")) + "|" + path
            headers = {
                "Idempotency-Key": "trajectory-staged-" + hashlib.sha256(seed.encode()).hexdigest()
            }
        status, raw, response_headers = request(
            base,
            path,
            data,
            content_type,
            method=method,
            extra_headers=headers,
        )
        if not 200 <= status < 300:
            raise HTTPError(
                base + path,
                status,
                raw.decode("utf-8", errors="replace")[:500],
                response_headers,
                BytesIO(raw),
            )
        return status, raw

    return upload_large_archive(
        staged_request,
        release.archive if release is not None else ARCHIVE,
        slug,
        expected_version,
        title=title,
    )


def current_version(site: dict) -> str:
    nested = site.get("site") if isinstance(site.get("site"), dict) else site
    version = str(nested.get("currentVersionId") or (nested.get("version") or {}).get("id") or "")
    if not version.startswith("ver_"):
        raise RuntimeError("Artifact Site metadata omitted current version")
    return version


def operation_identity(
    *,
    base: str,
    title: str,
    kind: str,
    slug: str = "",
    expected_version: str = "",
    archive: Path | None = None,
) -> dict:
    return {
        "schema": "lex_webarena_replay_artifact_operation/1",
        "base_url": base,
        "archive_sha256": sha256(archive if archive is not None else ARCHIVE),
        "title": title,
        "kind": kind,
        "slug": slug,
        "expected_version": expected_version,
    }


def pending_operation(identity: dict, *, allow_create: bool) -> tuple[dict, bool]:
    return PublicationOperation(OPERATION_JOURNAL, "trajectory-replay-").pending(
        identity, allow_create=allow_create
    )


def completed_operation(base: str, key: str) -> tuple[int, bytes] | None:
    def send(path, *args, **kwargs):
        status, raw, _ = request(base, path, *args, **kwargs)
        return status, raw

    return PublicationOperation(OPERATION_JOURNAL, "trajectory-replay-").completed(
        key, request=send, decode=response_json
    )


def idempotent_upload(
    base: str,
    path: str,
    payload: bytes,
    content_type: str,
    identity: dict,
    *,
    allow_create: bool,
    release: PublicationSnapshot | None = None,
) -> tuple[int, bytes]:
    def send(path, *args, **kwargs):
        status, raw, _ = request(base, path, *args, **kwargs)
        return status, raw

    return PublicationOperation(OPERATION_JOURNAL, "trajectory-replay-").upload(
        path,
        payload,
        content_type,
        identity,
        allow_create=allow_create,
        request=send,
        decode=response_json,
        guard=release.check if release else lambda: None,
    )


def clear_pending_operation(identity: dict) -> None:
    PublicationOperation(OPERATION_JOURNAL, "trajectory-replay-").clear(identity)


def preview_has_report_marker(body: bytes) -> bool:
    text = body.decode("utf-8", errors="replace")
    return any(marker in text for marker in PUBLIC_REPORT_MARKERS)


def archive_hashes(path: Path) -> dict[str, str]:
    return archive_inventory(path)


def verify_export(base: str, slug: str, *, archive: Path | None = None) -> None:
    status, raw, _ = request(base, f"/api/sites/{urllib.parse.quote(slug)}/export")
    if status != 200:
        response_json(status, raw, operation="site export")
    with tempfile.NamedTemporaryFile(suffix=".zip") as stream:
        stream.write(raw)
        stream.flush()
        selected = archive if archive is not None else ARCHIVE
        if archive_hashes(Path(stream.name)) != archive_hashes(selected):
            raise RuntimeError("deployed Artifact Site file tree differs from package")


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


def save_receipt(value: dict) -> None:
    safe = {
        key: item for key, item in value.items() if key not in {"editToken", "claimToken", "token"}
    }
    atomic_write(RECEIPT, json_bytes(safe))
    os.chmod(RECEIPT, 0o600)


def pending_created_site(base: str, *, archive: Path | None = None) -> str:
    if not RECEIPT.is_file():
        return ""
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("verified"):
        return ""
    if (
        receipt.get("base_url") != base
        or receipt.get("archive_sha256") != sha256(archive if archive is not None else ARCHIVE)
        or not isinstance(receipt.get("slug"), str)
        or not receipt["slug"]
    ):
        raise ValueError("pending site receipt differs from the current release package")
    return receipt["slug"]


def main(*, create: bool) -> None:
    recover_publication_binding()
    with snapshot_publication(ARCHIVE, inputs=[ATTESTATION, PROFILE, REVIEW]) as release:
        validate_package()
        release.check()
        _publish(create=create, release=release)


def _publish(*, create: bool, release: PublicationSnapshot) -> None:
    def send(*args, **kwargs):
        release.check()
        return request(*args, **kwargs)

    profile, base, bound_slug, expected_version = publication_target(require_bound=False)
    publication = profile["publication"]
    title = str(publication.get("title") or "Browser Trajectory Replay")
    large_archive = release.archive.stat().st_size > STAGED_UPLOAD_THRESHOLD
    payload = content_type = None
    if not large_archive:
        payload, content_type = multipart_archive(title, archive=release.archive)
    resume_binding = binding_receipt_matches(
        RECEIPT, base=base, archive_sha256=release.sha256,
        slug=bound_slug, version=expected_version,
    )
    if bound_slug or expected_version:
        if create and not resume_binding:
            raise ValueError("replay report is already bound; update it without --create")
        if not bound_slug or not expected_version.startswith("ver_"):
            raise ValueError("current replay profile has no complete Artifact Site binding")
        slug = bound_slug
        share_url = str(publication.get("share_url") or "")
        upload_identity = operation_identity(
            base=base,
            title=title,
            kind="version",
            slug=slug,
            expected_version=expected_version,
            archive=release.archive,
        )
        status, raw, _ = send(base, f"/api/sites/{urllib.parse.quote(slug)}")
        before = response_json(status, raw, operation="site lookup")
        remote_version = current_version(before)
        if remote_version != expected_version or resume_binding:
            # A previous upload may have committed before a post-upload check
            # failed. Resume only when the exported tree proves that the
            # remote version is byte-identical to the current package.
            verify_export(base, slug, archive=release.archive)
        else:
            if large_archive:
                raw = staged_archive_upload(
                    base,
                    title=title,
                    slug=slug,
                    expected_version=expected_version,
                    identity=upload_identity,
                    release=release,
                )
                status = 200
            else:
                query = urllib.parse.urlencode({"expected_version": expected_version})
                status, raw = idempotent_upload(
                    base,
                    f"/api/sites/{urllib.parse.quote(slug)}/versions?{query}",
                    payload,
                    content_type,
                    upload_identity,
                    allow_create=True,
                    release=release,
                )
            response_json(status, raw, operation="site version upload")
    else:
        upload_identity = operation_identity(
            base=base, title=title, kind="create", archive=release.archive
        )
        slug = pending_created_site(base, archive=release.archive)
        if slug:
            if OPERATION_JOURNAL.exists():
                pending_operation(upload_identity, allow_create=False)
            verify_export(base, slug, archive=release.archive)
        else:
            if large_archive:
                raw = staged_archive_upload(
                    base,
                    title=title,
                    slug=None,
                    expected_version=None,
                    identity=upload_identity,
                    release=release,
                )
                status = 200
            else:
                status, raw = idempotent_upload(
                    base,
                    "/api/sites",
                    payload,
                    content_type,
                    upload_identity,
                    allow_create=create,
                    release=release,
                )
            created = response_json(status, raw, operation="site creation")
            slug = str(created.get("slug") or "")
            if not slug:
                raise RuntimeError("site creation response omitted slug")
            save_receipt(
                {
                    "base_url": base,
                    "slug": slug,
                    "archive_sha256": release.sha256,
                    "verified": False,
                }
            )
        share_status, share_raw, _ = send(
            base,
            f"/api/sites/{urllib.parse.quote(slug)}/shares",
            json.dumps({"policy": "public"}).encode(),
            "application/json",
            method="POST",
        )
        share = response_json(share_status, share_raw, operation="public share creation")
        share_url = str(share.get("url") or "")
        if not share_url.startswith("https://"):
            raise RuntimeError("share creation response omitted public URL")
    status, raw, _ = send(base, f"/api/sites/{urllib.parse.quote(slug)}")
    metadata = response_json(status, raw, operation="site lookup after upload")
    version = current_version(metadata)
    verify_export(base, slug, archive=release.archive)
    share_path, share_token = public_share_context(base, share_url)
    public_status, public_body, _ = send(
        base,
        share_path,
        authenticated=False,
    )
    if public_status != 200:
        response_json(public_status, public_body, operation="public share verification")
    preview_status, preview_body, _ = send(
        base,
        f"/api/preview/{urllib.parse.quote(slug)}",
        authenticated=False,
        extra_headers={"X-Artifact-Share": share_token},
    )
    if preview_status != 200 or not preview_has_report_marker(preview_body):
        raise RuntimeError("deployed preview is unavailable or missing report marker")
    release.check()
    validate_package()
    release.check()
    status, raw, _ = send(base, f"/api/sites/{urllib.parse.quote(slug)}")
    current = response_json(status, raw, operation="final site lookup")
    if current_version(current) != version:
        raise ValueError("remote version changed before publication binding")
    save_receipt({"base_url": base, "slug": slug, "share_url": share_url,
                  "version": version, "archive_sha256": release.sha256,
                  "verified": False, "phase": "binding"})
    clear_pending_operation(upload_identity)
    bind_publication(profile, slug=slug, share_url=share_url, version=version, release=release)
    save_receipt(
        {
            "base_url": base,
            "slug": slug,
            "share_url": share_url,
            "version": version,
            "archive_sha256": release.sha256,
            "verified": True,
        }
    )
    print(
        json.dumps(
            {"slug": slug, "url": share_url, "version": version},
            ensure_ascii=False,
        )
    )


def cli() -> None:
    global ARCHIVE, ATTESTATION, RECEIPT, PROFILE, REVIEW, BINDING_JOURNAL, OPERATION_JOURNAL
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--profile", type=Path, default=PROFILE)
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


if __name__ == "__main__":
    cli()
