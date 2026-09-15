"""Small opt-in smoke test against the real Chromium and Moli containers."""

from __future__ import annotations

import os
import uuid

import docker
import pytest
from playwright.sync_api import sync_playwright

from harness.browser_use.runner import (
    ENGINE_IMAGES,
    ENGINE_PORTS,
    _cleanup_runtime,
    _published_endpoint,
    _start_browser_container,
    _wait_cdp,
)

pytestmark = [
    pytest.mark.real_smoke,
    pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_USE_SMOKE") != "1",
        reason="set RUN_BROWSER_USE_SMOKE=1 to run real browser containers",
    ),
]


@pytest.mark.parametrize("engine", ("chromium", "moli"))
def test_real_browser_opens_public_page_and_extracts_heading(engine: str) -> None:
    """Start the production evaluation image, connect over CDP, and use the live web."""
    client = docker.from_env()
    client.images.get(ENGINE_IMAGES[engine])
    run_id = f"smoke-{uuid.uuid4().hex[:10]}"
    network = client.networks.create(
        f"lex-browser-use-eval-{run_id}-{engine}",
        driver="bridge",
        labels={"lex.browser-use-eval.smoke": run_id},
    )
    containers = {}
    browsers = {}
    playwright = sync_playwright().start()
    try:
        container = _start_browser_container(
            client,
            engine=engine,
            run_id=run_id,
            cpus=1.0,
            memory="1g",
            network=network.name,
        )
        containers[engine] = container
        container.reload()
        endpoint = _published_endpoint(
            container,
            engine=engine,
            cdp_port=ENGINE_PORTS[engine],
        )
        websocket_url = _wait_cdp(endpoint, timeout_seconds=30, container=container)
        browser = playwright.chromium.connect_over_cdp(websocket_url)
        browsers[engine] = browser
        page = browser.contexts[0].pages[0]

        response = page.goto(
            "https://example.com/",
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        assert response is not None and response.ok
        assert page.locator("h1").inner_text() == "Example Domain"
    finally:
        _cleanup_runtime(browsers, playwright, containers, {engine: network})
