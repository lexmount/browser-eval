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

    monkeypatch.setattr(capture, "_capture_in_process", unexpected)
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

    monkeypatch.setattr(capture, "_capture_in_process", observe)
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

    monkeypatch.setattr(capture, "_capture_in_process", observe)
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
            <div style="opacity:0"><span>Transparent menu</span></div>
            <noscript>Fallback code is not visible with JavaScript enabled</noscript>
            <template>Template payload is not visible</template>
            <span>Visible duplicate</span><div style="opacity:0">Visible duplicate</div>
            <p style="opacity:0.5">Translucent text</p>
            <p>Inline <strong>formatting</strong> preserved</p>
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
    assert ("Transparent menu" in row["content_text"]) == (mode != "visible")
    assert "Visible duplicate" in row["content_text"]
    assert "Translucent text" in row["content_text"]
    assert "Inline formatting preserved" in " ".join(row["content_text"].split())
    if mode == "visible":
        assert "Inline formatting preserved" in row["content_text"]
        assert row["content_text"].count("Visible duplicate") == 1
        assert "Fallback code" not in row["content_text"]
        assert "Template payload" not in row["content_text"]
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


def test_stuck_capture_worker_is_bounded_and_remains_an_unqualified_observation(monkeypatch):
    import time
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', 'import time; time.sleep(30)')
    began = time.monotonic()
    row = capture._capture_in_process(case_with(), 20, 1)
    assert time.monotonic() - began < 8
    assert row['status'] == 'failure'
    assert row['completion']['satisfied'] is False
    assert row['attempt_count'] is None
    assert row['attempt_errors'][0]['type'] == 'TimeoutError'
    assert 'wall-clock budget' in row['attempt_errors'][0]['message']


def test_crashed_capture_worker_does_not_erase_other_case_results(monkeypatch):
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', "raise RuntimeError('controlled worker failure')")
    row = capture._capture_in_process(case_with(), 1000, 1)
    assert row['case_id'] == 'new-task'
    assert row['status'] == 'failure'
    assert 'controlled worker failure' in row['attempt_errors'][0]['message']


@pytest.mark.skipif(not os.getenv('BROWSER_EVAL_TEST_BROWSER_BINARY'), reason='explicit Chromium required')
def test_batch_preserves_browser_path_relative_to_external_caller(tmp_path, monkeypatch, rendered_page):
    from pathlib import Path

    (tmp_path / 'browser').mkdir()
    (tmp_path / 'browser/chrome').symlink_to(Path(os.environ['BROWSER_EVAL_TEST_BROWSER_BINARY']).resolve())
    monkeypatch.chdir(tmp_path)
    # Only the loopback fixture bypasses public DNS qualification.
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM',
        'import harness.web_fetch.browser_observations as c; '
        'c.resolve_public_host=lambda url, ip: ("127.0.0.1", None, "local-fixture"); '
        + capture._CAPTURE_PROGRAM)
    task = case_with({'selector': 'main', 'text_mode': 'visible'})
    task['url'] = rendered_page
    result = capture.capture_batch([task], timeout_ms=10000, attempts=1,
                                   browser_binary='browser/chrome', checkpoints=tmp_path / 'checkpoints')
    assert result['new-task']['completion']['satisfied'], json.dumps(result, indent=2)
    assert 'Inline formatting preserved' in result['new-task']['content_text']


@pytest.mark.skipif(os.name != 'posix' or not os.getenv('BROWSER_EVAL_TEST_BROWSER_BINARY'),
                    reason='POSIX and explicit Chromium required')
@pytest.mark.parametrize('discovery_failure', [None, 'missing', 'exit', 'timeout'])
@pytest.mark.parametrize('stalled_process', ['driver', 'browser', 'driver-exit'])
def test_hung_driver_cleanup_removes_owned_chrome_and_preserves_another_browser(tmp_path, monkeypatch, discovery_failure, stalled_process):
    import subprocess
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv('DEBUG_COLORS', '1')
    pid_file = tmp_path / 'owned-browser.json'
    binary = os.environ['BROWSER_EVAL_TEST_BROWSER_BINARY']
    program = f'''
import json, os, signal, subprocess, time
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path={binary!r}, headless=True, args=['--no-sandbox'])
    browser.new_page()
    driver = p._impl_obj._connection._transport._proc.pid
    table = subprocess.check_output(['ps', '-A', '-o', 'pid=,ppid='], text=True)
    parents = {{int(pid): int(parent) for pid, parent in (line.split() for line in table.splitlines())}}
    children = {{pid for pid, parent in parents.items() if parent == driver}}
    while True:
        descendants = {{pid for pid, parent in parents.items() if parent in children}}
        if descendants <= children:
            break
        children.update(descendants)
    children = sorted(children)
    Path({str(pid_file)!r}).write_text(json.dumps(children))
    os.kill(driver if {stalled_process!r} == 'driver' else children[0], signal.SIGSTOP)
    if {stalled_process!r} == 'driver-exit':
        os.kill(driver, signal.SIGKILL)
    time.sleep(30)
'''
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', program)
    if discovery_failure:
        def unavailable(*args, **kwargs):
            if discovery_failure == 'missing':
                raise FileNotFoundError('ps missing')
            if discovery_failure == 'exit':
                raise subprocess.CalledProcessError(1, 'ps')
            raise subprocess.TimeoutExpired('ps', 1)
        monkeypatch.setattr(capture.subprocess, 'check_output', unavailable)
    with sync_playwright() as p:
        unrelated = p.chromium.launch(executable_path=binary, headless=True, args=['--no-sandbox'])
        try:
            result = capture._capture_in_process(case_with(), 4000, 1, binary)
            assert result['status'] == 'failure'
            owned = json.loads(pid_file.read_text())
            assert owned
            import time
            deadline = time.monotonic() + 2
            while True:
                alive = [(pid, subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                          capture_output=True, text=True).stdout.strip()) for pid in owned]
                alive = [(pid, state) for pid, state in alive if state and not state.startswith('Z')]
                if not alive or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            assert not alive, alive
            assert unrelated.new_page().evaluate('1 + 1') == 2
        finally:
            unrelated.close()
            if pid_file.exists():
                import signal
                for pid in json.loads(pid_file.read_text()):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process enumeration')
@pytest.mark.parametrize('failure', ['missing', 'exit', 'timeout'])
def test_capture_timeout_remains_bounded_when_process_enumeration_fails(monkeypatch, failure):
    import subprocess
    import time

    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', 'import time; time.sleep(4)')
    def unavailable(*args, **kwargs):
        if failure == 'missing':
            raise FileNotFoundError('ps is unavailable')
        if failure == 'exit':
            raise subprocess.CalledProcessError(1, 'ps')
        raise subprocess.TimeoutExpired('ps', 1)
    monkeypatch.setattr(capture.subprocess, 'check_output', unavailable)
    began = time.monotonic()
    result = capture._capture_in_process(case_with(), 20, 1)
    assert time.monotonic() - began < 3.8
    assert result['status'] == 'failure'
    assert result['attempt_errors'][0]['type'] == 'TimeoutError'


def test_worker_launch_failure_is_checkpointed_without_aborting_the_batch(tmp_path, monkeypatch):
    original = capture.subprocess.Popen
    calls = []
    def launch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('controlled process launch failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(capture.subprocess, 'Popen', launch)
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM',
        'import json,sys; p=json.load(sys.stdin); print(json.dumps({'
        '"case_id":p["case"]["id"],"status":"success","completion":{"satisfied":True}}))')
    binary = tmp_path / 'browser'
    binary.write_bytes(b'fixture binary identity')
    cases = [case_with(), {**case_with(), 'id': 'next-task'}]
    result = capture.capture_batch(cases, timeout_ms=1000, attempts=1,
                                   browser_binary=str(binary), checkpoints=tmp_path / 'checkpoints')
    assert result['new-task']['status'] == 'failure'
    assert result['new-task']['attempt_errors'][0]['type'] == 'OSError'
    assert result['next-task']['status'] == 'success'
    assert len(list((tmp_path / 'checkpoints').glob('*.json'))) == 2


def test_unsupported_batch_platform_is_rejected_before_launch(monkeypatch):
    monkeypatch.setattr(capture, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(capture.subprocess, 'Popen', lambda *a, **kw: pytest.fail('unsupported launch'))
    with pytest.raises(ValueError, match='POSIX'):
        capture.capture_batch([case_with()], timeout_ms=1000, attempts=1)
    with pytest.raises(ValueError, match='POSIX'):
        capture._capture_in_process(case_with(), 1000, 1)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process escalation')
@pytest.mark.parametrize('reused', [False, True])
def test_hard_timeout_signals_only_currently_owned_children_once(monkeypatch, reused):
    import signal

    launch = capture.subprocess.Popen
    started = []
    signals = []
    groups = []
    snapshots = []
    child = 99999999
    def worker(*args, **kwargs):
        process = launch(*args, **kwargs)
        started.append(process.pid)
        return process
    def process_table(*args, **kwargs):
        snapshots.append(1)
        parent = 1 if reused else started[0]
        return f'{started[0]} 0 worker\n{child} {parent} child\n'
    def terminate_group(pid, sig):
        groups.append((pid, sig))
        os.killpg(pid, sig)
    monkeypatch.setattr(capture.subprocess, 'Popen', worker)
    monkeypatch.setattr(capture.subprocess, 'check_output', process_table)
    monkeypatch.setattr(capture, 'os', SimpleNamespace(name='posix', environ=os.environ, getpgid=lambda pid: started[0],
        kill=lambda pid, sig: signals.append((pid, sig)), killpg=terminate_group))
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', 'import time; time.sleep(30)')
    result = capture._capture_in_process(case_with(), 20, 1)
    assert result['status'] == 'failure'
    assert signals == ([] if reused else [(child, signal.SIGKILL)])
    assert groups == [(started[0], signal.SIGKILL)]


@pytest.mark.skipif(os.name != 'posix', reason='POSIX browser ownership')
@pytest.mark.parametrize('mode', ['exited', 'reused'])
def test_timeout_does_not_signal_closed_browser_receipts_or_unowned_reused_pids(monkeypatch, mode):
    launch = capture.subprocess.Popen
    worker_pid = []
    signalled = []
    unrelated = 99999999
    def worker(*args, **kwargs):
        process = launch(*args, **kwargs)
        worker_pid.append(process.pid)
        return process
    def processes(*args, **kwargs):
        if mode == 'exited':
            raise FileNotFoundError('no process listing')
        return f'{worker_pid[0]} 0 worker\n{unrelated} 1 unrelated\n'
    log = f'2026-10-26T00:00:00Z pw:browser <launched> pid={unrelated}\n'
    if mode == 'exited':
        log += f'2026-10-26T00:00:01Z pw:browser [pid={unrelated}] <process did exit: exitCode=0, signal=null>\n'
    monkeypatch.setattr(capture.subprocess, 'Popen', worker)
    monkeypatch.setattr(capture.subprocess, 'check_output', processes)
    monkeypatch.setattr(capture, 'os', SimpleNamespace(name='posix', environ=os.environ, getpgid=os.getpgid,
        kill=lambda pid, sig: signalled.append((pid, sig)), killpg=os.killpg))
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM',
        f'import sys,time; sys.stderr.write({log!r}); sys.stderr.flush(); time.sleep(30)')
    result = capture._capture_in_process(case_with(), 20, 1)
    assert result['status'] == 'failure'
    assert signalled == []


@pytest.mark.skipif(os.name != 'posix', reason='POSIX inherited pipe writer')
def test_surviving_inherited_pipe_writer_does_not_abort_batch_or_checkpointing(tmp_path, monkeypatch):
    import signal
    import time

    pid_file = tmp_path / 'writer.pid'
    child = ("import os,sys,time; from pathlib import Path; pid=os.fork();\n"
             "if pid: sys.exit(0)\n"
             f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
             "sys.stderr.write('controlled inherited pipe writer\\n'); sys.stderr.flush(); time.sleep(30)")
    program = f'''import json,subprocess,sys,time
params=json.load(sys.stdin)
if params['case']['id']=='new-task':
    subprocess.run([sys.executable,'-c',{child!r}],check=True)
    time.sleep(30)
else:
    print(json.dumps(dict(case_id=params['case']['id'],status='success',completion=dict(satisfied=True))))
'''
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', program)
    binary = tmp_path / 'browser'
    binary.write_bytes(b'fixture browser identity')
    cases = [case_with(), {**case_with(), 'id': 'next-task'}]
    began = time.monotonic()
    try:
        result = capture.capture_batch(cases, timeout_ms=20, attempts=1,
            browser_binary=str(binary), checkpoints=tmp_path / 'checkpoints')
        assert time.monotonic() - began < 6
        assert result['new-task']['status'] == 'failure'
        assert 'controlled inherited pipe writer' in result['new-task']['worker_diagnostics']
        assert result['next-task']['status'] == 'success'
        assert len(list((tmp_path / 'checkpoints').glob('*.json'))) == 2
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != 'posix', reason='POSIX late browser launch')
@pytest.mark.parametrize('inherit_pipes', [False, True])
def test_browser_launched_during_failed_discovery_is_stopped(tmp_path, monkeypatch, inherit_pipes):
    import signal
    import time

    process_status = capture.subprocess.run
    trigger = tmp_path / 'launch'
    pid_file = tmp_path / 'browser.pid'
    program = f'''import json,subprocess,sys,time
from pathlib import Path
json.load(sys.stdin)
while not Path({str(trigger)!r}).exists(): time.sleep(.01)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)',
    '--user-data-dir='+{str(tmp_path / 'profile')!r},'--remote-debugging-pipe'],
    start_new_session=True, stdout={"None" if inherit_pipes else "subprocess.DEVNULL"},
    stderr={"None" if inherit_pipes else "subprocess.DEVNULL"})
Path({str(pid_file)!r}).write_text(str(child.pid))
print('2026-10-26T00:00:00Z pw:browser <launching> browser --user-data-dir='
      +{str(tmp_path / 'profile')!r}+' --remote-debugging-pipe',file=sys.stderr,flush=True)
print(f'2026-10-26T00:00:00Z pw:browser <launched> pid={{child.pid}}',file=sys.stderr,flush=True)
time.sleep(30)
'''
    def unavailable(*args, **kwargs):
        trigger.touch()
        deadline = time.monotonic() + 1
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        time.sleep(.05)
        raise FileNotFoundError('process discovery unavailable')
    signals = []
    def kill_group(pid, sig):
        signals.append(pid)
        os.killpg(pid, sig)
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', program)
    monkeypatch.setattr(capture.subprocess, 'check_output', unavailable)
    monkeypatch.setattr(capture, 'os', SimpleNamespace(name='posix', environ=os.environ, getpgid=os.getpgid,
        kill=os.kill, killpg=kill_group))
    try:
        result = capture._capture_in_process(case_with(), 20, 1)
        assert result['status'] == 'failure'
        browser_pid = int(pid_file.read_text())
        assert browser_pid in signals
        assert len(signals) == len(set(signals))
        deadline = time.monotonic() + 2
        while True:
            state = process_status(['ps', '-o', 'stat=', '-p', str(browser_pid)],
                capture_output=True, text=True, timeout=1).stdout.strip()
            if not state or state.startswith('Z'):
                break
            assert time.monotonic() < deadline, 'late browser survived capture timeout'
            time.sleep(.02)
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize('failure_call', [1, 2])
def test_worker_pipe_error_is_checkpointed_and_next_case_runs(tmp_path, monkeypatch, failure_call):
    launch = capture.subprocess.Popen
    workers = []
    def start(*args, **kwargs):
        process = launch(*args, **kwargs)
        if not workers:
            communicate = process.communicate
            calls = []
            def fail_pipe(*args, **kwargs):
                calls.append(1)
                if len(calls) == failure_call:
                    raise OSError('controlled worker pipe failure')
                return communicate(*args, **kwargs)
            process.communicate = fail_pipe
        workers.append(process)
        return process
    monkeypatch.setattr(capture.subprocess, 'Popen', start)
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM',
        'import json,sys,time; p=json.load(sys.stdin); '
        'time.sleep(30 if p["case"]["id"]=="new-task" else 0); '
        'print(json.dumps(dict(case_id=p["case"]["id"],status="success",completion=dict(satisfied=True))))')
    binary = tmp_path / 'browser'
    binary.write_bytes(b'fixture binary')
    try:
        result = capture.capture_batch([case_with(), {**case_with(), 'id': 'next-task'}],
            timeout_ms=20, attempts=1, browser_binary=str(binary), checkpoints=tmp_path / 'checkpoints')
        assert result['new-task']['status'] == 'failure'
        assert result['new-task']['attempt_errors'][0]['type'] == 'OSError'
        assert 'controlled worker pipe failure' in result['new-task']['attempt_errors'][0]['message']
        assert result['next-task']['status'] == 'success'
        assert len(list((tmp_path / 'checkpoints').glob('*.json'))) == 2
        assert workers[0].poll() is not None
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX browser ownership')
@pytest.mark.parametrize('fallback_available', [False, True])
def test_unrelated_live_pid_in_stale_receipt_survives_failed_discovery(monkeypatch, fallback_available):
    import subprocess
    import sys

    run = subprocess.run
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                          start_new_session=True) as unrelated:
        log = ('2026-10-26T00:00:00Z pw:browser <launching> chrome '
               '--user-data-dir=/tmp/browser-eval-expired-profile --remote-debugging-pipe\n'
               f'2026-10-26T00:00:00Z pw:browser <launched> pid={unrelated.pid}\n')
        monkeypatch.setattr(capture, '_CAPTURE_PROGRAM',
            f'import sys,time; sys.stderr.write({log!r}); sys.stderr.flush(); time.sleep(30)')
        def unavailable(*args, **kwargs):
            raise FileNotFoundError('process listing unavailable')
        def fallback(args, **kwargs):
            if args[0] == 'pgrep' and not fallback_available:
                raise FileNotFoundError('fallback unavailable')
            return run(args, **kwargs)
        monkeypatch.setattr(capture.subprocess, 'check_output', unavailable)
        monkeypatch.setattr(capture.subprocess, 'run', fallback)
        try:
            result = capture._capture_in_process(case_with(), 20, 1)
            assert result['status'] == 'failure'
            assert unrelated.poll() is None, 'unrelated process killed by stale launch receipt'
        finally:
            unrelated.kill()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX late browser descendants')
def test_delayed_launch_receipt_cleans_children_created_after_snapshot(tmp_path, monkeypatch):
    import signal
    import subprocess
    import time

    trigger = tmp_path / 'spawn-child'
    browser_pid = tmp_path / 'browser.pid'
    child_pid = tmp_path / 'child.pid'
    browser_program = f'''import os,subprocess,sys,time
from pathlib import Path
Path({str(browser_pid)!r}).write_text(str(os.getpid()))
while not Path({str(trigger)!r}).exists(): time.sleep(.01)
child=subprocess.Popen([sys.executable,'-c','import os,signal,time; os.kill(os.getpid(),signal.SIGSTOP); time.sleep(30)'])
Path({str(child_pid)!r}).write_text(str(child.pid))
print(f'2026-10-26T00:00:00Z pw:browser <launched> pid={{os.getpid()}}',file=sys.stderr,flush=True)
time.sleep(30)
'''
    program = f'''import subprocess,sys,time
subprocess.Popen([sys.executable,'-c',{browser_program!r}], start_new_session=True)
time.sleep(30)
'''
    snapshot = subprocess.check_output
    def delayed_snapshot(*args, **kwargs):
        table = snapshot(*args, **kwargs)
        if browser_pid.exists() and not trigger.exists():
            trigger.touch()
            deadline = time.monotonic() + 1
            while not child_pid.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            time.sleep(.05)
        return table
    monkeypatch.setattr(capture, '_CAPTURE_PROGRAM', program)
    monkeypatch.setattr(capture.subprocess, 'check_output', delayed_snapshot)
    try:
        result = capture._capture_in_process(case_with(), 20, 1)
        assert result['status'] == 'failure'
        pid = int(child_pid.read_text())
        deadline = time.monotonic() + 2
        while True:
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
                capture_output=True, text=True, timeout=1).stdout.strip()
            if not state or state.startswith('Z'):
                break
            assert time.monotonic() < deadline, 'late browser child survived cleanup'
            time.sleep(.02)
    finally:
        for path in (child_pid, browser_pid):
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
