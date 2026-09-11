"""Create or update the standalone fixed-trajectory report on Artifact Hub."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.artifact_auth import load_token  # noqa: E402

DEFAULT_BASE = "https://artifact-hub.app.lexmount.com"


def _request(base: str, token: str, path: str, data=None, content_type=None, auth=True):
    headers = {"User-Agent": "WebArena-Fixed-Trajectory-Publisher/1.0"}
    if auth:
        headers["Authorization"] = "Bearer " + token
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(base + path, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=240) as response:
        return response.status, response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--update", action="store_true")
    parser.add_argument("--expected-version")
    parser.add_argument(
        "--archive", type=Path, default=ROOT / "publish/webarena-fixed-trajectory.zip"
    )
    parser.add_argument(
        "--attestation",
        type=Path,
        default=ROOT / "publish/webarena-fixed-trajectory-attestation.json",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "publish/webarena-fixed-trajectory-site-receipt.json",
    )
    parser.add_argument(
        "--base-url", default=os.environ.get("ARTIFACT_HUB_BASE_URL", DEFAULT_BASE)
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    token = load_token(base_url=base)
    attestation = json.loads(args.attestation.read_text(encoding="utf-8"))
    archive_sha = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if archive_sha != attestation.get("archive_sha256"):
        raise ValueError("publication archive does not match its attestation")
    if args.create and args.receipt.exists():
        raise ValueError("--create refuses to overwrite an existing site receipt")
    if args.update and (
        not args.receipt.is_file()
        or not args.expected_version
        or not args.expected_version.startswith("ver_")
    ):
        raise ValueError("--update requires a receipt and --expected-version ver_...")

    prior = {} if args.create else json.loads(args.receipt.read_text(encoding="utf-8"))
    boundary = "----webarena" + uuid.uuid4().hex
    parts = []
    for key, value in (
        ("mode", "zip"),
        ("title", "Chromium 与 Moli：WebArena 固定轨迹回放评测"),
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
            'filename="webarena-fixed-trajectory.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode()
        + args.archive.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    endpoint = "/api/sites"
    if args.update:
        endpoint = (
            "/api/sites/"
            + quote(str(prior["slug"]), safe="")
            + "/versions?expected_version="
            + quote(args.expected_version, safe="")
        )
    status, raw = _request(
        base,
        token,
        endpoint,
        b"".join(parts),
        "multipart/form-data; boundary=" + boundary,
    )
    if status != 200:
        raise RuntimeError(f"Artifact Hub returned {status}")
    response = json.loads(raw)
    result = response if args.create else {**prior, **response}
    result["base_url"] = base
    if not result.get("share_url"):
        _status, raw = _request(
            base,
            token,
            "/api/sites/" + quote(str(result["slug"]), safe="") + "/shares",
            json.dumps({"policy": "public"}).encode(),
            "application/json",
        )
        result["share_url"] = json.loads(raw)["url"]
    public = urllib.request.Request(result["share_url"], headers={"User-Agent": "report-check/1"})
    with urllib.request.urlopen(public, timeout=60) as response_public:
        if response_public.status != 200:
            raise RuntimeError("public share link is not reachable")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    os.chmod(args.receipt, 0o600)
    print(
        json.dumps(
            {
                "slug": result["slug"],
                "version": (
                    (result.get("version") or {}).get("id")
                    or result.get("versionId")
                    or result.get("version_id")
                ),
                "url": result["share_url"],
                "unauthenticated_status": 200,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
