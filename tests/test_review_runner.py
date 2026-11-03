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
        if path.exists() and path.read_text().count(" ") == 1:
            session, child = path.read_text().split()
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
    with log.open("w") as stream:
        completed = run_reviewer(
            python("import sys; print('out', flush=True); print('err', file=sys.stderr)"),
            stdout=stream, stderr=subprocess.STDOUT, timeout=30,
        )
    assert completed.returncode == 0 and completed.stdout is None
    assert log.read_text().split() == ["out", "err"]


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
