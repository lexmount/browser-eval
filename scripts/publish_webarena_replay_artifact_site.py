#!/usr/bin/env python3
"""Create or atomically update the current replay report on Artifact Site."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from urllib.error import HTTPError

from harness.web_fetch.artifact_auth import load_token

ARCHIVE = Path("publish/trajectory-replay-artifact-site.zip")
ATTESTATION = Path("publish/trajectory-replay-artifact-site.attestation.json")
RECEIPT = Path("publish/trajectory-replay-artifact-site-receipt.json")
PROFILE = Path("evaluation/trajectory-replay/webarena-verified/current-run.json")
DATASET = Path("evaluation/trajectory-replay/webarena-verified/trajectories.json")
REVIEW = Path("research/trajectory-replay-failure-review.json")
BINDING_JOURNAL = Path("publish/trajectory-replay-artifact-site-binding-pending.json")
OPERATION_JOURNAL = Path("publish/trajectory-replay-artifact-site-operation-pending.json")
PUBLIC_REPORT_MARKERS = (
    "<title>浏览器轨迹重放</title>",
    "<title>Browser Trajectory Replay</title>",
)


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


def recover_publication_binding() -> None:
    if not BINDING_JOURNAL.is_file():
        return
    pending = json.loads(BINDING_JOURNAL.read_text(encoding="utf-8"))
    if pending.get("schema") != "lex_webarena_replay_publication_binding/1":
        raise ValueError("invalid pending publication binding")
    profile = pending.get("profile")
    attestation = pending.get("attestation")
    if not isinstance(profile, dict) or not isinstance(attestation, dict):
        raise ValueError("invalid pending publication binding payload")
    profile_payload = json_bytes(profile)
    if hashlib.sha256(profile_payload).hexdigest() != attestation.get("profile_sha256"):
        raise ValueError("pending publication binding has an invalid profile hash")
    atomic_write(PROFILE, profile_payload)
    atomic_write(ATTESTATION, json_bytes(attestation))
    BINDING_JOURNAL.unlink()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


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
        with urllib.request.urlopen(req, timeout=240) as response:
            return response.status, response.read(), dict(response.headers.items())
    except HTTPError as error:
        try:
            return error.code, error.read(), dict(error.headers.items())
        finally:
            error.close()


def validate_package() -> dict:
    recover_publication_binding()
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated replay package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    report = Path(str(attestation.get("report") or ""))
    evidence = Path(str(attestation.get("evidence") or ""))
    checks = {
        "schema": attestation.get("schema") == "lex_webarena_replay_publication_attestation/1",
        "archive": sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir() and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "profile": sha256(PROFILE) == attestation.get("profile_sha256"),
        "dataset": sha256(DATASET) == attestation.get("dataset_sha256"),
        "review": sha256(REVIEW) == attestation.get("review_sha256"),
        "evidence": evidence.is_file() and sha256(evidence) == attestation.get("evidence_sha256"),
    }
    report_manifest = report / "report-manifest.json"
    if report_manifest.is_file():
        built = json.loads(report_manifest.read_text(encoding="utf-8"))
        built_closure_sha = built.get("diagnostic_closure_sha256")
    else:
        built_closure_sha = None
    source_closure_sha = attestation.get("diagnostic_closure_sha256")
    checks["diagnostic_closure_binding"] = built_closure_sha == source_closure_sha
    if source_closure_sha:
        closure = Path(str(attestation.get("diagnostic_closure") or ""))
        checks["diagnostic_closure"] = (
            closure.is_file()
            and not closure.is_symlink()
            and not any(parent.is_symlink() for parent in closure.parents)
            and sha256(closure) == source_closure_sha
        )
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError("publication package is stale: " + ", ".join(failed))
    return attestation


def multipart_archive(title: str) -> tuple[bytes, str]:
    identity = hashlib.sha256()
    identity.update(title.encode())
    identity.update(b"\0")
    identity.update(sha256(ARCHIVE).encode())
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
        + ARCHIVE.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def current_version(site: dict) -> str:
    nested = site.get("site") if isinstance(site.get("site"), dict) else site
    version = str(nested.get("currentVersionId") or (nested.get("version") or {}).get("id") or "")
    if not version.startswith("ver_"):
        raise RuntimeError("Artifact Site metadata omitted current version")
    return version


def public_share_context(base: str, share_url: str) -> tuple[str, str]:
    target = urllib.parse.urlsplit(share_url)
    authority = urllib.parse.urlsplit(base)
    if (
        target.scheme != authority.scheme
        or target.netloc != authority.netloc
        or not target.path.startswith("/v/")
        or target.query
        or target.fragment
    ):
        raise ValueError("current replay profile has no valid same-origin public share URL")
    token = target.path.removeprefix("/v/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("current replay profile has no valid public share token")
    return target.path, token


def public_share_path(base: str, share_url: str) -> str:
    return public_share_context(base, share_url)[0]


def operation_identity(
    *,
    base: str,
    title: str,
    kind: str,
    slug: str = "",
    expected_version: str = "",
) -> dict:
    return {
        "schema": "lex_webarena_replay_artifact_operation/1",
        "base_url": base,
        "archive_sha256": sha256(ARCHIVE),
        "title": title,
        "kind": kind,
        "slug": slug,
        "expected_version": expected_version,
    }


def pending_operation(identity: dict, *, allow_create: bool) -> tuple[dict, bool]:
    if OPERATION_JOURNAL.is_file():
        pending = json.loads(OPERATION_JOURNAL.read_text(encoding="utf-8"))
        key = pending.pop("key", None)
        if (
            pending != identity
            or not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key)
        ):
            raise ValueError("pending Artifact Site operation differs from this release")
        pending["key"] = key
        return pending, True
    if not allow_create:
        raise ValueError("no bound site or matching pending creation operation")
    pending = dict(identity)
    pending["key"] = "trajectory-replay-" + uuid.uuid4().hex
    atomic_write(OPERATION_JOURNAL, json_bytes(pending))
    return pending, False


def completed_operation(base: str, key: str) -> tuple[int, bytes] | None:
    status, raw, _ = request(base, f"/api/operations/{urllib.parse.quote(key)}")
    if status == 404:
        return None
    operation = response_json(status, raw, operation="operation recovery")
    state = operation.get("status")
    if state == "running":
        raise RuntimeError("Artifact Site operation is still running; retry this command later")
    if state == "retryable":
        return None
    if state != "completed":
        raise RuntimeError("Artifact Site operation returned an unknown recovery status")
    result = operation.get("result")
    http_status = operation.get("httpStatus")
    if not isinstance(result, dict) or not isinstance(http_status, int):
        raise RuntimeError("completed Artifact Site operation omitted its result")
    return http_status, json.dumps(result, ensure_ascii=False).encode()


def idempotent_upload(
    base: str,
    path: str,
    payload: bytes,
    content_type: str,
    identity: dict,
    *,
    allow_create: bool,
) -> tuple[int, bytes]:
    pending, resumed = pending_operation(identity, allow_create=allow_create)
    key = pending["key"]
    if resumed:
        recovered = completed_operation(base, key)
        if recovered is not None:
            return recovered
    status, raw, _ = request(
        base,
        path,
        payload,
        content_type,
        extra_headers={"Idempotency-Key": key},
    )
    return status, raw


def clear_pending_operation(identity: dict) -> None:
    if not OPERATION_JOURNAL.exists():
        return
    pending_operation(identity, allow_create=False)
    OPERATION_JOURNAL.unlink(missing_ok=True)


def preview_has_report_marker(body: bytes) -> bool:
    text = body.decode("utf-8", errors="replace")
    return any(marker in text for marker in PUBLIC_REPORT_MARKERS)


def archive_hashes(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            item.filename: hashlib.sha256(archive.read(item)).hexdigest()
            for item in archive.infolist()
            if not item.is_dir()
        }


def verify_export(base: str, slug: str) -> None:
    status, raw, _ = request(base, f"/api/sites/{urllib.parse.quote(slug)}/export")
    if status != 200:
        response_json(status, raw, operation="site export")
    with tempfile.NamedTemporaryFile(suffix=".zip") as stream:
        stream.write(raw)
        stream.flush()
        if archive_hashes(Path(stream.name)) != archive_hashes(ARCHIVE):
            raise RuntimeError("deployed Artifact Site file tree differs from package")


def bind_publication(profile: dict, *, slug: str, share_url: str, version: str) -> None:
    publication = profile.setdefault("publication", {})
    publication.update(
        {
            "site_slug": slug,
            "share_url": share_url,
            "expected_version": version,
            "state": "verified",
        }
    )
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    profile_payload = json_bytes(profile)
    attestation["profile_sha256"] = hashlib.sha256(profile_payload).hexdigest()
    pending = {
        "schema": "lex_webarena_replay_publication_binding/1",
        "profile": profile,
        "attestation": attestation,
    }
    atomic_write(BINDING_JOURNAL, json_bytes(pending))
    recover_publication_binding()


def save_receipt(value: dict) -> None:
    safe = {
        key: item for key, item in value.items() if key not in {"editToken", "claimToken", "token"}
    }
    atomic_write(RECEIPT, json_bytes(safe))
    os.chmod(RECEIPT, 0o600)


def pending_created_site(base: str) -> str:
    if not RECEIPT.is_file():
        return ""
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("verified"):
        return ""
    if (
        receipt.get("base_url") != base
        or receipt.get("archive_sha256") != sha256(ARCHIVE)
        or not isinstance(receipt.get("slug"), str)
        or not receipt["slug"]
    ):
        raise ValueError("pending site receipt differs from the current release package")
    return receipt["slug"]


def main(*, create: bool) -> None:
    validate_package()
    profile, base, bound_slug, expected_version = publication_target(require_bound=False)
    publication = profile["publication"]
    title = str(publication.get("title") or "Browser Trajectory Replay")
    payload, content_type = multipart_archive(title)
    if bound_slug or expected_version:
        if create:
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
        )
        status, raw, _ = request(base, f"/api/sites/{urllib.parse.quote(slug)}")
        before = response_json(status, raw, operation="site lookup")
        remote_version = current_version(before)
        if remote_version != expected_version:
            # A previous upload may have committed before a post-upload check
            # failed. Resume only when the exported tree proves that the
            # remote version is byte-identical to the current package.
            verify_export(base, slug)
        else:
            query = urllib.parse.urlencode({"expected_version": expected_version})
            status, raw = idempotent_upload(
                base,
                f"/api/sites/{urllib.parse.quote(slug)}/versions?{query}",
                payload,
                content_type,
                upload_identity,
                allow_create=True,
            )
            response_json(status, raw, operation="site version upload")
    else:
        upload_identity = operation_identity(base=base, title=title, kind="create")
        slug = pending_created_site(base)
        if slug:
            if OPERATION_JOURNAL.exists():
                pending_operation(upload_identity, allow_create=False)
            verify_export(base, slug)
        else:
            status, raw = idempotent_upload(
                base,
                "/api/sites",
                payload,
                content_type,
                upload_identity,
                allow_create=create,
            )
            created = response_json(status, raw, operation="site creation")
            slug = str(created.get("slug") or "")
            if not slug:
                raise RuntimeError("site creation response omitted slug")
            save_receipt(
                {
                    "base_url": base,
                    "slug": slug,
                    "archive_sha256": sha256(ARCHIVE),
                    "verified": False,
                }
            )
        share_status, share_raw, _ = request(
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
    status, raw, _ = request(base, f"/api/sites/{urllib.parse.quote(slug)}")
    metadata = response_json(status, raw, operation="site lookup after upload")
    version = current_version(metadata)
    verify_export(base, slug)
    share_path, share_token = public_share_context(base, share_url)
    public_status, public_body, _ = request(
        base,
        share_path,
        authenticated=False,
    )
    if public_status != 200:
        response_json(public_status, public_body, operation="public share verification")
    preview_status, preview_body, _ = request(
        base,
        f"/api/preview/{urllib.parse.quote(slug)}",
        authenticated=False,
        extra_headers={"X-Artifact-Share": share_token},
    )
    if preview_status != 200 or not preview_has_report_marker(preview_body):
        raise RuntimeError("deployed preview is unavailable or missing report marker")
    clear_pending_operation(upload_identity)
    bind_publication(profile, slug=slug, share_url=share_url, version=version)
    save_receipt(
        {
            "base_url": base,
            "slug": slug,
            "share_url": share_url,
            "version": version,
            "archive_sha256": sha256(ARCHIVE),
            "verified": True,
        }
    )
    print(
        json.dumps(
            {"slug": slug, "url": share_url, "version": version},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    main(create=parser.parse_args().create)
