from __future__ import annotations

from pathlib import Path

from harness.browser_use import runner
from harness.browser_use.runner import (
    ENGINE_PORTS,
    _external_websocket_url,
    _http_origin,
    _prepare_page,
)


def test_browser_engines_publish_their_external_cdp_ports():
    assert ENGINE_PORTS == {"chromium": 9223, "moli": 9222}


def test_moli_image_installs_its_verified_dynamic_font_dependency():
    dockerfile = Path("examples/browser_use/docker/moli.Dockerfile").read_text(encoding="utf-8")

    assert "fontconfig" in dockerfile


def test_external_websocket_url_rewrites_container_advertised_host():
    assert (
        _external_websocket_url(
            "http://127.0.0.1:32776",
            "ws://0.0.0.0:9222/devtools/browser/moli-browser",
        )
        == "ws://127.0.0.1:32776/devtools/browser/moli-browser"
    )


def test_http_origin_keeps_only_scheme_and_authority():
    assert _http_origin("https://example.com:8443/a?q=1") == "https://example.com:8443"
    assert _http_origin("about:blank") is None


def test_prepare_page_keeps_context_but_replaces_renderer_page(monkeypatch):
    calls = []

    class Replacement:
        def wait_for_timeout(self, timeout):
            calls.append(("settle", timeout))

    replacement = Replacement()

    class Context:
        def new_page(self):
            calls.append(("new_page",))
            return replacement

    class OldPage:
        context = Context()

        def close(self, **kwargs):
            calls.append(("close", kwargs))

    origins = {"https://example.com"}
    monkeypatch.setattr(
        runner,
        "_reset_state",
        lambda page, visited: calls.append(("reset", set(visited))),
    )

    result = _prepare_page(OldPage(), origins, settle_ms=1500)

    assert result is replacement
    assert origins == set()
    assert calls == [
        ("reset", {"https://example.com"}),
        ("close", {"run_before_unload": False}),
        ("new_page",),
        ("settle", 1500),
    ]
