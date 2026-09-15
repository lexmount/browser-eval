#!/usr/bin/env python3
"""Create or atomically update the browser-use report on Artifact Hub."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from urllib.error import HTTPError

from harness.web_fetch.artifact_auth import load_token

ARCHIVE = Path("publish/browser-use-sites.zip")
ATTESTATION = Path("publish/browser-use-sites.attestation.json")
RECEIPT = Path("publish/browser-use-artifact-hub-receipt.json")
PROFILE = Path("evaluation/browser-use/current-report.json")
PUBLIC_REPORT_MARKER = "<title>首页 · Browser Use 评测</title>"


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


def validate_package() -> None:
    if not ARCHIVE.is_file() or not ATTESTATION.is_file():
        raise ValueError("missing validated Browser Use package or attestation")
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    if attestation.get("schema") != "lex_browser_use_publication_attestation/2":
        raise ValueError("publication attestation is not the current multi-evidence schema")
    report = Path(str(attestation.get("report") or ""))
    evidence_paths = [Path(str(path)) for path in attestation.get("evidence") or []]
    expected_evidence = attestation.get("evidence_files") or {}
    checks = {
        "archive": sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir()
        and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "profile": sha256(PROFILE) == attestation.get("profile_sha256"),
        "evidence": bool(evidence_paths)
        and set(expected_evidence) == {str(path) for path in evidence_paths}
        and all(
            path.is_dir()
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
            "publication package is stale; rebuild before publishing: "
            + ", ".join(failed)
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
):
    base, _, _ = publication_target(require_existing=False)
    headers = {
        "Authorization": "Bearer " + load_token(base_url=base),
        "User-Agent": "Browser-Use-Report-Publisher/1.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=240) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


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


def multipart_archive() -> tuple[bytes, str]:
    if not ARCHIVE.is_file():
        raise ValueError(f"missing validated package: {ARCHIVE}")
    boundary = "----browseruse" + uuid.uuid4().hex
    parts = []
    for key, value in (
        ("mode", "zip"),
        ("title", "Chromium 与 Moli 的 Browser Use 能力对比"),
    ):
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"'
                f"\r\n\r\n{value}\r\n"
            ).encode()
        )
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="browser-use.zip"\r\nContent-Type: application/zip\r\n\r\n'
        ).encode()
        + ARCHIVE.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def save_receipt(result: dict) -> None:
    safe = {key: value for key, value in result.items() if key not in {"editToken", "claimToken"}}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(RECEIPT, 0o600)


def bind_published_version(version_id: str) -> None:
    """Advance the publication authority and keep its package attestation current."""

    if not version_id.startswith("ver_"):
        raise ValueError("site update response omitted a valid version id")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    publication = profile.setdefault("publication", {})
    publication["expected_version"] = version_id
    PROFILE.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    attestation["profile_sha256"] = sha256(PROFILE)
    ATTESTATION.write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(*, create: bool = False) -> None:
    validate_package()
    payload, content_type = multipart_archive()
    base, expected_slug, expected_version = publication_target(require_existing=not create)
    if create:
        status, raw = request("/api/sites", payload, content_type)
        result = response_json(status, raw, operation="site creation")
        if not result.get("slug"):
            raise RuntimeError("site creation response omitted slug")
        save_receipt(result)
    elif not RECEIPT.exists():
        raise ValueError(
            "bound Browser Use site receipt is missing; refusing to create a new site"
        )
    else:
        result = json.loads(RECEIPT.read_text(encoding="utf-8"))
        if result.get("slug") != expected_slug:
            raise ValueError("Artifact Hub receipt differs from the bound Browser Use site")
        status, raw = request(f"/api/sites/{result['slug']}")
        current = response_json(status, raw, operation="site lookup")
        version_id = (current.get("site") or {}).get("currentVersionId")
        if not version_id:
            raise RuntimeError("could not resolve the current Artifact Hub version")
        if version_id != expected_version:
            raise ValueError("Artifact Hub current version differs from current-report authority")
        query = urllib.parse.urlencode({"expected_version": expected_version})
        status, raw = request(
            f"/api/sites/{result['slug']}/versions?{query}", payload, content_type
        )
        updated = response_json(status, raw, operation="site version upload")
        result.update(
            {
                key: value
                for key, value in updated.items()
                if key not in {"editToken", "claimToken"}
            }
        )
    title_status, title_raw = request(
        f"/api/sites/{result['slug']}",
        json.dumps({"title": "Chromium 与 Moli 的 Browser Use 能力对比"}).encode(),
        "application/json",
        method="PATCH",
    )
    if not 200 <= title_status < 300:
        response_json(title_status, title_raw, operation="site title update")
    result["title"] = "Chromium 与 Moli 的 Browser Use 能力对比"
    if not result.get("share_url"):
        status, raw = request(
            f"/api/sites/{result['slug']}/shares",
            json.dumps({"policy": "public"}).encode(),
            "application/json",
        )
        share = response_json(status, raw, operation="public share creation")
        result["share_url"] = share["url"]
    version_id = str(
        result.get("versionId")
        or result.get("version_id")
        or result.get("currentVersionId")
        or ""
    )
    bind_published_version(version_id)
    save_receipt(result)
    public = urllib.request.Request(
        result["share_url"], headers={"User-Agent": "Browser-Use-Report-Publisher/1.0"}
    )
    with urllib.request.urlopen(public, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"public report returned {response.status}")
    preview = urllib.request.Request(
        f"{base}/api/preview/{result['slug']}",
        headers={"User-Agent": "Browser-Use-Report-Publisher/1.0"},
    )
    with urllib.request.urlopen(preview, timeout=60) as response:
        body = response.read().decode("utf-8", errors="replace")
        validate_public_preview(response.status, body)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--create",
        action="store_true",
        help="explicitly create a new owned site when the bound site cannot be updated",
    )
    main(create=parser.parse_args().create)
