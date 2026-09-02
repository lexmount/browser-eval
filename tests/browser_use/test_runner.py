from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from harness.browser_use import diagnostics, runner
from harness.browser_use.runner import (
    ENGINE_PORTS,
    _cleanup_runtime,
    _external_websocket_url,
    _http_origin,
    _prepare_page,
    _published_endpoint,
)
from harness.browser_use.suite import BrowserTask, TaskSuite


def test_browser_engines_publish_their_external_cdp_ports():
    assert ENGINE_PORTS == {"chromium": 9223, "moli": 9222}


@pytest.mark.parametrize("unexpected_diagnostic_error", [False, True])
def test_run_saves_evidence_before_diagnostics_and_cleans_up(
    tmp_path, monkeypatch, unexpected_diagnostic_error
):
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}")
    output = tmp_path / "artifacts"
    task = BrowserTask("task", "Task", (), (), (), "simple", {}, {})
    monkeypatch.setattr(runner, "load_suite", lambda _: TaskSuite("test", (task,)))
    client = MagicMock()
    playwright = MagicMock()
    monkeypatch.setattr(runner.docker, "from_env", lambda: client)
    monkeypatch.setattr(
        runner, "sync_playwright", lambda: SimpleNamespace(start=lambda: playwright)
    )
    monkeypatch.setattr(runner, "_start_browser_container", lambda *a, **k: MagicMock())
    monkeypatch.setattr(runner, "_published_endpoint", lambda *a, **k: "http://test.invalid")
    monkeypatch.setattr(runner, "_wait_cdp", lambda *a, **k: "ws://test.invalid")
    monkeypatch.setattr(runner, "DockerStatsResources", lambda _: None)
    monkeypatch.setattr(runner, "_reset_state", lambda *a: None)
    monkeypatch.setattr(runner, "_prepare_page", lambda page, *a, **k: page)
    monkeypatch.setattr(runner, "_environment", lambda *a: {})
    monkeypatch.setattr(
        runner,
        "_run_task",
        lambda engine, *a, **k: {
            "engine": engine,
            "task_id": "task",
            "attempt": 1,
            "status": "fail",
            "duration_ms": 1,
            "error": "TimeoutError",
        },
    )
    baseline = {
        "score": {"moli": 0},
        "by_task": {"task": {"failures": {"moli": [{"error": "TimeoutError"}]}}},
    }
    monkeypatch.setattr(runner, "summarize_attempts", lambda *a, **k: dict(baseline))
    report = MagicMock()
    monkeypatch.setattr(runner, "write_latest_report", report)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://test.invalid")

    def verify_artifacts():
        attempts = (output / "attempts.jsonl").read_bytes()
        summary_bytes = (output / "summary.json").read_bytes()
        manifest = json.loads((output / "manifest.json").read_text())
        assert len(attempts.splitlines()) == 2
        assert (
            manifest["artifact_provenance"]["attempts_sha256"]
            == hashlib.sha256(attempts).hexdigest()
        )
        assert (
            manifest["artifact_provenance"]["summary_sha256"]
            == hashlib.sha256(summary_bytes).hexdigest()
        )
        return json.loads(summary_bytes)

    def diagnostic_failure(*a, **k):
        assert verify_artifacts() == baseline
        raise URLError("diagnostic service unavailable")

    if unexpected_diagnostic_error:
        monkeypatch.setattr(runner, "diagnose_summary", diagnostic_failure)
        with pytest.raises(URLError):
            runner.run_evaluation(
                suite_path=suite_path,
                output_dir=output,
                reports_dir=tmp_path,
                max_retries=0,
                llm_diagnostics=True,
            )
        assert verify_artifacts() == baseline
    else:
        monkeypatch.setattr(diagnostics, "add_llm_diagnoses", diagnostic_failure)
        result = runner.run_evaluation(
            suite_path=suite_path,
            output_dir=output,
            reports_dir=tmp_path,
            max_retries=0,
            llm_diagnostics=True,
        )
        assert verify_artifacts() == result
        assert result["score"] == baseline["score"]
        assert result["failure_diagnostics"]["llm"]["error_type"] == "URLError"
        assert len(result["failure_diagnostics"]["rule_engine"]["groups"]) == 1
        report.assert_called_once()
    playwright.stop.assert_called_once()
    client.networks.create.return_value.remove.assert_called()


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


def test_published_endpoint_handles_missing_port_binding():
    class Container:
        attrs = {"NetworkSettings": {"Ports": {}}}

    with pytest.raises(RuntimeError, match="chromium: Docker did not publish CDP port"):
        _published_endpoint(Container(), engine="chromium", cdp_port=9223)


def test_published_endpoint_uses_loopback_host_binding():
    class Container:
        attrs = {
            "NetworkSettings": {
                "Ports": {"9222/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32768"}]}
            }
        }

    assert (
        _published_endpoint(Container(), engine="moli", cdp_port=9222) == "http://127.0.0.1:32768"
    )


def test_cleanup_continues_after_playwright_stop_failure():
    calls = []

    class Browser:
        def close(self):
            calls.append("browser.close")

    class Playwright:
        def stop(self):
            calls.append("playwright.stop")
            raise RuntimeError("already stopped")

    class Container:
        def remove(self, *, force):
            calls.append(("container.remove", force))

    class Network:
        def remove(self):
            calls.append("network.remove")

    _cleanup_runtime(
        {"chromium": Browser()},
        Playwright(),
        {"chromium": Container()},
        {"chromium": Network()},
    )

    assert calls == [
        "browser.close",
        "playwright.stop",
        ("container.remove", True),
        "network.remove",
    ]


def test_prepare_page_keeps_context_but_replaces_renderer_page(monkeypatch):
    calls = []

    class Replacement:
        def set_viewport_size(self, size):
            calls.append(("viewport", size))

        def evaluate(self, expression):
            return {"width": 1280, "height": 900}

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
        ("viewport", {"width": 1280, "height": 900}),
        ("settle", 1500),
    ]
