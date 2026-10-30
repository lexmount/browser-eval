from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from harness.environments.browser import (
    ENGINE_PORTS,
    cleanup_browser_runtime,
    external_websocket_url,
    published_endpoint,
)
from harness.trajectory_replay import diagnostics, runner
from harness.trajectory_replay.runner import _http_origin, _prepare_page
from harness.trajectory_replay.suite import BrowserTask, TaskSuite


def test_browser_engines_publish_their_external_cdp_ports():
    assert ENGINE_PORTS == {"chromium": 9223, "moli": 9222}


def test_empty_tool_trace_cannot_pass_process_validation(monkeypatch):
    task = BrowserTask("empty", "Empty", (), (), (), "simple", {}, {"expected_actions": []})
    page = MagicMock()
    resources = MagicMock()
    sampler = MagicMock()
    monkeypatch.setattr(runner, "ResourceSampler", lambda *args, **kwargs: sampler)
    monkeypatch.setattr(runner, "execute_steps", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_resource_result", lambda *args: {})

    result = runner._run_task("chromium", page, set(), resources, task, 1, 100)

    assert result["process_status"] == "fail"
    assert result["process_validation"]["all_calls_passed"] is False


@pytest.mark.parametrize("generate_report", [False, True])
@pytest.mark.parametrize("unexpected_diagnostic_error", [False, True])
@pytest.mark.parametrize("with_smoke", [False, True])
def test_run_saves_evidence_before_diagnostics_and_cleans_up(
    tmp_path, monkeypatch, unexpected_diagnostic_error, generate_report, with_smoke
):
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}")
    import shutil

    from harness.trajectory_replay.report_inputs import replay_fixture_root
    shutil.copytree(replay_fixture_root(), suite_path.parent / "fixtures")
    (suite_path.parent / "fixtures/core.html").write_text("Original historical core")
    (suite_path.parent / "fixtures/nested").mkdir()
    (suite_path.parent / "fixtures/nested/custom.js").write_text("window.historical = true;")
    smoke_path = tmp_path / "selected-smoke.json"
    smoke_raw = b'{"results":[{"status":"fail"}],"executed_at":"2026-09-01"}'
    smoke_path.write_bytes(smoke_raw)
    # A neighboring success must not be selected instead of the explicit evidence.
    (suite_path.parent / "agent_smoke.json").write_text('{"results":[{"status":"pass"}]}')
    output = tmp_path / "artifacts"
    task = BrowserTask(
        "task", "Task", ({"action": "navigate", "url": "{fixture_origin}/core.html"},),
        (), (), "simple", {}, {},
    )
    monkeypatch.setattr(runner, "_start_fixture_server", lambda root: (
        MagicMock(), MagicMock(), "http://fixture.test",
    ))
    task.ground_truth["expected_outputs"] = {}
    monkeypatch.setattr(runner, "load_suite", lambda _: TaskSuite("test", (task,)))
    client = MagicMock()
    playwright = MagicMock()
    monkeypatch.setattr(runner.docker, "from_env", lambda: client)
    monkeypatch.setattr(
        runner, "sync_playwright", lambda: SimpleNamespace(start=lambda: playwright)
    )
    monkeypatch.setattr(runner, "start_browser_container", lambda *a, **k: MagicMock())
    monkeypatch.setattr(runner, "published_endpoint", lambda *a, **k: "http://test.invalid")
    monkeypatch.setattr(runner, "wait_cdp", lambda *a, **k: "ws://test.invalid")
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
    report_path = tmp_path / "report.html"
    report = MagicMock(
        side_effect=lambda *a, **k: report_path.write_text("<html>Saved results</html>")
    )
    monkeypatch.setattr(runner, "write_latest_report", report)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://test.invalid")

    def verify_artifacts():
        attempts = (output / "attempts.jsonl").read_bytes()
        summary_bytes = (output / "summary.json").read_bytes()
        manifest = json.loads((output / "manifest.json").read_text())
        if with_smoke:
            assert manifest["agent_smoke"]["results"] == [{"status": "fail"}]
            assert manifest["agent_smoke"]["provenance"]["source_sha256"] == (
                hashlib.sha256(smoke_raw).hexdigest()
            )
        else:
            assert "agent_smoke" not in manifest
        fixture_root = suite_path.parent / "fixtures"
        assert len(manifest["fixture_files"]) == 9
        assert "nested/custom.js" in manifest["fixture_files"]
        for name, digest in manifest["fixture_files"].items():
            assert digest == hashlib.sha256((fixture_root / name).read_bytes()).hexdigest()
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
                agent_smoke_path=smoke_path if with_smoke else None,
                max_retries=0,
                llm_diagnostics=True,
                generate_report=generate_report,
            )
        assert verify_artifacts() == baseline
    else:
        monkeypatch.setattr(diagnostics, "add_llm_diagnoses", diagnostic_failure)
        result = runner.run_evaluation(
            suite_path=suite_path,
            output_dir=output,
            reports_dir=tmp_path,
                agent_smoke_path=smoke_path if with_smoke else None,
            max_retries=0,
            llm_diagnostics=True,
            generate_report=generate_report,
        )
        assert verify_artifacts() == result
        assert result["score"] == baseline["score"]
        assert result["failure_diagnostics"]["llm"]["error_type"] == "URLError"
        assert len(result["failure_diagnostics"]["rule_engine"]["groups"]) == 1
        if generate_report:
            report.assert_called_once()
            assert report_path.is_file()
        else:
            report.assert_not_called()
            assert not report_path.exists()
            # Rebuild from the persisted attempts through the public command;
            # browser/provider mocks are not called again.
            import importlib.util
            root = Path(__file__).resolve().parents[2]
            script = root / 'examples/trajectory_replay/rebuild_report.py'
            spec = importlib.util.spec_from_file_location('explicit_replay_report', script)
            rebuild = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rebuild)
            monkeypatch.setattr(rebuild, 'load_suite', lambda _: TaskSuite('test', (task,)))
            monkeypatch.setattr(rebuild, 'summarize_attempts', lambda *a, **k: result)
            monkeypatch.setattr(rebuild, 'diagnose_summary', lambda *a, **k: None)
            monkeypatch.setattr(rebuild, 'write_latest_report', report)
            evidence = tmp_path / 'qualification.json'
            evidence.write_text(json.dumps({
                'dataset_sha256': hashlib.sha256(suite_path.read_bytes()).hexdigest(),
                'results': [{'task_id': 'task', 'status': 'pass', 'observed': {}}],
                'automatic_browser_replay': {'status': 'pass', 'passed': 1},
            }))
            monkeypatch.setattr(sys, 'argv', ['rebuild_report', '--artifacts', str(output),
                '--suite', str(suite_path), '--reports', str(tmp_path),
                '--ground-truth-evidence', str(evidence)])
            rebuild.main()
            report.assert_called_once()
            assert report_path.is_file()
    playwright.stop.assert_called_once()
    client.networks.create.return_value.remove.assert_called()


def test_moli_image_installs_its_verified_dynamic_font_dependency():
    dockerfile = Path("examples/trajectory_replay/docker/moli.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "fontconfig" in dockerfile


def test_external_websocket_url_rewrites_container_advertised_host():
    assert (
        external_websocket_url(
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
        published_endpoint(Container(), engine="chromium", cdp_port=9223)


def test_published_endpoint_uses_loopback_host_binding():
    class Container:
        attrs = {
            "NetworkSettings": {
                "Ports": {"9222/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32768"}]}
            }
        }

    assert (
        published_endpoint(Container(), engine="moli", cdp_port=9222) == "http://127.0.0.1:32768"
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

    cleanup_browser_runtime(
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


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS system aliases')
@pytest.mark.parametrize('alias', ['/tmp', '/var'])
def test_replay_accepts_system_alias_but_rejects_user_links(tmp_path, monkeypatch, alias):
    from harness.trajectory_replay.report_inputs import verified_dataset_path

    # pytest's temporary root is inside /private/var on macOS.
    canonical = Path('/private') / Path(alias).name
    with tempfile.TemporaryDirectory(dir=canonical if alias == '/tmp' else tmp_path) as directory:
        root = Path(directory)
        aliased = Path(alias) / root.relative_to(canonical)
        dataset = root / 'dataset.json'
        dataset.write_bytes(b'{}')
        digest = hashlib.sha256(b'{}').hexdigest()
        assert verified_dataset_path(aliased / 'dataset.json', digest).read_bytes() == b'{}'
        seen = []
        def stop_at_suite(path):
            seen.append(path)
            raise RuntimeError('suite reached')
        monkeypatch.setattr(runner, 'load_suite', stop_at_suite)
        with pytest.raises(RuntimeError, match='suite reached'):
            runner.run_evaluation(suite_path=dataset, output_dir=aliased / 'new',
                                  reports_dir=root / 'report')
        assert seen == [dataset]
        link = root / 'user-link'
        link.symlink_to(root, target_is_directory=True)
        with pytest.raises(ValueError, match='symlink'):
            verified_dataset_path(link / 'dataset.json', digest)
        with pytest.raises(ValueError, match='symlink'):
            runner.run_evaluation(suite_path=dataset, output_dir=link / 'new',
                                  reports_dir=root / 'report')
