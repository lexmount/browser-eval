#!/usr/bin/env python3
"""Create or atomically update the browser-use report on Artifact Hub."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
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
from harness.web_fetch.artifact_auth import load_token
from harness.web_fetch.safety import reject_evidence_symlinks

ARCHIVE = Path("publish/browser-use-sites.zip")
ATTESTATION = Path("publish/browser-use-sites.attestation.json")
RECEIPT = Path("publish/browser-use-artifact-hub-receipt.json")
PROFILE: Path | None = None
OPERATION_JOURNAL = Path("publish/browser-use-artifact-site-operation-pending.json")
BINDING_JOURNAL = Path("publish/browser-use-artifact-site-binding-pending.json")
PUBLIC_REPORT_MARKER = "<title>首页 · Browser Use 评测</title>"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    return _tree_sha256(root, framed_paths=True)


def validate_package() -> None:
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated Browser Use package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    if attestation.get("schema") != "lex_browser_use_publication_attestation/2":
        raise ValueError("publication attestation is not the current multi-evidence schema")
    report = Path(str(attestation.get("report") or ""))
    evidence_paths = [Path(str(path)) for path in attestation.get("evidence") or []]
    expected_evidence = attestation.get("evidence_files") or {}
    required = {"manifest.json", "run-config.json", "attempts.jsonl", "result-review.json"}
    for path in evidence_paths:
        reject_evidence_symlinks(path)
    checks = {
        "archive": sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir() and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "profile": sha256(PROFILE) == attestation.get("profile_sha256"),
        "evidence": bool(evidence_paths)
        and set(expected_evidence) == {str(path) for path in evidence_paths}
        and all(
            path.is_dir()
            and set(expected_evidence[str(path)]) == required
            and all(
                (path / name).is_file() and sha256(path / name) == digest
                for name, digest in expected_evidence[str(path)].items()
            )
            for path in evidence_paths
        ),
    }
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError(
            "publication package is stale; rebuild before publishing: " + ", ".join(failed)
        )


def publication_target(*, require_existing: bool = True) -> tuple[str, str, str]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    publication = profile.get("publication") or {}
    base = str(publication.get("base_url") or "").rstrip("/")
    slug = str(publication.get("site_slug") or "")
    version = str(publication.get("expected_version") or "")
    if not base.startswith("https://"):
        raise ValueError("current Browser Use report has no valid publication base")
    if require_existing and (not slug or not version.startswith("ver_")):
        raise ValueError("current Browser Use report has no valid publication target")
    return base, slug, version


def request(
    path: str,
    data: bytes | None = None,
    content_type: str | None = None,
    *,
    method: str | None = None,
    base_url: str | None = None,
    extra_headers: dict | None = None,
):
    base = base_url or publication_target(require_existing=False)[0]
    headers = {
        "Authorization": "Bearer " + load_token(base_url=base),
        "User-Agent": "Browser-Use-Report-Publisher/1.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    headers.update(extra_headers or {})
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=240) as response:
            return response.status, response.read()
    except HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


def response_json(status: int, raw: bytes, *, operation: str) -> dict:
    """Decode an Artifact Hub response without hiding its HTTP failure."""

    if not 200 <= status < 300:
        detail = raw.decode("utf-8", errors="replace").strip()[:500] or "empty response"
        raise RuntimeError(f"{operation} failed with HTTP {status}: {detail}")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            f"{operation} returned HTTP {status} without a valid JSON response"
        ) from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} returned a non-object JSON response")
    return value


def validate_public_preview(status: int, body: str) -> None:
    """Verify the stable report shell instead of a revision-specific headline."""

    if status != 200 or PUBLIC_REPORT_MARKER not in body:
        raise RuntimeError("public preview is unavailable or missing the report shell")


def multipart_archive(
    *, archive: Path | None = None, title: str = "Browser Use 评测"
) -> tuple[bytes, str]:
    archive = archive if archive is not None else ARCHIVE
    if not archive.is_file():
        raise ValueError(f"missing validated package: {ARCHIVE}")
    seed = title.encode() + b"\0" + sha256(archive).encode()
    boundary = "----browseruse" + hashlib.sha256(seed).hexdigest()[:32]
    parts = []
    for key, value in (
        ("mode", "zip"),
        ("title", title),
    ):
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="browser-use.zip"\r\nContent-Type: application/zip\r\n\r\n'
        ).encode()
        + archive.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def save_receipt(result: dict) -> None:
    safe = {
        key: value
        for key, value in result.items()
        if key not in {"editToken", "claimToken", "token"}
    }
    atomic_write(RECEIPT, json_bytes(safe))
    os.chmod(RECEIPT, 0o600)


def recover_publication_binding() -> None:
    recover_binding(
        PROFILE,
        ATTESTATION,
        BINDING_JOURNAL,
        schema="lex_browser_use_publication_binding/1",
        writer=atomic_write,
    )


def bind_published_version(
    version_id: str,
    *,
    slug: str | None = None,
    share_url: str | None = None,
    release: PublicationSnapshot | None = None,
) -> None:
    if not version_id.startswith("ver_"):
        raise ValueError("site update response omitted a valid version id")
    publication = {"expected_version": version_id}
    if slug is not None:
        publication["site_slug"] = slug
    if share_url is not None:
        publication["share_url"] = share_url
    bind_publication_files(
        PROFILE,
        ATTESTATION,
        BINDING_JOURNAL,
        publication=publication,
        schema="lex_browser_use_publication_binding/1",
        expected=release.sources if release is not None else None,
        writer=atomic_write,
    )


def main(*, create: bool = False) -> None:
    recover_publication_binding()
    with snapshot_publication(ARCHIVE, inputs=[ATTESTATION, PROFILE]) as release:
        validate_package()
        release.check()
        _publish(create=create, release=release)


def _publish(*, create: bool, release: PublicationSnapshot) -> None:
    def send(*args, **kwargs):
        release.check()
        return request(*args, base_url=base, **kwargs)

    title = str(
        json.loads(PROFILE.read_text()).get("publication", {}).get("title") or "Browser Use 评测"
    )
    payload, content_type = multipart_archive(archive=release.archive, title=title)
    base, expected_slug, expected_version = publication_target(require_existing=False)
    operations = PublicationOperation(OPERATION_JOURNAL, "browser-use-")
    identity = {
        "schema": "lex_browser_use_artifact_operation/1",
        "base_url": base,
        "archive_sha256": release.sha256,
        "title": title,
        "kind": "version" if expected_slug or expected_version else "create",
        "slug": expected_slug,
        "expected_version": expected_version,
    }
    resume_binding = binding_receipt_matches(
        RECEIPT, base=base, archive_sha256=release.sha256,
        slug=expected_slug, version=expected_version, version_field="versionId",
    )
    if expected_slug or expected_version:
        if create and not resume_binding:
            raise ValueError("Browser Use report is already bound; update it without --create")
        if not expected_slug or not expected_version.startswith("ver_"):
            raise ValueError("current Browser Use profile has no complete publication binding")
        if not RECEIPT.exists():
            raise ValueError("bound Browser Use site receipt is missing")
        result = json.loads(RECEIPT.read_bytes())
        if result.get("slug") != expected_slug:
            raise ValueError("site receipt differs from the bound Browser Use site")
        status, raw = send(f"/api/sites/{expected_slug}")
        current = response_json(status, raw, operation="site lookup")
        remote_version = str((current.get("site") or current).get("currentVersionId") or "")
        if not remote_version.startswith("ver_"):
            raise RuntimeError("could not resolve the current Artifact Site version")
        if remote_version != expected_version or resume_binding:
            status, exported = send(f"/api/sites/{expected_slug}/export")
            if status != 200:
                response_json(status, exported, operation="site export")
            if archive_inventory(exported) != archive_inventory(release.archive):
                raise ValueError("remote version differs from the bound release package")
            result["versionId"] = remote_version
        else:
            query = urllib.parse.urlencode({"expected_version": expected_version})
            status, raw = operations.upload(
                f"/api/sites/{expected_slug}/versions?{query}",
                payload,
                content_type,
                identity,
                allow_create=True,
                request=send,
                decode=response_json,
                guard=release.check,
            )
            result.update(response_json(status, raw, operation="site version upload"))
    else:
        result = json.loads(RECEIPT.read_bytes()) if RECEIPT.exists() else {}
        pending_site = result.get("slug") if result.get("verified") is False else None
        if pending_site:
            if result.get("base_url") != base or result.get("archive_sha256") != release.sha256:
                raise ValueError("pending site receipt differs from the current release package")
            if OPERATION_JOURNAL.exists():
                operations.pending(identity, allow_create=False)
            status, exported = send(f"/api/sites/{pending_site}/export")
            if status != 200:
                response_json(status, exported, operation="site export")
            if archive_inventory(exported) != archive_inventory(release.archive):
                raise ValueError("pending site differs from the bound release package")
        else:
            status, raw = operations.upload(
                "/api/sites",
                payload,
                content_type,
                identity,
                allow_create=create,
                request=send,
                decode=response_json,
                guard=release.check,
            )
            result = response_json(status, raw, operation="site creation")
            if not result.get("slug"):
                raise RuntimeError("site creation response omitted slug")
            result.update({"base_url": base, "archive_sha256": release.sha256, "verified": False})
            save_receipt(result)
        status, raw = send(f"/api/sites/{result['slug']}")
        current = response_json(status, raw, operation="created site lookup")
        version = str((current.get("site") or current).get("currentVersionId") or "")
        if not version.startswith("ver_"):
            raise RuntimeError("created site metadata omitted current version")
        result["versionId"] = version
    title_status, title_raw = send(
        f"/api/sites/{result['slug']}",
        json.dumps({"title": title}).encode(),
        "application/json",
        method="PATCH",
    )
    if not 200 <= title_status < 300:
        response_json(title_status, title_raw, operation="site title update")
    result["title"] = title
    if not result.get("share_url"):
        status, raw = send(
            f"/api/sites/{result['slug']}/shares",
            json.dumps({"policy": "public"}).encode(),
            "application/json",
        )
        share = response_json(status, raw, operation="public share creation")
        result["share_url"] = share["url"]
    version_id = str(
        result.get("versionId") or result.get("version_id") or result.get("currentVersionId") or ""
    )
    status, exported = send(f"/api/sites/{result['slug']}/export")
    if status != 200:
        response_json(status, exported, operation="site export")
    if archive_inventory(exported) != archive_inventory(release.archive):
        raise RuntimeError("deployed Artifact Site file tree differs from package")
    share_path, share_token = public_share_context(base, result["share_url"])
    public = urllib.request.Request(
        base + share_path, headers={"User-Agent": "Browser-Use-Report-Publisher/1.0"}
    )
    with urllib.request.urlopen(public, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"public report returned {response.status}")
    preview = urllib.request.Request(
        f"{base}/api/preview/{result['slug']}",
        headers={"User-Agent": "Browser-Use-Report-Publisher/1.0", "X-Artifact-Share": share_token},
    )
    with urllib.request.urlopen(preview, timeout=60) as response:
        body = response.read().decode("utf-8", errors="replace")
        validate_public_preview(response.status, body)
    release.check()
    validate_package()
    release.check()
    status, raw = send(f"/api/sites/{result['slug']}")
    current = response_json(status, raw, operation="final site lookup")
    if str((current.get("site") or current).get("currentVersionId") or "") != version_id:
        raise ValueError("remote version changed before publication binding")
    save_receipt({**result, "base_url": base, "archive_sha256": release.sha256,
                  "versionId": version_id, "verified": False, "phase": "binding"})
    operations.clear(identity)
    bind_published_version(
        version_id, slug=result["slug"], share_url=result["share_url"], release=release
    )
    result.update({"verified": True, "base_url": base, "archive_sha256": release.sha256})
    save_receipt(result)
    print(
        json.dumps(
            {
                "slug": result["slug"],
                "url": result["share_url"],
                "version": version_id,
            },
            ensure_ascii=False,
        )
    )


def cli() -> None:
    global ARCHIVE, ATTESTATION, PROFILE, RECEIPT, BINDING_JOURNAL, OPERATION_JOURNAL
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--create",
        action="store_true",
        help="explicitly create a new owned site when the bound site cannot be updated",
    )
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--attestation", type=Path, default=ATTESTATION)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--binding-journal", type=Path, default=BINDING_JOURNAL)
    parser.add_argument("--operation-journal", type=Path, default=OPERATION_JOURNAL)
    args = parser.parse_args()
    OPERATION_JOURNAL = args.operation_journal
    BINDING_JOURNAL = args.binding_journal
    ARCHIVE, ATTESTATION, PROFILE, RECEIPT = (
        args.archive,
        args.attestation,
        args.profile,
        args.receipt,
    )
    main(create=args.create)


if __name__ == "__main__":
    cli()
