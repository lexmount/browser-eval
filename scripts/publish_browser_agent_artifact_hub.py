#!/usr/bin/env python3
"""Publish the validated browser-agent report as a new Artifact Hub site."""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from pathlib import Path

from harness.web_fetch.artifact_auth import load_token

BASE = "https://artifact-hub.dev.lexmount.net"
ARCHIVE = Path("publish/browser-agent-sites.zip")
RECEIPT = Path("publish/browser-agent-artifact-hub-receipt.json")


def request(path: str, data: bytes | None = None, content_type: str | None = None):
    headers = {
        "Authorization": "Bearer " + load_token(),
        "User-Agent": "Browser-Agent-Report-Publisher/1.0",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=240) as response:
        return response.status, response.read()


def main() -> None:
    if RECEIPT.exists():
        result = json.loads(RECEIPT.read_text(encoding="utf-8"))
    else:
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
        status, raw = request(
            "/api/sites",
            b"".join(parts),
            "multipart/form-data; boundary=" + boundary,
        )
        result = json.loads(raw)
        if status != 200 or not result.get("slug"):
            raise RuntimeError(result)
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(RECEIPT, 0o600)
    if not result.get("share_url"):
        status, raw = request(
            f"/api/sites/{result['slug']}/shares",
            json.dumps({"policy": "public"}).encode(),
            "application/json",
        )
        if not 200 <= status < 300:
            raise RuntimeError(raw.decode(errors="replace"))
        result["share_url"] = json.loads(raw)["url"]
        RECEIPT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(RECEIPT, 0o600)
    public = urllib.request.Request(
        result["share_url"], headers={"User-Agent": "Browser-Agent-Report-Publisher/1.0"}
    )
    with urllib.request.urlopen(public, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"public report returned {response.status}")
    preview = urllib.request.Request(
        f"{BASE}/api/preview/{result['slug']}",
        headers={"User-Agent": "Browser-Agent-Report-Publisher/1.0"},
    )
    with urllib.request.urlopen(preview, timeout=60) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status != 200 or "Browser Use 公开评测怎么选" not in body:
            raise RuntimeError("public preview is unavailable or missing the report heading")
    print(json.dumps({"slug": result["slug"], "url": result["share_url"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
