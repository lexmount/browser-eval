"""Byte-level snapshots of every report track's synthetic example build."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.reporting import versions
from harness.trajectory_replay.public_webarena.publication import stage_report
from harness.web_fetch import call_boundary, runner
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.report import generate_wcxb, generate_webmainbench
from harness.web_fetch.report_build import build_report as build_webfetch_report
from harness.web_search.example import example as build_web_search_example
from lexbench_webfetch.models import EvalCase
from tests.browser_use.test_report import build_browser_use_fixture
from tests.golden_snapshots import check_golden
from tests.public_webarena_fixture import write_report_fixture
from tests.test_webmainbench_full_report import render_full_report
from tests.test_webmainbench_report import sample_report_inputs
from tests.trajectory_replay.test_webarena_report import build_replay_fixture
from tests.wcxb_fixture import create_report_inputs

# Reports record the revision and source hash of the builder. Both change with
# every commit, which is what the snapshots must not depend on.
PINNED_BUILDER = {"version": "0.0.0-golden", "revision": None,
                  "source_sha256": "0" * 64, "modified": False}


@pytest.fixture(autouse=True)
def pinned_builder_identity(monkeypatch):
    monkeypatch.setattr(versions, "software_identity", lambda: dict(PINNED_BUILDER))
    monkeypatch.setattr(runner, "software_identity", lambda: dict(PINNED_BUILDER))


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_browser_use_report(tmp_path: Path, monkeypatch, outcome: str) -> None:
    output, evidence, _ = build_browser_use_fixture(
        tmp_path, monkeypatch, "en", failed=outcome == "fail"
    )
    check_golden(
        f"browser-use-{outcome}", output, tmp_path, path_inputs=[evidence / "attempts.jsonl"]
    )


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_trajectory_replay_report(tmp_path: Path, monkeypatch, outcome: str) -> None:
    output, *_, files = build_replay_fixture(tmp_path, monkeypatch, failed=outcome == "fail")
    check_golden(
        f"trajectory-replay-{outcome}", output, tmp_path, path_inputs=[files["results.json"]]
    )


def test_public_webarena_report(tmp_path: Path) -> None:
    source, _ = write_report_fixture(tmp_path)
    stage_report(source, tmp_path / "staged")
    check_golden("public-webarena", tmp_path / "staged", tmp_path)


def test_web_search_report(tmp_path: Path) -> None:
    build_web_search_example(tmp_path)
    check_golden("web-search", tmp_path / "report", tmp_path)


def test_wcxb_report(tmp_path: Path) -> None:
    inputs = create_report_inputs(tmp_path, english_source=True)
    generate_wcxb(*inputs, include_english=True)
    check_golden("wcxb", inputs[0], tmp_path)


def test_webmainbench_sample_report(tmp_path: Path) -> None:
    output = tmp_path / "report"
    output.mkdir()
    dataset, records, summaries, manifests = sample_report_inputs(output)
    review = {"status": "passed", "reviewed_at": "2026-09-14T00:00:00Z", "checks": []}
    generate_webmainbench(
        output, dataset, records, summaries, manifests, review, include_english=True
    )
    check_golden("webmainbench-sample", output, tmp_path)


def test_webmainbench_full_report(tmp_path: Path) -> None:
    render_full_report(tmp_path, "method-binding.json")
    check_golden("webmainbench-full", tmp_path, tmp_path)


def test_web_fetch_report(tmp_path: Path, monkeypatch) -> None:
    # Timestamps, run IDs and timings come from the clock, and the execution
    # manifest records the host; pin both.
    clock = SimpleNamespace(monotonic=lambda: 0.0, perf_counter=lambda: 0.0, sleep=lambda _: None)
    monkeypatch.setattr(runner, "time", clock)
    monkeypatch.setattr(call_boundary, "time", clock)
    monkeypatch.setattr(runner, "utc_now", lambda: "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(runner, "platform", SimpleNamespace(platform=lambda: "golden-platform"))
    monkeypatch.setattr(runner.socket, "gethostname", lambda: "golden-host")
    monkeypatch.setattr(runner.sys, "version", "3.11.0 (golden)")
    cases = [
        {
            "id": case_id,
            "name": case_id,
            "url": "https://example.com/" + case_id,
            "category": "static",
            "track": "web",
            "task_prompt": "Read expected content",
            "expected_any": ["Expected"],
        }
        for case_id in ("ok", "fail")
    ]
    dataset = {
        "schema_version": 1, "id": "golden", "name": "Golden", "tool": "web_fetch", "cases": cases
    }
    run = tmp_path / "run"
    runner.run_evaluation(
        output_dir=run,
        dataset_meta=dataset,
        dataset_path=tmp_path / "dataset.json",
        cases=[EvalCase(**case) for case in cases],
        providers=[FixtureProvider("fixture", {"type": "fixture", "fail_cases": ["fail"]})],
        provider_config_path=tmp_path / "providers.json",
        runs=1,
        timeout=1,
        delay=0,
    )
    build_webfetch_report(run, tmp_path / "report", language="en")
    check_golden("web-fetch", tmp_path / "report", tmp_path)
