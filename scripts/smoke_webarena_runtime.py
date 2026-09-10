"""Diagnostic-only browser-to-WebArena connectivity smoke with redacted HAR evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import docker
from playwright.sync_api import sync_playwright

from harness.browser_use.har import CdpHarRecorder
from harness.browser_use.runner import (
    ENGINE_IMAGES,
    ENGINE_PORTS,
    _cleanup_runtime,
    _published_endpoint,
    _start_browser_container,
    _validate_measured_resource_controls,
    _wait_cdp,
)
from harness.browser_use.webarena_environment import WebArenaEnvironment

SITE_TOKENS = {
    "shopping_admin": "__SHOPPING_ADMIN__",
    "shopping": "__SHOPPING__",
    "reddit": "__REDDIT__",
    "gitlab": "__GITLAB__",
    "map": "__MAP__",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def site_urls(config: dict[str, Any], sites: tuple[str, ...]) -> dict[str, str]:
    environments = config.get("environments") or {}
    urls: dict[str, str] = {}
    for site in sites:
        token = SITE_TOKENS[site]
        row = environments.get(token)
        if not isinstance(row, dict):
            raise ValueError(f"runtime config is missing {token}")
        if row.get("credentials") is not None:
            raise ValueError(f"diagnostic runtime config must not persist credentials: {token}")
        values = row.get("urls") or []
        index = int(row.get("active_url_idx") or 0)
        if not values or index < 0 or index >= len(values):
            raise ValueError(f"runtime config has no active URL for {token}")
        urls[site] = str(values[index])
    return urls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site", action="append", choices=tuple(SITE_TOKENS), required=True)
    parser.add_argument(
        "--verify-login",
        action="store_true",
        help="authenticate with credentials_env before starting diagnostic HAR capture",
    )
    parser.add_argument("--cpus", type=float, default=None)
    args = parser.parse_args()
    config_path = args.config.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite diagnostic evidence: {output}")
    sites = tuple(dict.fromkeys(args.site))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    urls = site_urls(config, sites)
    output.mkdir(parents=True)
    client = docker.from_env()
    if args.cpus is not None:
        if args.cpus <= 0:
            raise ValueError("--cpus must be positive")
        _validate_measured_resource_controls(client.info())
    receipt: dict[str, Any] = {
        "schema": "lex_webarena_runtime_smoke/1",
        "diagnostic_only": True,
        "qualification_evidence": False,
        "cpu_limit": args.cpus,
        "memory_limit": "1g",
        "login_verified": args.verify_login,
        "config_sha256": _sha256(config_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engines": {},
    }
    for engine in ("chromium", "moli"):
        client.images.get(ENGINE_IMAGES[engine])
        run_id = f"runtime-smoke-{uuid.uuid4().hex[:10]}"
        network = client.networks.create(
            f"lex-browser-use-eval-{run_id}-{engine}",
            driver="bridge",
            labels={"lex.browser-use-eval.diagnostic": run_id},
        )
        containers: dict[str, Any] = {}
        browsers: dict[str, Any] = {}
        playwright = sync_playwright().start()
        try:
            container = _start_browser_container(
                client,
                engine=engine,
                run_id=run_id,
                cpus=args.cpus,
                memory="1g",
                network=network.name,
            )
            containers[engine] = container
            container.reload()
            endpoint = _published_endpoint(
                container, engine=engine, cdp_port=ENGINE_PORTS[engine]
            )
            browser = playwright.chromium.connect_over_cdp(
                _wait_cdp(endpoint, timeout_seconds=30, container=container)
            )
            browsers[engine] = browser
            page = browser.contexts[0].pages[0]
            if args.verify_login:
                environment = WebArenaEnvironment(config_path)
                page = environment.prepare(
                    SimpleNamespace(
                        task_id="diagnostic-login-smoke",
                        environment={
                            "kind": "webarena_verified",
                            "sites": list(sites),
                            "start_urls": [SITE_TOKENS[site] for site in sites],
                        },
                    ),
                    page,
                )
            recorder = CdpHarRecorder(page)
            pages = []
            for site, url in urls.items():
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                status = response.status if response is not None else None
                if status is None or status >= 400:
                    raise RuntimeError(f"{engine} failed to load {site}: HTTP {status}")
                pages.append(
                    {
                        "site": site,
                        "requested_url": url,
                        "final_url": page.url,
                        "http_status": status,
                        "title": page.title(),
                    }
                )
            har_path = output / f"{engine}.har"
            har = recorder.stop(har_path)
            receipt["engines"][engine] = {
                "container_image_id": container.image.id,
                "pages": pages,
                "har": {**har, "sha256": _sha256(har_path)},
            }
        finally:
            _cleanup_runtime(browsers, playwright, containers, {engine: network})
    (output / "receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
