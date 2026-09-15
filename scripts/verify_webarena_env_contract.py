"""Verify that webarena-env and browser-eval bind the same site images."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "evaluation/browser-use-public/webarena-env-contract.json"


def verify(environment_repo: Path, contract_path: Path = DEFAULT_CONTRACT) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema") != "lexmount_webarena_cross_repo_contract/1":
        raise ValueError("invalid cross-repository contract schema")
    env_lock = json.loads(
        (environment_repo.resolve() / contract["environment_lock"]).read_text(encoding="utf-8")
    )
    eval_lock = json.loads(
        (ROOT / contract["evaluation_lock"]).read_text(encoding="utf-8")
    )
    expected_version = contract["webarena_verified_version"]
    if env_lock.get("webarena_verified_version") != expected_version:
        raise ValueError("webarena-env package version binding differs")
    if eval_lock.get("webarena_verified_version") != expected_version:
        raise ValueError("browser-eval package version binding differs")
    sites = contract["required_sites"]
    if set(env_lock.get("images") or {}) != set(sites):
        raise ValueError("webarena-env image inventory differs")
    if set(eval_lock.get("images") or {}) != set(sites):
        raise ValueError("browser-eval image inventory differs")
    mismatches = []
    for site in sites:
        left = env_lock["images"][site]
        right = eval_lock["images"][site]
        for key in ("pinned", "architecture", "os"):
            if left.get(key) != right.get(key):
                mismatches.append(f"{site}.{key}")
    if mismatches:
        raise ValueError("cross-repository image binding differs: " + ", ".join(mismatches))
    env_runtime = json.loads(
        (environment_repo.resolve() / contract["environment_runtime_template"]).read_text(
            encoding="utf-8"
        )
    )
    eval_runtime = json.loads(
        (ROOT / contract["evaluation_runtime_template"]).read_text(encoding="utf-8")
    )
    for site in sites:
        token = f"__{site.upper()}__"
        site_row = (env_runtime.get("sites") or {}).get(site) or {}
        eval_row = (eval_runtime.get("environments") or {}).get(token) or {}
        urls = eval_row.get("urls") or []
        control_url = (eval_runtime.get("control_urls") or {}).get(site)
        if len(urls) != 1 or not control_url:
            raise ValueError(f"cross-repository runtime template is missing {site} URLs")
        if urlparse(str(urls[0])).port != site_row.get("port"):
            raise ValueError(f"cross-repository runtime site port differs: {site}")
        if urlparse(str(control_url)).port != site_row.get("control_port"):
            raise ValueError(f"cross-repository runtime control port differs: {site}")
        env_credentials = (env_runtime.get("credentials_env") or {}).get(site)
        if env_credentials is not None and env_credentials != eval_row.get("credentials_env"):
            raise ValueError(f"cross-repository credential variable names differ: {site}")
    delivery_path = environment_repo.resolve() / contract["environment_site_delivery_contract"]
    if not delivery_path.is_file():
        raise ValueError(f"environment repo is missing site delivery contract: {delivery_path}")
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    if delivery.get("webarena_verified_version") != expected_version:
        raise ValueError("cross-repository site delivery package version differs")
    image_lock = delivery.get("image_lock")
    if not isinstance(image_lock, str) or not image_lock:
        raise ValueError("cross-repository site delivery image_lock is missing")
    if (delivery_path.parent / image_lock).resolve() != (
        environment_repo.resolve() / contract["environment_lock"]
    ).resolve():
        raise ValueError("cross-repository site delivery image lock path differs")
    components = delivery.get("components") or {}
    if set(components) != set(sites) - {"wikipedia"}:
        raise ValueError("cross-repository site delivery inventory differs")
    for site, component in components.items():
        if component.get("image") != env_lock["images"][site]["pinned"]:
            raise ValueError(f"cross-repository site delivery image differs: {site}")
    map_site = components["map"]
    archive_urls = map_site.get("data_urls") or []
    archive_digests = map_site.get("data_archive_sha256") or {}
    archive_bytes = map_site.get("data_archive_bytes") or {}
    archive_names = [Path(urlparse(url).path).name for url in archive_urls]
    if (
        len(archive_names) != 3
        or len(set(archive_names)) != 3
        or set(archive_names) != set(archive_digests)
        or set(archive_names) != set(archive_bytes)
        or any(not re.fullmatch(r"[a-f0-9]{64}", str(value)) for value in archive_digests.values())
        or any(not isinstance(value, int) or value <= 0 for value in archive_bytes.values())
    ):
        raise ValueError("cross-repository Map archive checksum contract is incomplete")
    return {
        "status": "pass",
        "webarena_verified_version": expected_version,
        "sites": len(sites),
        "runtime_templates": "matching",
        "map_archive_checksums": "complete",
        "environment_pull_request": contract["environment_pull_request"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-repo", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    print(json.dumps(verify(args.environment_repo, args.contract), ensure_ascii=False))


if __name__ == "__main__":
    main()
