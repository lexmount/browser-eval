"""Live HTTP smoke test for one isolated Rainbond WebArena slice.

Set WEBARENA_RAINBOND_SMOKE_URLS to one four-site URL object or a list of
such objects to exercise multiple slices together. The test sends requests
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
WIKIPEDIA_ARTICLE = "/content/wikipedia_en_all_maxi_2022-05/A/Boston_Celtics"
REQUEST_TIMEOUT_SECONDS = 30
P95_LIMIT_SECONDS = 10


def _configured_urls() -> dict[str, str]:
    raw = os.environ.get("WEBARENA_RAINBOND_SMOKE_URLS", "")
    if not raw:
        pytest.skip("WEBARENA_RAINBOND_SMOKE_URLS is not set")
    value = json.loads(raw)
    slices = value if isinstance(value, list) else [value]
    assert slices, "at least one slice is required"
    urls = {}
    for index, sites in enumerate(slices, start=1):
        if not isinstance(sites, dict) or set(sites) != set(SITES):
            raise AssertionError("each slice must contain exactly: " + ", ".join(sorted(SITES)))
        for site, url in sites.items():
            urls[f"slice-{index}/{site}"] = str(url).rstrip("/") + "/"
    assert len(set(urls.values())) == len(urls), "mutable sites must have distinct URLs"
    if any(not url.startswith(("http://", "https://")) for url in urls.values()):
        raise AssertionError("all Rainbond smoke URLs must use HTTP or HTTPS")
    wikipedia = os.environ.get("WEBARENA_RAINBOND_SMOKE_WIKIPEDIA_URL", "").rstrip("/")
    if wikipedia:
        assert wikipedia.startswith(("http://", "https://")), "Wikipedia must use HTTP or HTTPS"
        urls["shared/wikipedia"] = wikipedia + WIKIPEDIA_ARTICLE
    return urls


def _request(site: str, url: str) -> tuple[str, float, int]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "browser-eval-rainbond-smoke/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
            status = response.status
    except Exception as exc:
        raise AssertionError(f"{site} request failed: {type(exc).__name__}: {exc}") from exc
    elapsed = time.monotonic() - started
    assert status == 200, f"{site} returned HTTP {status}"
    expected = (
        "<title>Boston Celtics" if site == "shared/wikipedia"
        else SITES[site.rsplit("/", 1)[-1]]
    )
    assert expected.encode() in body, f"{site} returned the wrong page"
    return site, elapsed, len(body)


@pytest.mark.real_smoke
def test_isolated_rainbond_slice_handles_concurrent_page_loads():
    urls = _configured_urls()
    jobs = [
        (site, urls[site])
        for _ in range(REQUESTS_PER_SITE)
        for site in sorted(urls)
    ]
    results: list[tuple[str, float, int]] = []
    failures = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = [executor.submit(_request, site, url) for site, url in jobs]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append(str(exc))

    assert not failures, "\n".join(failures)
    assert len(results) == len(urls) * REQUESTS_PER_SITE
    for site in sorted(urls):
        durations = sorted(elapsed for name, elapsed, _size in results if name == site)
        sizes = [size for name, _elapsed, size in results if name == site]
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        assert p95 < P95_LIMIT_SECONDS, f"{site} p95 was {p95:.3f}s"
        assert min(sizes) > 1_000, f"{site} returned an unexpectedly small page"
        print(
            f"{site}: requests={len(durations)} "
            f"median={statistics.median(durations):.3f}s p95={p95:.3f}s"
        )


def test_smoke_configuration_accepts_multiple_isolated_slices(monkeypatch):
    monkeypatch.delenv("WEBARENA_RAINBOND_SMOKE_WIKIPEDIA_URL", raising=False)
    slices = [{site: f"http://slot-{i}-{site}.example" for site in SITES} for i in (1, 2)]
    monkeypatch.setenv("WEBARENA_RAINBOND_SMOKE_URLS", json.dumps(slices))
    assert len(_configured_urls()) == 8


def test_smoke_configuration_includes_one_shared_wikipedia(monkeypatch):
    sites = {site: f"http://{site}.example" for site in SITES}
    monkeypatch.setenv("WEBARENA_RAINBOND_SMOKE_URLS", json.dumps(sites))
    monkeypatch.setenv("WEBARENA_RAINBOND_SMOKE_WIKIPEDIA_URL", "https://wiki.example/")
    assert _configured_urls()["shared/wikipedia"] == "https://wiki.example" + WIKIPEDIA_ARTICLE


def test_smoke_configuration_rejects_shared_mutable_sites(monkeypatch):
    sites = {site: f"http://{site}.example" for site in SITES}
    monkeypatch.setenv("WEBARENA_RAINBOND_SMOKE_URLS", json.dumps([sites, sites]))
    with pytest.raises(AssertionError, match="distinct URLs"):
        _configured_urls()
