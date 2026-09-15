"""Resolve official WebArena-Verified Docker tags to immutable registry digests."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any

IMAGES = {
    site: f"am1n3e/webarena-verified-{site}:latest"
    for site in ("shopping_admin", "shopping", "reddit", "gitlab", "wikipedia", "map")
}
ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def _json_url(url: str, *, headers: dict[str, str] | None = None) -> tuple[Any, Any]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response), response.headers


def resolve_image(reference: str) -> dict[str, Any]:
    repository, tag = reference.rsplit(":", 1)
    token_payload, _ = _json_url(
        "https://auth.docker.io/token?service=registry.docker.io&scope="
        f"repository:{repository}:pull"
    )
    token = str(token_payload["token"])
    authenticated = {"Accept": ACCEPT, "Authorization": f"Bearer {token}"}
    manifest, headers = _json_url(
        f"https://registry-1.docker.io/v2/{repository}/manifests/{tag}",
        headers=authenticated,
    )
    digest = str(headers.get("Docker-Content-Digest") or "")
    if not digest.startswith("sha256:"):
        raise ValueError(f"registry omitted immutable digest for {reference}")
    layers = manifest.get("layers") or []
    platforms = [item.get("platform") for item in manifest.get("manifests") or []]
    config_digest = (manifest.get("config") or {}).get("digest")
    image_config = {}
    if isinstance(config_digest, str):
        image_config, _ = _json_url(
            f"https://registry-1.docker.io/v2/{repository}/blobs/{config_digest}",
            headers=authenticated,
        )
    return {
        "tag": reference,
        "pinned": f"{repository}@{digest}",
        "manifest_digest": digest,
        "media_type": manifest.get("mediaType"),
        "compressed_layer_bytes": sum(int(item.get("size") or 0) for item in layers),
        "platforms": platforms,
        "architecture": image_config.get("architecture"),
        "os": image_config.get("os"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = {
        "schema": "lex_webarena_environment_images/1",
        "source": "Docker Hub registry manifests",
        "webarena_verified_version": "1.2.3",
        "images": {site: resolve_image(reference) for site, reference in IMAGES.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
