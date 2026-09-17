"""Verify the repository's WebArena deployment and evaluation configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path = ROOT) -> dict:
    lock_path = root / "evaluation/browser-use-public/webarena-environment-images.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    runtime_path = root / "deploy/webarena/runtime.example.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    evaluation = json.loads(
        (
            root / "examples/trajectory_replay/public_webarena/webarena.config.example.json"
        ).read_text(encoding="utf-8")
    )
    delivery_path = root / "deploy/webarena/site-delivery-contract.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    if lock.get("schema") != "lex_webarena_environment_images/1":
        raise ValueError("invalid image lock schema")
    if (
        lock.get("webarena_verified_version") != "1.2.3"
        or delivery.get("webarena_verified_version") != "1.2.3"
    ):
        raise ValueError("WebArena package version differs")
    sites = {"shopping_admin", "shopping", "reddit", "gitlab", "map", "wikipedia"}
    if set(lock.get("images") or {}) != sites or set(runtime.get("sites") or {}) != sites:
        raise ValueError("site inventory differs")
    for path, config in ((runtime_path, runtime), (delivery_path, delivery)):
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


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False))
