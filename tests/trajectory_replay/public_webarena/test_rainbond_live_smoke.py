"""Live HTTP smoke test for one isolated Rainbond WebArena slice.

Set WEBARENA_RAINBOND_SMOKE_URLS to a JSON object containing the four site
URLs, then run this file with the real_smoke marker. The test sends requests
to every site concurrently so a green result covers the shared gateway path
as well as each site's basic page rendering.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

SITES = {
    "shopping_admin": "<title>Home Page",
    "shopping": "<title>One Stop Market",
    "reddit": "<title>Postmill",
    "gitlab": "<title>Sign in · GitLab",
}
REQUESTS_PER_SITE = 8
REQUEST_TIMEOUT_SECONDS = 30
P95_LIMIT_SECONDS = 10


def _configured_urls() -> dict[str, str]:
    raw = os.environ.get("WEBARENA_RAINBOND_SMOKE_URLS", "")
    if not raw:
        pytest.skip("WEBARENA_RAINBOND_SMOKE_URLS is not set")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != set(SITES):
        raise AssertionError(
            "WEBARENA_RAINBOND_SMOKE_URLS must contain exactly: "
            + ", ".join(sorted(SITES))
        )
    urls = {site: str(url).rstrip("/") + "/" for site, url in value.items()}
    if any(not url.startswith(("http://", "https://")) for url in urls.values()):
        raise AssertionError("all Rainbond smoke URLs must use HTTP or HTTPS")
    return urls


def _request(site: str, url: str) -> tuple[str, float, int]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "browser-eval-rainbond-smoke/1"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read()
        status = response.status
    elapsed = time.monotonic() - started
    assert status == 200, f"{site} returned HTTP {status}"
    assert SITES[site].encode() in body, f"{site} returned the wrong page"
    return site, elapsed, len(body)


@pytest.mark.real_smoke
def test_isolated_rainbond_slice_handles_concurrent_page_loads():
    urls = _configured_urls()
    jobs = [
        (site, urls[site])
        for _ in range(REQUESTS_PER_SITE)
        for site in sorted(SITES)
    ]
    results: list[tuple[str, float, int]] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = [executor.submit(_request, site, url) for site, url in jobs]
        for future in as_completed(futures):
            results.append(future.result())

    assert len(results) == len(SITES) * REQUESTS_PER_SITE
    for site in sorted(SITES):
        durations = sorted(elapsed for name, elapsed, _size in results if name == site)
        sizes = [size for name, _elapsed, size in results if name == site]
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        assert p95 < P95_LIMIT_SECONDS, f"{site} p95 was {p95:.3f}s"
        assert min(sizes) > 1_000, f"{site} returned an unexpectedly small page"
        print(
            f"{site}: requests={len(durations)} "
            f"median={statistics.median(durations):.3f}s p95={p95:.3f}s"
        )
