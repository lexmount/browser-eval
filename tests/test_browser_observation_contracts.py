"""Capture contracts preserve declared scope and reject stale observations."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from harness.web_fetch import browser_observations as capture


def case_with(rule=None):
    return {
        "id": "new-task",
        "url": "https://example.com/article",
        "source_verification": {"browser_observation": rule} if rule is not None else {},
    }


@pytest.mark.parametrize(
    "rule",
    [
        {"minimum": 0},
        {"minimum": True},
        {"selector": []},
        {"content_selector": ""},
        {"text_mode": "best"},
        {"unknown": 1},
        [],
    ],
)
def test_invalid_policy_rejected_before_browser_calls(rule, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid policy reached browser capture")

    monkeypatch.setattr(capture, "capture_with_retries", unexpected)
    with pytest.raises(ValueError, match="policy"):
        capture.capture_batch([case_with(rule)], timeout_ms=1000, attempts=1)


def test_historical_task_name_does_not_select_policy_for_new_definition():
    case = {**case_with(), "id": "javascript-spa"}
    with pytest.raises(ValueError, match="Historical observation contract changed"):
        capture.capture_policy(case)
    case["source_verification"]["browser_observation"] = {"selector": "main"}
    assert capture.capture_policy(case).selector == "main"


def test_registered_historical_policies_match_frozen_definitions():
    from pathlib import Path

    from tests.conftest import read_frozen_dataset_bytes

    dataset = json.loads(read_frozen_dataset_bytes(Path("evaluation/dataset.json")))
    cases = {row["id"]: row for row in dataset["cases"]}
    policy = capture.capture_policy(cases["javascript-spa"])
    assert (policy.selector, policy.minimum, policy.content_selector, policy.text_mode) == (
        ".quote",
        10,
        "body",
        "visible",
    )
    policy = capture.capture_policy(cases["live-025-api-reference"])
    assert (policy.content_selector, policy.text_mode) == ("main", "dom")


def test_resource_observation_reports_real_overrun(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(capture, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(capture, "match_resource_targets", lambda *args: [{"satisfied": True}])
    monkeypatch.setattr(capture, "resource_source_scope_identity", lambda case: {})

    class Page:
        def evaluate(self, expression):
            clock[0] = 2.0
            return [{"tag": "img", "attribute": "src", "url": "https://example.com/a.png"}]

    row = capture.capture_resource_observation(Page(), case_with(), 1000, 0.0)
    assert row["completion"]["elapsed_ms"] == 2000
    assert row["completion"]["satisfied"] is False


@pytest.fixture
def checkpoint_setup(tmp_path, monkeypatch):
    binary = tmp_path / "browser"
    binary.write_bytes(b"browser v1")
    calls = []

    def observe(case, *args, **kwargs):
        calls.append(case["id"])
        return {"case_id": case["id"], "status": "success", "content_text": "Article"}

    monkeypatch.setattr(capture, "capture_with_retries", observe)
    options = dict(
        timeout_ms=1000,
        attempts=1,
        browser_binary=str(binary),
        checkpoints=tmp_path / "checkpoints",
    )
    return binary, calls, options


@pytest.mark.parametrize("change", ["binary", "source", "policy", "scope"])
def test_changed_capture_inputs_cannot_reuse_or_replace_checkpoint(
    checkpoint_setup, monkeypatch, change
):
    binary, calls, options = checkpoint_setup
    case = case_with()
    capture.capture_batch([case], **options)
    checkpoint = options["checkpoints"] / "new-task.json"
    original = checkpoint.read_bytes()
    if change == "binary":
        binary.write_bytes(b"browser v2")
    elif change == "source":
        monkeypatch.setattr(capture, "capture_source_identity", lambda: {"extractor": "new"})
    elif change == "policy":
        case["source_verification"]["browser_observation"] = {"selector": "main"}
    else:
        options["observation_scope"] = "visible_page"
    with pytest.raises(ValueError, match="inputs changed"):
        capture.capture_batch([case], **options)
    assert checkpoint.read_bytes() == original
    assert calls == ["new-task"]


def test_binary_changed_during_capture_cannot_install_checkpoint(checkpoint_setup, monkeypatch):
    binary, _, options = checkpoint_setup

    def observe(case, *args, **kwargs):
        binary.write_bytes(b"changed during observation")
        return {"case_id": case["id"]}

    monkeypatch.setattr(capture, "capture_with_retries", observe)
    with pytest.raises(ValueError, match="implementation changed"):
        capture.capture_batch([case_with()], **options)
    assert not (options["checkpoints"] / "new-task.json").exists()


def test_checkpoint_requires_identified_binary(tmp_path):
    with pytest.raises(ValueError, match="explicit browser binary"):
        capture.capture_batch([case_with()], timeout_ms=1000, attempts=1, checkpoints=tmp_path)


@pytest.mark.parametrize("target", ["directory", "file"])
def test_symlinked_checkpoints_rejected(checkpoint_setup, tmp_path, target):
    _, calls, options = checkpoint_setup
    if target == "directory":
        options["checkpoints"].symlink_to(tmp_path, target_is_directory=True)
    else:
        options["checkpoints"].mkdir()
        (options["checkpoints"] / "new-task.json").symlink_to(tmp_path / "absent")
    with pytest.raises(ValueError, match="symlink"):
        capture.capture_batch([case_with()], **options)
    assert calls == []


def test_cli_preserves_output_created_during_capture(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({"cases": [case_with()]}))
    output = tmp_path / "observations.json"

    def observe(*args, **kwargs):
        output.write_bytes(b"independently created output")
        return {"new-task": {"status": "success", "completion": {"satisfied": True}}}

    monkeypatch.setattr(capture, "capture_batch", observe)
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture", "--dataset", str(dataset), "--output", str(output), "--case-ids", "new-task"],
    )
    with pytest.raises(FileExistsError):
        capture.main()
    assert output.read_bytes() == b"independently created output"


@pytest.mark.parametrize("duplicate", ["dataset", "selection"])
def test_cli_duplicate_scope_fails_before_capture(tmp_path, monkeypatch, duplicate):
    dataset = tmp_path / "dataset.json"
    cases = [case_with()] * (2 if duplicate == "dataset" else 1)
    dataset.write_text(json.dumps({"cases": cases}))
    output = tmp_path / "observations.json"
    selected = "new-task,new-task" if duplicate == "selection" else "new-task"

    def unexpected(*args, **kwargs):
        pytest.fail("Duplicate scope reached browser capture")

    monkeypatch.setattr(capture, "capture_batch", unexpected)
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture", "--dataset", str(dataset), "--output", str(output), "--case-ids", selected],
    )
    with pytest.raises(SystemExit) as error:
        capture.main()
    assert error.value.code == 2
    assert not output.exists()


@pytest.fixture
def rendered_page():
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"""<html><body><nav>Navigation only</nav><main>
            <div style="display:none">Hidden panel</div>
            <div aria-hidden="true" style="display:none">Excluded panel</div>
            </main><footer>Footer only</footer>
            <script>setTimeout(()=>{for(let i=0;i<10;i++){
            const p=document.createElement('p');p.className='quote';p.textContent='Card '+i;
            document.querySelector('main').append(p)}},100)</script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(
    not os.getenv("BROWSER_EVAL_TEST_BROWSER_BINARY"), reason="explicit Chromium required"
)
@pytest.mark.parametrize("mode", ["visible", "dom", "reference"])
def test_live_multielement_completion_preserves_declared_content_scope(rendered_page, mode):
    case = case_with(
        {"selector": ".quote", "minimum": 10, "content_selector": "main", "text_mode": mode}
    )
    case["url"] = rendered_page
    row = capture.capture(case, 10000, os.environ["BROWSER_EVAL_TEST_BROWSER_BINARY"])
    assert row["completion"]["satisfied"] is True
    assert row["observed_records"] == 10
    assert all(f"Card {i}" in row["content_text"] for i in range(10))
    assert "Navigation only" not in row["content_text"]
    assert "Footer only" not in row["content_text"]
    assert ("Hidden panel" in row["content_text"]) == (mode != "visible")
    assert ("Excluded panel" in row["content_text"]) == (mode == "dom")


@pytest.mark.skipif(
    not os.getenv("BROWSER_EVAL_TEST_BROWSER_BINARY"), reason="explicit Chromium required"
)
def test_live_ambiguous_content_scope_is_rejected_instead_of_truncated(rendered_page):
    task = case_with({"selector": ".quote", "minimum": 10, "content_selector": ".quote"})
    task["url"] = rendered_page
    with pytest.raises(ValueError, match="exactly one element"):
        capture.capture(task, 10000, os.environ["BROWSER_EVAL_TEST_BROWSER_BINARY"])
