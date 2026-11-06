"""The shared reviewer runner keeps each caller's timeout and process-cleanup contract."""
import os
import subprocess
import sys
import time

import pytest

from harness.review_runner import ReviewerSessions, run_reviewer


def python(source):
    return [sys.executable, "-c", source]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until_gone(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(.02)
    return not alive(pid)


# Records its session id and its own child's pid, then outlives any timeout.
PARENT_WITH_CHILD = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
open(sys.argv[1], "w").write(f"{os.getsid(0)} {child.pid}")
time.sleep(60)
"""


def read_started(path, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").count(" ") == 1:
            session, child = path.read_text(encoding="utf-8").split()
            return int(session), int(child)
        time.sleep(.02)
    pytest.fail("Reviewer did not start")


@pytest.mark.parametrize("source", [
    "import sys; print('out'); print('err', file=sys.stderr)",
    "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
])
def test_foreground_result_matches_subprocess_run(tmp_path, source):
    command = python(source)
    reference = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, timeout=30)
    completed = run_reviewer(command, cwd=tmp_path, timeout=30)
    assert (completed.args, completed.returncode, completed.stdout, completed.stderr) == (
        reference.args, reference.returncode, reference.stdout, reference.stderr)


def test_foreground_timeout_message_matches_subprocess_run(tmp_path):
    command = python("import time; time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired) as reference:
        subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, timeout=.2)
    with pytest.raises(subprocess.TimeoutExpired) as observed:
        run_reviewer(command, cwd=tmp_path, timeout=.2)
    assert str(observed.value) == str(reference.value)


def test_foreground_log_file_receives_merged_output(tmp_path):
    log = tmp_path / "reviewer.log"
    with log.open("w", encoding="utf-8") as stream:
        completed = run_reviewer(
            python("import sys; print('out', flush=True); print('err', file=sys.stderr)"),
            stdout=stream, stderr=subprocess.STDOUT, timeout=30,
        )
    assert completed.returncode == 0 and completed.stdout is None
    assert log.read_text(encoding="utf-8").split() == ["out", "err"]


def test_foreground_timeout_kills_only_the_direct_child_in_this_session(tmp_path):
    started = tmp_path / "started"
    with pytest.raises(subprocess.TimeoutExpired):
        run_reviewer([*python(PARENT_WITH_CHILD), str(started)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    session, grandchild = read_started(started)
    try:
        assert session == os.getsid(0)
        assert alive(grandchild)
    finally:
        os.kill(grandchild, 9)


def test_session_timeout_kills_the_whole_group_and_reaps_the_reviewer(tmp_path):
    started = tmp_path / "started"
    sessions = ReviewerSessions()
    process = sessions.start([*python(PARENT_WITH_CHILD), str(started)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with pytest.raises(subprocess.TimeoutExpired):
        sessions.communicate(process, b"", timeout=3)
    session, grandchild = read_started(started)
    assert session == process.pid != os.getsid(0)
    assert process.returncode == -9
    assert wait_until_gone(grandchild)


def test_stop_kills_running_sessions_and_refuses_late_starts(tmp_path):
    started = tmp_path / "started"
    sessions = ReviewerSessions()
    process = sessions.start([*python(PARENT_WITH_CHILD), str(started)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, grandchild = read_started(started)
    assert not sessions.stopping
    sessions.stop()
    assert sessions.stopping and process.returncode == -9
    assert wait_until_gone(grandchild)
    assert sessions.start(python("raise SystemExit('must not start')")) is None


def fail_communicate(monkeypatch, error):
    def communicate(process, *args, **kwargs):
        interrupted.append(process)
        raise error

    interrupted = []
    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
    return interrupted


def test_foreground_interrupt_kills_and_reaps_the_reviewer(monkeypatch):
    interrupted = fail_communicate(monkeypatch, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run_reviewer(python("import time; time.sleep(5)"), timeout=30)
    assert [process.returncode for process in interrupted] == [-9]


def test_session_communication_error_kills_the_whole_group_and_propagates(tmp_path, monkeypatch):
    started = tmp_path / "started"
    sessions = ReviewerSessions()
    process = sessions.start([*python(PARENT_WITH_CHILD), str(started)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, grandchild = read_started(started)
    error = OSError("communication failed")
    fail_communicate(monkeypatch, error)
    with pytest.raises(OSError) as raised:
        sessions.communicate(process, None, timeout=30)
    assert raised.value is error
    assert process.returncode == -9
    assert wait_until_gone(grandchild)


def test_kill_stops_one_session_and_forgets_it(tmp_path):
    started = tmp_path / "started"
    sessions = ReviewerSessions()
    process = sessions.start([*python(PARENT_WITH_CHILD), str(started)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, grandchild = read_started(started)
    sessions.kill(process)
    assert process.returncode == -9
    assert wait_until_gone(grandchild)
    sessions.kill(process)
    assert not sessions.stopping and sessions.start([sys.executable, "-c", ""]).wait() == 0

def test_chat_reviewer_sends_inline_evidence_without_tools_or_credentials(monkeypatch):
    import json

    from harness.review_runner import run_chat_reviewer

    requests = []

    class Response:
        status = 200

        def text(self):
            return json.dumps({"id": "one", "model": "pinned-reviewer", "usage": {},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"cases":[]}'}}]})

    def request(url, **kwargs):
        requests.append((url, kwargs))
        return Response()

    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", request)
    result, receipt = run_chat_reviewer(model="pinned-reviewer", prompt="Audit all fields",
        evidence={"source_markdown": "untrusted source"}, schema={"type": "object"},
        credentials={"OPENAI_API_KEY": "secret-key", "OPENAI_BASE_URL": "https://example.org/v1"})
    url, sent = requests[0]
    assert url == "https://example.org/v1/chat/completions"
    assert "tools" not in sent["json_body"]
    assert "secret-key" not in json.dumps(sent["json_body"])
    assert sent["json_body"]["model"] == "pinned-reviewer"
    assert result == {"cases": []} and receipt["returned_model"] == "pinned-reviewer"


def test_chat_reviewer_rejects_truncated_output(monkeypatch):
    import json

    from harness.review_runner import run_chat_reviewer

    class Response:
        status = 200

        def text(self):
            return json.dumps({"choices": [{"finish_reason": "length", "message": {
                "content": '{"cases":[]}'}}]})

    monkeypatch.setattr(
        "harness.web_fetch.providers.http_client.request", lambda *a, **kw: Response()
    )
    with pytest.raises(ValueError, match="incomplete"):
        run_chat_reviewer(model="pinned", prompt="Audit", evidence={}, schema={},
            credentials={"OPENAI_API_KEY": "secret", "OPENAI_BASE_URL": "https://example.org/v1"})


@pytest.mark.parametrize("raw, expected, suffix", [
    ('{"cases":[]}', {"cases": []}, ""),
    ('{"cases":[]\n', {"cases": []}, "}"),
    ('{"cases":[] \n\t', {"cases": []}, "}"),
    ('{"cases":[{}]', {"cases": [{}]}, "}"),
    ('{"text":"braces [ inside a string","cases":[]',
     {"text": "braces [ inside a string", "cases": []}, "}"),
])
def test_review_json_recovers_only_complete_values_at_eof(raw, expected, suffix):
    from harness.review_runner import parse_review_json

    assert parse_review_json(raw) == (expected, suffix)


@pytest.mark.parametrize("raw", ['{"value":', '{"value":"unfinished',
                                  '{"value":tru', '{"a":1,}', '{"a":1]'])
def test_review_json_never_invents_missing_content(raw):
    from harness.review_runner import parse_review_json

    with pytest.raises(ValueError):
        parse_review_json(raw)
