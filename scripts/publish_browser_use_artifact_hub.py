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

from harness.web_fetch.artifact_auth import load_token

ARCHIVE = Path("publish/browser-use-sites.zip")
ATTESTATION = Path("publish/browser-use-sites.attestation.json")
RECEIPT = Path("publish/browser-use-artifact-hub-receipt.json")
PROFILE = Path("evaluation/browser-use/current-report.json")


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
    report = Path(str(attestation.get("report") or ""))
    evidence = Path(str(attestation.get("evidence") or ""))
    expected_evidence = attestation.get("evidence_files") or {}
    checks = {
        "archive": sha256(ARCHIVE) == attestation.get("archive_sha256"),
        "report": report.is_dir()
        and tree_sha256(report) == attestation.get("report_tree_sha256"),
        "profile": sha256(PROFILE) == attestation.get("profile_sha256"),
        "evidence": evidence.is_dir()
        and all(
            (evidence / name).is_file() and sha256(evidence / name) == digest
            for name, digest in expected_evidence.items()
        ),
    }
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError(
            "publication package is stale; rebuild before publishing: "
            + ", ".join(failed)
        )


def publication_target() -> tuple[str, str, str]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    publication = profile.get("publication") or {}
    base = str(publication.get("base_url") or "").rstrip("/")
    slug = str(publication.get("site_slug") or "")
    version = str(publication.get("expected_version") or "")
    if not base.startswith("https://") or not slug or not version.startswith("ver_"):
        raise ValueError("current Browser Use report has no valid publication target")
    return base, slug, version


def request(
    path: str,
    data: bytes | None = None,
    content_type: str | None = None,
    *,
    method: str | None = None,
):
    base, _, _ = publication_target()
    headers = {
        "Authorization": "Bearer " + load_token(),
        "User-Agent": "Browser-Use-Report-Publisher/1.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=240) as response:
        return response.status, response.read()


def multipart_archive() -> tuple[bytes, str]:
    if not ARCHIVE.is_file():
        raise ValueError(f"missing validated package: {ARCHIVE}")
    boundary = "----browseruse" + uuid.uuid4().hex
    parts = []
    for key, value in (
        ("mode", "zip"),
        ("title", "Chromium 与 Moli：Browser Use 能力实测"),
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


def main(*, create: bool = False) -> None:
    validate_package()
    payload, content_type = multipart_archive()
    base, expected_slug, expected_version = publication_target()
    if create:
        status, raw = request("/api/sites", payload, content_type)
        result = json.loads(raw)
        if status != 200 or not result.get("slug"):
            raise RuntimeError(result)
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
        current = json.loads(raw)
        version_id = (current.get("site") or {}).get("currentVersionId")
        if status != 200 or not version_id:
            raise RuntimeError("could not resolve the current Artifact Hub version")
        if version_id != expected_version:
            raise ValueError("Artifact Hub current version differs from current-report authority")
        query = urllib.parse.urlencode({"expected_version": expected_version})
        status, raw = request(
            f"/api/sites/{result['slug']}/versions?{query}", payload, content_type
        )
        updated = json.loads(raw)
        if not 200 <= status < 300:
            raise RuntimeError(updated)
        result.update(
            {
                key: value
                for key, value in updated.items()
                if key not in {"editToken", "claimToken"}
            }
        )
    if not result.get("share_url"):
        status, raw = request(
            f"/api/sites/{result['slug']}/shares",
            json.dumps({"policy": "public"}).encode(),
            "application/json",
        )
        if not 200 <= status < 300:
            raise RuntimeError(raw.decode(errors="replace"))
        result["share_url"] = json.loads(raw)["url"]
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
        if response.status != 200 or "Chromium 与 Moli：Browser Use 能力实测" not in body:
            raise RuntimeError("public preview is unavailable or missing the report heading")
    print(json.dumps({"slug": result["slug"], "url": result["share_url"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--create",
        action="store_true",
        help="explicitly create a new owned site when the bound site cannot be updated",
    )
    main(create=parser.parse_args().create)
