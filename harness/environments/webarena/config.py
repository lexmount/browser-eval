"""Validated image lock and host-local runtime configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SITES = ("shopping_admin", "shopping", "reddit", "gitlab", "map", "wikipedia")
SITE_TOKENS = {site: "__" + site.upper() + "__" for site in SITES}
WEBARENA_VERIFIED_VERSION = "1.2.3"
ARCH_ALIASES = {"aarch64": "arm64", "x86_64": "amd64"}


@dataclass(frozen=True)
class SiteConfig:
    name: str
    image: str
    architecture: str
    port: int
    control_port: int


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    hostname: str
    sites: dict[str, SiteConfig]

    @classmethod
    def load(cls, path: str | Path) -> RuntimeConfig:
        config_path = Path(path).resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if payload.get("schema") != "lexmount_webarena_runtime/1":
            raise ValueError("invalid WebArena runtime schema")
        hostname = str(payload.get("hostname") or "").strip()
        if not hostname or "${" in hostname:
            raise ValueError("runtime hostname must replace the example placeholder")
        lock_path = Path(str(payload.get("image_lock") or ""))
        if not lock_path.is_absolute():
            lock_path = config_path.parent / lock_path
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("schema") != "lex_webarena_environment_images/1":
            raise ValueError("invalid WebArena image lock schema")
        if lock.get("webarena_verified_version") != "1.2.3":
            raise ValueError("image lock does not match webarena-verified 1.2.3")
        runtime_sites = payload.get("sites") or {}
        locked_sites = lock.get("images") or {}
        sites: dict[str, SiteConfig] = {}
        used_ports: set[int] = set()
        for name in SITES:
            runtime = runtime_sites.get(name)
            image = locked_sites.get(name)
            if not isinstance(runtime, dict) or not isinstance(image, dict):
                raise ValueError(f"runtime or image lock is missing site {name}")
            port = int(runtime.get("port") or 0)
            control_port = int(runtime.get("control_port") or 0)
            if not 1 <= port <= 65535 or not 1 <= control_port <= 65535:
                raise ValueError(f"site {name} ports must be between 1 and 65535")
            if port == control_port or {port, control_port} & used_ports:
                raise ValueError(f"site {name} ports must be unique")
            used_ports.update((port, control_port))
            pinned = str(image.get("pinned") or "")
            if "@sha256:" not in pinned:
                raise ValueError(f"site {name} image must be pinned by digest")
            architecture = normalized_architecture(image.get("architecture"))
            if architecture not in {"amd64", "arm64"}:
                raise ValueError(f"site {name} architecture must be explicitly amd64 or arm64")
            sites[name] = SiteConfig(
                name=name,
                image=pinned,
                architecture=architecture,
                port=port,
                control_port=control_port,
            )
        return cls(path=config_path, hostname=hostname, sites=sites)


def normalized_architecture(value: Any) -> str:
    architecture = str(value or "")
    return ARCH_ALIASES.get(architecture, architecture)


def official_image_references() -> dict[str, str]:
    return {site: f"am1n3e/webarena-verified-{site}:latest" for site in SITES}


REGISTRY_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def _registry_json(url: str, *, headers: dict[str, str] | None = None) -> tuple[Any, Any]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        digest = response.headers.get("Docker-Content-Digest")
        if digest and digest != "sha256:" + hashlib.sha256(raw).hexdigest():
            raise ValueError("registry digest does not match received bytes")
        return json.loads(raw), response.headers


def resolve_image(reference: str) -> dict[str, Any]:
    repository, tag = reference.rsplit(":", 1)
    token_payload, _ = _registry_json(
        "https://auth.docker.io/token?service=registry.docker.io&scope="
        f"repository:{repository}:pull"
    )
    token = str(token_payload["token"])
    authenticated = {"Accept": REGISTRY_ACCEPT, "Authorization": f"Bearer {token}"}
    manifest, headers = _registry_json(
        f"https://registry-1.docker.io/v2/{repository}/manifests/{tag}",
        headers=authenticated,
    )
    digest = str(headers.get("Docker-Content-Digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"registry omitted immutable digest for {reference}")
    layers = manifest.get("layers") or []
    platforms = [item.get("platform") for item in manifest.get("manifests") or []]
    config_digest = (manifest.get("config") or {}).get("digest")
    image_config = {}
    if isinstance(config_digest, str):
        image_config, _ = _registry_json(
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


def main_image_lock(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from harness.web_fetch.safety import unlinked_path

    args.output = unlinked_path(args.output)
    if args.output.exists():
        raise ValueError("image lock output already exists")
    payload = {
        "schema": "lex_webarena_environment_images/1",
        "source": "Docker Hub registry manifests",
        "webarena_verified_version": WEBARENA_VERIFIED_VERSION,
        "images": {
            site: resolve_image(reference)
            for site, reference in official_image_references().items()
        },
    }
    from harness.web_fetch.safety import write_new_file

    write_new_file(
        args.output, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )


def verify_deployment_contract(root: Path) -> dict:
    lock_path = root / "evaluation/browser-use-public/webarena-environment-images.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    runtime_path = root / "deploy/webarena/runtime.example.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    evaluation_path = (
        root / "examples/trajectory_replay/public_webarena/webarena.config.example.json"
    )
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    pool_path = root / "examples/trajectory_replay/public_webarena/rainbond-pool.example.json"
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    delivery_path = root / "deploy/webarena/site-delivery-contract.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    if lock.get("schema") != "lex_webarena_environment_images/1":
        raise ValueError("invalid image lock schema")
    if (
        lock.get("webarena_verified_version") != WEBARENA_VERIFIED_VERSION
        or delivery.get("webarena_verified_version") != WEBARENA_VERIFIED_VERSION
    ):
        raise ValueError("WebArena package version differs")
    sites = set(SITES)
    if set(lock.get("images") or {}) != sites or set(runtime.get("sites") or {}) != sites:
        raise ValueError("site inventory differs")
    for path, config in (
        (runtime_path, runtime),
        (delivery_path, delivery),
        (evaluation_path, evaluation),
        (pool_path, pool),
    ):
        if (
            not config.get("image_lock")
            or (path.parent / config["image_lock"]).resolve() != lock_path.resolve()
        ):
            raise ValueError("deployment must reference the canonical image lock")
    if set(delivery.get("components") or {}) != sites - {"wikipedia"}:
        raise ValueError("site delivery inventory differs")
    for site in sites:
        image = lock["images"][site]
        if not re.fullmatch(r".+@sha256:[a-f0-9]{64}", image.get("pinned", "")):
            raise ValueError(f"image must have a valid digest: {site}")
        if image.get("architecture") not in {"amd64", "arm64"} or image.get("os") != "linux":
            raise ValueError(f"image platform is invalid: {site}")
        row = runtime["sites"][site]
        eval_row = evaluation["environments"][f"__{site.upper()}__"]
        if len(eval_row["urls"]) != 1 or urlparse(eval_row["urls"][0]).port != row["port"]:
            raise ValueError(f"site port differs: {site}")
        if urlparse(evaluation["control_urls"][site]).port != row["control_port"]:
            raise ValueError(f"control port differs: {site}")
        if site in runtime["credentials_env"] and runtime["credentials_env"][site] != eval_row.get(
            "credentials_env"
        ):
            raise ValueError(f"credential variable names differ: {site}")
        component = delivery["components"].get(site)
        if component is not None:
            if "image" in component or component.get("architecture") != image["architecture"]:
                raise ValueError(f"site delivery must use the canonical image identity: {site}")
    map_site = delivery["components"]["map"]
    names = [Path(urlparse(url).path).name for url in map_site["data_urls"]]
    digests = map_site["data_archive_sha256"]
    sizes = map_site["data_archive_bytes"]
    if (
        len(names) != 3
        or len(set(names)) != 3
        or set(names) != set(digests)
        or set(names) != set(sizes)
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(value)) for value in digests.values())
        or any(type(value) is not int or value <= 0 for value in sizes.values())
    ):
        raise ValueError("Map archive checksum contract is incomplete")
    return {
        "status": "pass",
        "sites": len(sites),
        "image_lock": "canonical",
        "runtime_templates": "matching",
    }


def deployment_check_main(argv=None, *, default_root=None):
    parser = argparse.ArgumentParser(description="Verify deployment and evaluation configuration.")
    parser.add_argument("--root", type=Path, default=default_root or Path.cwd())
    args = parser.parse_args(argv)
    print(json.dumps(verify_deployment_contract(args.root), ensure_ascii=False))
