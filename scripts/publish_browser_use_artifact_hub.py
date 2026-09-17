#!/usr/bin/env python3
"""Publish the current Browser Use report to an Artifact Site target."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from urllib.error import HTTPError

from harness.web_fetch.artifact_auth import load_token

ARCHIVE = Path("publish/browser-use-sites.zip")
ATTESTATION = Path("publish/browser-use-sites.attestation.json")
RECEIPT = Path("publish/browser-use-artifact-site-receipt.json")
PROFILE = Path("evaluation/browser-use/current-report.json")
BINDING_JOURNAL = Path("publish/browser-use-artifact-site-binding-pending.json")
TITLE = "Chromium 与 Moli 的 Browser Use 能力对比"
PUBLIC_REPORT_MARKER = "<title>首页 · Browser Use 评测</title>"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def recover_publication_binding() -> None:
    if not BINDING_JOURNAL.is_file():
        return
    pending = json.loads(BINDING_JOURNAL.read_text(encoding="utf-8"))
    if pending.get("schema") != "lex_browser_use_publication_binding/1":
        raise ValueError("invalid pending Browser Use publication binding")
    profile, attestation = pending.get("profile"), pending.get("attestation")
    if not isinstance(profile, dict) or not isinstance(attestation, dict):
        raise ValueError("invalid pending Browser Use publication binding payload")
    profile_payload = json_bytes(profile)
    if hashlib.sha256(profile_payload).hexdigest() != attestation.get("profile_sha256"):
        raise ValueError("pending Browser Use publication binding has an invalid profile hash")
    atomic_write(PROFILE, profile_payload)
    atomic_write(ATTESTATION, json_bytes(attestation))
    BINDING_JOURNAL.unlink()


def validate_package() -> None:
    recover_publication_binding()
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated Browser Use package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    report = Path(str(attestation.get("report") or ""))
    evidence_paths = [Path(str(path)) for path in attestation.get("evidence") or []]
    expected_evidence = attestation.get("evidence_files") or {}
    checks = {
        "schema": attestation.get("schema") == "lex_browser_use_publication_attestation/2",
        "archive": sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir() and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "profile": sha256(PROFILE) == attestation.get("profile_sha256"),
        "evidence": bool(evidence_paths)
        and set(expected_evidence) == {str(path) for path in evidence_paths}
        and all(
            path.is_dir()
            and all((path / name).is_file() and sha256(path / name) == digest for name, digest in expected_evidence[str(path)].items())
            for path in evidence_paths
        ),
    }
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError("publication package is stale; rebuild before publishing: " + ", ".join(failed))


def publication_target(*, base_override: str = "") -> tuple[dict, str, str, str]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    publication = profile.get("publication") or {}
    base = (base_override or str(publication.get("base_url") or "")).rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Browser Use report has no valid Artifact Site base URL")
    return profile, base, str(publication.get("site_slug") or ""), str(publication.get("expected_version") or "")


def request(base: str, path: str, data: bytes | None = None, content_type: str | None = None, *, method: str | None = None, authenticated: bool = True, idempotency_key: str = "") -> tuple[int, bytes]:
    headers = {"User-Agent": "Browser-Use-Artifact-Site-Publisher/1.0"}
    if authenticated:
        headers["Authorization"] = "Bearer " + load_token(base_url=base)
    if content_type:
        headers["Content-Type"] = content_type
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
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
    if not 200 <= status < 300:
        detail = raw.decode("utf-8", errors="replace").strip()[:500] or "empty response"
        raise RuntimeError(f"{operation} failed with HTTP {status}: {detail}")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{operation} returned HTTP {status} without a valid JSON response") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{operation} returned a non-object JSON response")
    return value


def validate_public_preview(status: int, body: str) -> None:
    if status != 200 or PUBLIC_REPORT_MARKER not in body:
        raise RuntimeError("public preview is unavailable or missing the report shell")


def multipart_archive() -> tuple[bytes, str]:
    # The same package and operation key must produce identical request bytes on retry.
    boundary = "----browseruse" + sha256(ARCHIVE)[:32]
    parts = []
    for key, value in (("mode", "zip"), ("title", TITLE)):
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="browser-use.zip"\r\nContent-Type: application/zip\r\n\r\n').encode() + ARCHIVE.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def current_version(value: dict) -> str:
    site = value.get("site") if isinstance(value.get("site"), dict) else value
    version = str(site.get("currentVersionId") or (site.get("version") or {}).get("id") or "")
    if not version.startswith("ver_"):
        raise RuntimeError("Artifact Site metadata omitted current version")
    return version


def public_share_path(base: str, share_url: str) -> str:
    target, authority = urllib.parse.urlsplit(share_url), urllib.parse.urlsplit(base)
    if target.scheme != authority.scheme or target.netloc != authority.netloc or not target.path.startswith("/v/") or target.query or target.fragment:
        raise ValueError("published Browser Use share URL is not a same-origin public capability URL")
    return target.path


def public_report_path(base: str, slug: str, share_url: str) -> str:
    token = public_share_path(base, share_url).removeprefix("/v/")
    if not token or "/" in token:
        raise ValueError("published Browser Use share URL has no valid token")
    return f"/api/preview/{urllib.parse.quote(slug, safe='')}?" + urllib.parse.urlencode({"share": token})


def archive_hashes(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {item.filename: hashlib.sha256(archive.read(item)).hexdigest() for item in archive.infolist() if not item.is_dir()}


def export_matches_package(base: str, slug: str) -> bool:
    status, raw = request(base, f"/api/sites/{urllib.parse.quote(slug)}/export")
    if status != 200:
        response_json(status, raw, operation="site export")
    with tempfile.NamedTemporaryFile(suffix=".zip") as stream:
        stream.write(raw)
        stream.flush()
        return archive_hashes(Path(stream.name)) == archive_hashes(ARCHIVE)


def verify_export(base: str, slug: str) -> None:
    if not export_matches_package(base, slug):
        raise RuntimeError("deployed Artifact Site file tree differs from package")


def bind_publication(profile: dict, *, base: str, slug: str, share_url: str, report_url: str, version: str) -> None:
    publication = profile.setdefault("publication", {})
    publication.update({"base_url": base, "site_slug": slug, "share_url": share_url, "report_url": report_url, "expected_version": version, "state": "verified"})
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    profile_payload = json_bytes(profile)
    attestation["profile_sha256"] = hashlib.sha256(profile_payload).hexdigest()
    atomic_write(BINDING_JOURNAL, json_bytes({"schema": "lex_browser_use_publication_binding/1", "profile": profile, "attestation": attestation}))
    recover_publication_binding()


def bind_published_version(version_id: str) -> None:
    profile, base, slug, _ = publication_target()
    publication = profile.setdefault("publication", {})
    bind_publication(profile, base=base, slug=slug, share_url=str(publication.get("share_url") or ""), report_url=str(publication.get("report_url") or ""), version=version_id)


def save_receipt(value: dict) -> None:
    safe = {key: item for key, item in value.items() if key not in {"editToken", "claimToken", "token"}}
    atomic_write(RECEIPT, json_bytes(safe))
    os.chmod(RECEIPT, 0o600)


def pending_created_site(base: str) -> str:
    if not RECEIPT.is_file():
        return ""
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("verified"):
        return ""
    if receipt.get("base_url") != base or receipt.get("archive_sha256") != sha256(ARCHIVE):
        raise ValueError("pending site receipt differs from current release package")
    return str(receipt.get("slug") or "")


def create_site(base: str, payload: bytes, content_type: str) -> str:
    """Resume an uncertain create with its persisted operation key."""
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8")) if RECEIPT.is_file() else {}
    if receipt and not receipt.get("verified"):
        existing_slug = pending_created_site(base)
        if existing_slug:
            return existing_slug
    if receipt.get("verified"):
        raise ValueError("Browser Use site is already published; refusing another create")
    key = str(receipt.get("idempotency_key") or "")
    if not key:
        if receipt:
            raise ValueError("pending site has no idempotency key; inspect it before another create")
        key = "browser-use-create-" + uuid.uuid4().hex
        receipt = {"base_url": base, "slug": "", "archive_sha256": sha256(ARCHIVE),
                   "idempotency_key": key, "verified": False}
        save_receipt(receipt)
    status, raw = request(base, f"/api/operations/{urllib.parse.quote(key)}")
    if status == 200:
        operation = response_json(status, raw, operation="site creation lookup")
        if operation.get("status") == "completed":
            result = operation.get("result") or {}
            slug = str(result.get("slug") or "")
            if not slug:
                raise RuntimeError("completed site creation omitted slug")
            receipt["slug"] = slug
            save_receipt(receipt)
            return slug
        if operation.get("status") == "running":
            raise RuntimeError("site creation is still running; retry this command later")
        if operation.get("status") != "retryable":
            raise RuntimeError("site creation has an unknown operation state")
    elif status not in (404,):
        response_json(status, raw, operation="site creation lookup")
    status, raw = request(base, "/api/sites", payload, content_type, method="POST", idempotency_key=key)
    slug = str(response_json(status, raw, operation="site creation").get("slug") or "")
    if not slug:
        raise RuntimeError("site creation response omitted slug")
    receipt["slug"] = slug
    save_receipt(receipt)
    return slug


def update_site(base: str, slug: str, expected_version: str, payload: bytes, content_type: str) -> None:
    """Keep one operation key across retries of a version upload."""
    digest = sha256(ARCHIVE)
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8")) if RECEIPT.is_file() else {}
    pending = receipt.get("pending_update")
    if pending:
        if pending.get("archive_sha256") != digest or pending.get("expected_version") != expected_version or receipt.get("base_url") != base or receipt.get("slug") != slug:
            raise ValueError("pending version upload differs from the current release package or target")
        key = str(pending.get("idempotency_key") or "")
        if not key:
            raise ValueError("pending version upload has no idempotency key")
    else:
        key = "browser-use-update-" + uuid.uuid4().hex
        receipt.update({"base_url": base, "slug": slug, "pending_update": {
            "archive_sha256": digest, "expected_version": expected_version, "idempotency_key": key,
        }})
        save_receipt(receipt)
    status, raw = request(base, f"/api/operations/{urllib.parse.quote(key)}")
    if status == 200:
        operation = response_json(status, raw, operation="site version lookup")
        if operation.get("status") == "completed":
            return
        if operation.get("status") == "running":
            raise RuntimeError("site version upload is still running; retry this command later")
        if operation.get("status") != "retryable":
            raise RuntimeError("site version upload has an unknown operation state")
    elif status != 404:
        response_json(status, raw, operation="site version lookup")
    query = urllib.parse.urlencode({"expected_version": expected_version})
    status, raw = request(base, f"/api/sites/{urllib.parse.quote(slug)}/versions?{query}", payload, content_type, idempotency_key=key)
    response_json(status, raw, operation="site version upload")


def main(*, create: bool = False, base_url: str = "") -> None:
    validate_package()
    profile, base, bound_slug, expected_version = publication_target(base_override=base_url)
    if base_url and not create:
        raise ValueError("a new Artifact Site base can only be used with --create")
    payload, content_type = multipart_archive()
    current_base = str((profile.get("publication") or {}).get("base_url") or "").rstrip("/")
    creating_new_target = create and base != current_base
    if bound_slug and not creating_new_target:
        if create:
            raise ValueError("Browser Use report is already bound; omit --create to update it")
        if not expected_version.startswith("ver_"):
            raise ValueError("Browser Use report has incomplete Artifact Site binding")
        slug, share_url = bound_slug, str(profile["publication"].get("share_url") or "")
        status, raw = request(base, f"/api/sites/{urllib.parse.quote(slug)}")
        remote_version = current_version(response_json(status, raw, operation="site lookup"))
        if remote_version != expected_version:
            verify_export(base, slug)
        elif not export_matches_package(base, slug):
            update_site(base, slug, expected_version, payload, content_type)
    else:
        slug = pending_created_site(base)
        if slug:
            verify_export(base, slug)
        elif not create:
            raise ValueError("no bound site or matching pending creation receipt")
        else:
            slug = create_site(base, payload, content_type)
        status, raw = request(base, f"/api/sites/{urllib.parse.quote(slug)}/shares", json.dumps({"policy": "public"}).encode(), "application/json", method="POST")
        share_url = str(response_json(status, raw, operation="public share creation").get("url") or "")
        if not share_url.startswith("https://"):
            raise RuntimeError("share creation response omitted public URL")
    status, raw = request(base, f"/api/sites/{urllib.parse.quote(slug)}")
    version = current_version(response_json(status, raw, operation="site lookup after upload"))
    verify_export(base, slug)
    public_status, public_body = request(base, public_share_path(base, share_url), authenticated=False)
    if public_status != 200:
        response_json(public_status, public_body, operation="public share verification")
    report_path = public_report_path(base, slug, share_url)
    preview_status, preview_body = request(base, report_path, authenticated=False)
    validate_public_preview(preview_status, preview_body.decode("utf-8", errors="replace"))
    bind_publication(profile, base=base, slug=slug, share_url=share_url, report_url=base + report_path, version=version)
    save_receipt({"base_url": base, "slug": slug, "share_url": share_url, "report_url": base + report_path, "version": version, "archive_sha256": sha256(ARCHIVE), "verified": True})
    print(json.dumps({"slug": slug, "url": base + report_path, "share_url": share_url, "version": version}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--base-url", default="", help="new Artifact Site base URL; requires --create")
    arguments = parser.parse_args()
    main(create=arguments.create, base_url=arguments.base_url)
