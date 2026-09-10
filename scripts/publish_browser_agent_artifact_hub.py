#!/usr/bin/env python3
"""Create or atomically update the browser-agent report on Artifact Hub."""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from harness.web_fetch.artifact_auth import load_token

ARCHIVE = Path("publish/browser-agent-sites.zip")
RECEIPT = Path("publish/browser-agent-artifact-hub-receipt.json")
PROFILE = Path("evaluation/browser-agent/current-report.json")


def publication_target() -> tuple[str, str]:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    publication = profile.get("publication") or {}
    base = str(publication.get("base_url") or "").rstrip("/")
    slug = str(publication.get("site_slug") or "")
    if not base.startswith("https://") or not slug:
        raise ValueError("current Browser Agent report has no valid publication target")
    return base, slug


def request(
    path: str,
    data: bytes | None = None,
    content_type: str | None = None,
    *,
    method: str | None = None,
):
    base, _ = publication_target()
    headers = {
        "Authorization": "Bearer " + load_token(),
        "User-Agent": "Browser-Agent-Report-Publisher/1.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=240) as response:
        return response.status, response.read()


def multipart_archive() -> tuple[bytes, str]:
    if not ARCHIVE.is_file():
        raise ValueError(f"missing validated package: {ARCHIVE}")
    boundary = "----browseragent" + uuid.uuid4().hex
    parts = []
    for key, value in (
        ("mode", "zip"),
        ("title", "Browser Use 公开评测与 WebArena-Verified 对比"),
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
            'filename="browser-agent.zip"\r\nContent-Type: application/zip\r\n\r\n'
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
    payload, content_type = multipart_archive()
    base, expected_slug = publication_target()
    if create:
        status, raw = request("/api/sites", payload, content_type)
        result = json.loads(raw)
        if status != 200 or not result.get("slug"):
            raise RuntimeError(result)
        save_receipt(result)
    elif not RECEIPT.exists():
        raise ValueError(
            "bound Browser Agent site receipt is missing; refusing to create a new site"
        )
    else:
        result = json.loads(RECEIPT.read_text(encoding="utf-8"))
        if result.get("slug") != expected_slug:
            raise ValueError("Artifact Hub receipt differs from the bound Browser Agent site")
        status, raw = request(f"/api/sites/{result['slug']}")
        current = json.loads(raw)
        version_id = (current.get("site") or {}).get("currentVersionId")
        if status != 200 or not version_id:
            raise RuntimeError("could not resolve the current Artifact Hub version")
        query = urllib.parse.urlencode({"expected_version": version_id})
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
        result["share_url"], headers={"User-Agent": "Browser-Agent-Report-Publisher/1.0"}
    )
    with urllib.request.urlopen(public, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"public report returned {response.status}")
    preview = urllib.request.Request(
        f"{base}/api/preview/{result['slug']}",
        headers={"User-Agent": "Browser-Agent-Report-Publisher/1.0"},
    )
    with urllib.request.urlopen(preview, timeout=60) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status != 200 or "同一自主 Agent、同一模型，只替换浏览器" not in body:
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
