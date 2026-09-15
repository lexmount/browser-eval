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
    _capture_final_page_evidence,
    _cleanup_runtime,
    _environment,
    _environment_failure_row,
    _external_websocket_url,
    _http_origin,
    _prepare_page,
    _published_endpoint,
    _start_browser_container,
)
from harness.browser_use.suite import BrowserTask, TaskSuite


def test_authoring_runtime_caps_ad_hoc_repair_target_waits():
    task = BrowserTask(
        task_id="webarena-1",
        title="Task",
        tool_calls=(
            {"action": "click", "selector": "#target", "timeout_ms": 30_000},
            {
                "action": "click",
                "target": {"by": "role", "role": "button", "name": "Open"},
            },
        ),
        assertions=(),
        tags=("retrieve",),
        difficulty="simple",
        ground_truth={},
        source={},
        trajectory={"schema": "lex_browser_trajectory/1", "actions": []},
    )

    bounded = runner._bounded_authoring_task(task)

    assert bounded.tool_calls[0]["timeout_ms"] == 5_000
    assert bounded.tool_calls[1]["timeout_ms"] == 5_000
    assert bounded.tool_calls[1]["target_timeout_ms"] == 5_000
    assert bounded.trajectory["actions"] == list(bounded.tool_calls)


def test_environment_prepare_failure_is_a_fail_closed_physical_attempt(tmp_path):
    task = BrowserTask(
        task_id="webarena-809",
        title="Environment failure",
        tool_calls=({"action": "click", "selector": "#target"},),
        assertions=(),
        tags=("mutate",),
        difficulty="simple",
        ground_truth={"expected_actions": ["click"]},
        source={},
        evaluator={"kind": "webarena_verified"},
        environment={"kind": "webarena_verified", "sites": ["gitlab"]},
    )

    row = _environment_failure_row(
        "chromium",
        task,
        1,
        tmp_path / "attempt-1",
        is_retry=False,
        duration_ms=123.4,
        error="reset failed",
    )

    assert row["status"] == "fail"
    assert row["process_status"] == "fail"
    assert row["result_status"] == "fail"
    assert row["evidence_status"] == "fail"
    assert row["termination_reason"] == "environment_prepare_failed"
    assert row["trace"][0]["action"] == "prepare_environment"
    assert row["process_validation"] == {
        "expected_actions": ["click"],
        "actual_actions": [],
        "all_calls_passed": False,
        "passed": False,
    }
    assert (tmp_path / "attempt-1").is_dir()


def test_remaining_task_budget_is_shared_across_attempt_phases(monkeypatch):
    readings = iter((10.0, 10.025, 10.100))
    monkeypatch.setattr(runner.time, "perf_counter", lambda: next(readings))

    assert runner._remaining_task_budget_ms(10.125, phase="reset") == 125
    assert runner._remaining_task_budget_ms(10.125, phase="prepare") == 99
    assert runner._remaining_task_budget_ms(10.125, phase="execution") == 25


def test_remaining_task_budget_fails_closed_when_exhausted(monkeypatch):
    monkeypatch.setattr(runner.time, "perf_counter", lambda: 20.0)

    with pytest.raises(TimeoutError, match="before evaluator"):
        runner._remaining_task_budget_ms(20.0, phase="evaluator")


def test_webarena_site_reuse_is_read_only_and_site_exact():
    read_task = BrowserTask(
        task_id="webarena-1",
        title="Read",
        tool_calls=(),
        assertions=(),
        tags=("retrieve",),
        difficulty="simple",
        ground_truth={},
        source={},
        evaluator={"kind": "webarena_verified"},
        environment={"kind": "webarena_verified", "sites": ["shopping"]},
    )
    mutate_task = BrowserTask(
        task_id="webarena-2",
        title="Write",
        tool_calls=(),
        assertions=(),
        tags=("mutate",),
        difficulty="simple",
        ground_truth={},
        source={},
        evaluator={"kind": "webarena_verified"},
        environment={"kind": "webarena_verified", "sites": ["shopping"]},
    )

    assert runner._may_reuse_webarena_site(read_task, {"shopping"})
    assert not runner._may_reuse_webarena_site(read_task, {"gitlab"})
    assert not runner._may_reuse_webarena_site(mutate_task, {"shopping"})


def test_webarena_site_reuse_state_is_isolated_per_engine():
    states = runner._engine_site_state(("chromium", "moli"))

    states["chromium"].add("shopping")

    assert states == {"chromium": {"shopping"}, "moli": set()}


def test_browser_engines_publish_their_external_cdp_ports():
    assert ENGINE_PORTS == {"chromium": 9223, "moli": 9222}


def test_browser_container_may_omit_cpu_limit_only_when_explicit():
    client = MagicMock()

    _start_browser_container(
        client,
        engine="chromium",
        run_id="diagnostic",
        cpus=None,
        memory="1g",
        network="test-network",
    )

    kwargs = client.containers.run.call_args.kwargs
    assert "nano_cpus" not in kwargs
    assert kwargs["mem_limit"] == "1g"


def test_browser_container_rejects_nonpositive_explicit_cpu_limit():
    with pytest.raises(ValueError, match="cpus must be positive"):
        _start_browser_container(
            MagicMock(),
            engine="chromium",
            run_id="invalid",
            cpus=0,
            memory="1g",
            network="test-network",
        )


def test_measured_run_rejects_unlimited_cpu(tmp_path):
    with pytest.raises(ValueError, match="cpus must be positive for measured runs"):
        runner.run_evaluation(
            suite_path=tmp_path / "not-read.json",
            output_dir=tmp_path / "output",
            reports_dir=tmp_path / "reports",
            cpus=0,
        )


def test_trajectory_authoring_requires_one_chromium_attempt(tmp_path):
    with pytest.raises(ValueError, match="one Chromium attempt"):
        runner.run_evaluation(
            suite_path=tmp_path / "not-read.json",
            output_dir=tmp_path / "output",
            reports_dir=tmp_path / "reports",
            trajectory_authoring_run=True,
            engines=("chromium", "moli"),
            max_retries=0,
        )


def test_trajectory_authoring_and_qualification_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        runner.run_evaluation(
            suite_path=tmp_path / "not-read.json",
            output_dir=tmp_path / "output",
            reports_dir=tmp_path / "reports",
            trajectory_authoring_run=True,
            qualification_run=True,
            engines=("chromium",),
            max_retries=0,
        )


def test_trajectory_authoring_rejects_state_mutation_tasks_before_docker(
    tmp_path, monkeypatch
):
    task = BrowserTask(
        task_id="webarena-1",
        title="Mutation",
        tool_calls=({"name": "click", "arguments": {}},),
        assertions=(),
        tags=("state_mutation",),
        difficulty="simple",
        ground_truth={"qualification": {"status": "pending"}},
        source={},
        environment={"kind": "webarena_verified", "sites": ["shopping"]},
    )
    monkeypatch.setattr(runner, "load_suite", lambda _: TaskSuite("test", (task,)))
    client = MagicMock()
    monkeypatch.setattr(runner.docker, "from_env", lambda: client)

    with pytest.raises(
        ValueError, match="permits non-mutating retrieve/navigate tasks only: webarena-1"
    ):
        runner.run_evaluation(
            suite_path=tmp_path / "suite.json",
            output_dir=tmp_path / "output",
            reports_dir=tmp_path / "reports",
            trajectory_authoring_run=True,
            engines=("chromium",),
            max_retries=0,
            webarena_config_path=tmp_path / "webarena.json",
        )

    client.networks.create.assert_not_called()


def test_measured_run_rejects_missing_cgroup_controls_before_creating_output(
    tmp_path, monkeypatch
):
    task = BrowserTask("task", "Task", (), (), (), "simple", {}, {})
    monkeypatch.setattr(runner, "load_suite", lambda _: TaskSuite("test", (task,)))
    client = MagicMock()
    client.info.return_value = {
        "CpuCfsQuota": False,
        "CpuCfsPeriod": False,
        "MemoryLimit": True,
        "PidsLimit": True,
    }
    monkeypatch.setattr(runner.docker, "from_env", lambda: client)
    output = tmp_path / "output"

    with pytest.raises(RuntimeError, match="CPU CFS quota, CPU CFS period"):
        runner.run_evaluation(
            suite_path=tmp_path / "suite.json",
            output_dir=output,
            reports_dir=tmp_path / "reports",
        )

    assert not output.exists()
    client.networks.create.assert_not_called()


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
    client.info.return_value = {"OSType": "linux", "CgroupVersion": "2"}
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
    task_selection = {
        "kind": "native_scope_batch",
        "scope_sha256": "a" * 64,
        "batch_id": "shopping",
        "shard_count": 2,
        "shard_index": 1,
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
        assert manifest["protocol"]["task_selection"] == task_selection
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
                task_selection=task_selection,
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
            task_selection=task_selection,
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
    assert "--mount=type=bind,source=.,target=/mnt/build-context,ro" in dockerfile
    assert "sha256sum -c -" in dockerfile


def test_browser_images_expose_auditable_debian_mirror_overrides():
    for name in ("chromium.Dockerfile", "moli.Dockerfile"):
        dockerfile = Path("examples/browser_use/docker", name).read_text(encoding="utf-8")
        assert "ARG DEBIAN_MIRROR=http://deb.debian.org/debian" in dockerfile
        assert "ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security" in dockerfile
        assert "lex.browser-use-eval.debian-mirror" in dockerfile
        assert "lex.browser-use-eval.debian-security-mirror" in dockerfile


def test_environment_records_browser_image_build_source_labels():
    image = SimpleNamespace(
        id="sha256:image",
        tags=["lex-browser-use-eval-chromium:local"],
        attrs={
            "Config": {
                "Labels": {
                    "lex.browser-use-eval.debian-mirror": "http://mirror.example/debian",
                    "unrelated": "excluded",
                }
            }
        },
    )
    container = SimpleNamespace(
        image=image,
        exec_run=lambda _command: SimpleNamespace(
            output=b"Chromium 152.0.7977.82", exit_code=0
        ),
    )
    client = SimpleNamespace(
        info=lambda: {
            "OperatingSystem": "Linux",
            "Architecture": "x86_64",
            "NCPU": 8,
            "MemTotal": 16_000_000_000,
            "CgroupVersion": "2",
        },
        version=lambda: {"Version": "29.4.3"},
    )

    result = _environment(client, {"chromium": container})

    assert result["images"]["chromium"]["build_source"] == {
        "lex.browser-use-eval.debian-mirror": "http://mirror.example/debian"
    }
    assert result["browser_versions"]["chromium"] == "Chromium 152.0.7977.82"


def test_final_page_evidence_binds_screenshot_and_visible_page_state(tmp_path):
    screenshot = b"png-bytes"
    page = MagicMock()
    def write_screenshot(*, path, full_page, timeout):
        assert full_page is False
        assert timeout == 5_000
        Path(path).write_bytes(screenshot)
        return screenshot

    page.screenshot.side_effect = write_screenshot
    page.url = "https://example.test/result"
    page.title.return_value = "Result"
    page.context.pages = [page]
    page.is_closed.return_value = False
    page.evaluate.side_effect = [
        {
            "visibleText": "Visible result",
            "interactiveElements": [
                {
                    "index": 0,
                    "tag": "button",
                    "role": None,
                    "text": "Save",
                    "id": "save",
                    "name": None,
                    "ariaLabel": None,
                    "placeholder": None,
                    "title": None,
                    "href": None,
                    "type": "submit",
                    "checked": None,
                    "selected": None,
                    "disabled": False,
                    "visible": True,
                }
            ],
        },
        {"width": 1280, "height": 720},
    ]

    result = _capture_final_page_evidence(page, tmp_path)

    assert result["status"] == "complete"
    assert result["screenshot"]["sha256"] == hashlib.sha256(screenshot).hexdigest()
    assert json.loads((tmp_path / "final-page.json").read_text()) == {
        "url": "https://example.test/result",
        "title": "Result",
        "visible_text": "Visible result",
        "interactive_elements": [
            {
                "index": 0,
                "tag": "button",
                "role": None,
                "text": "Save",
                "id": "save",
                "name": None,
                "ariaLabel": None,
                "placeholder": None,
                "title": None,
                "href": None,
                "type": "submit",
                "checked": None,
                "selected": None,
                "disabled": False,
                "visible": True,
            }
        ],
        "open_pages": [{"url": "https://example.test/result", "title": "Result"}],
        "viewport": {"width": 1280, "height": 720},
        "capture_errors": [],
    }


def test_final_page_evidence_retries_navigation_races_and_writes_partial(tmp_path):
    page = MagicMock()
    page.context.pages = [page]
    page.is_closed.return_value = False
    page.url = "https://example.test/loading"
    page.wait_for_load_state.side_effect = TimeoutError("still loading")
    page.screenshot.side_effect = TimeoutError("context replaced")
    page.evaluate.side_effect = RuntimeError("execution context destroyed")
    page.title.side_effect = RuntimeError("execution context destroyed")

    result = _capture_final_page_evidence(page, tmp_path)

    assert result["status"] == "partial"
    assert result["screenshot"] is None
    assert result["page"]["path"] == "final-page.json"
    payload = json.loads((tmp_path / "final-page.json").read_text())
    assert payload["url"] == "https://example.test/loading"
    assert payload["visible_text"] == ""
    assert payload["interactive_elements"] == []
    assert {row["stage"] for row in payload["capture_errors"]} == {
        "load_state",
        "screenshot",
        "dom",
        "title",
        "viewport",
    }


@pytest.mark.parametrize(
    ("status", "qualification_run"),
    ((None, False), ("pending", False), ("eligible", True), ("skipped", True)),
)
def test_webarena_run_mode_fails_closed_on_qualification_state(
    tmp_path, monkeypatch, status, qualification_run
):
    qualification = {} if status is None else {"status": status}
    task = {
        "id": "webarena-1",
        "title": "Candidate",
        "difficulty": "simple",
        "tags": ["webarena"],
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "actions": [
                {"action": "navigate", "url": "https://example.com", "save_as": "page"}
            ],
        },
        "assertions": [],
        "evaluator": {"kind": "webarena_verified", "task_id": 1, "revision": 1},
        "environment": {
            "kind": "webarena_verified",
            "sites": ["reddit"],
            "start_urls": ["__REDDIT__"],
        },
        "ground_truth": {
            "expected_actions": ["navigate"],
            "qualification": qualification,
        },
        "source": {"url": "https://example.com/source"},
    }
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"schema": "lex_browser_use_tasks/4", "tasks": [task]}))
    docker_call = MagicMock()
    monkeypatch.setattr(runner.docker, "from_env", docker_call)

    with pytest.raises(ValueError, match="invalid qualification states"):
        runner.run_evaluation(
            suite_path=path,
            output_dir=tmp_path / "output",
            reports_dir=tmp_path / "reports",
            webarena_config_path=tmp_path / "config.json",
            engines=("chromium",),
            max_retries=0,
            qualification_run=qualification_run,
            qualification_pass_index=1 if qualification_run else None,
            qualification_required_passes=2 if qualification_run else None,
        )
    docker_call.assert_not_called()


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
        pages = []

        def new_page(self):
            calls.append(("new_page",))
            return replacement

    class OldPage:
        context = Context()
        url = "about:blank"

        def close(self, **kwargs):
            calls.append(("close", kwargs))

    origins = {"https://example.com"}
    OldPage.context.pages = [OldPage()]
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
