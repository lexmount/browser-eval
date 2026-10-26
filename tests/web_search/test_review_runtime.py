"""The reviewer really starts a constrained child and reuses only completed work."""
import json
import sys
import time
import tomllib
from pathlib import Path

import pytest

from harness.web_search import review
from tests.web_search.test_review import example, opinion


def stub_backend(root, contract, *, exit_code=0, delay=0):
    replies = {}
    for entry in contract['packets']:
        packet = json.loads((root / entry['path']).read_bytes())
        replies[entry['sha256']] = opinion(packet, entry['sha256'])
    executable = root / 'fixture-codex'
    executable.write_text(f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
output=Path(args[args.index('--output-last-message')+1])
text=sys.stdin.read();payload=json.loads(text[text.index('{{"packet_sha256"'):])
(output.parent/'observed.json').write_text(json.dumps({{"args":args,"environment":dict(os.environ)}}))
import time; time.sleep({delay})
if {exit_code}: sys.exit({exit_code})
replies=json.loads({json.dumps(json.dumps(replies))})
output.write_text(json.dumps(replies[payload['packet_sha256']]))
print(json.dumps({{"model":"gpt-6.1-sol","usage":{{"output_tokens":12}}}}))
''')
    executable.chmod(0o700)
    return str(executable)


@pytest.mark.parametrize("source_mode", ["frozen", "live"])
def test_real_child_is_isolated_then_reused(tmp_path, monkeypatch, source_mode):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'account'))
    monkeypatch.setenv('REVIEW_SECRET', 'must-not-reach-child')
    _, root, contract = example(tmp_path, source_mode=source_mode)
    account = tmp_path / "account"
    account.mkdir(exist_ok=True)
    (account / "auth.json").write_text("fixture credential")
    config_writes = []
    write_text = Path.write_text

    def capture_configuration(path, value, *args, **kwargs):
        if path.name == "config.toml":
            config_writes.append(value)
        return write_text(path, value, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", capture_configuration)
    backend = stub_backend(root, contract)
    assert review.run(root, workers=1, limit=1, executable=backend) == [('0001', 'completed')]
    status = json.loads((root / 'jobs/0001/status.json').read_bytes())
    observed = json.loads((root / 'jobs/0001/attempt-001/observed.json').read_bytes())
    isolated = Path(observed['environment']['CODEX_HOME'])
    assert not isolated.exists()
    assert observed['environment']['HOME'] != str(Path.home())
    assert not (root / 'codex-home/auth.json').exists()
    assert '--json' in observed['args']
    assert 'REVIEW_SECRET' not in observed['environment']
    assert len(config_writes) == 1
    assert tomllib.loads(config_writes[0]) == {}
    expected_search = "live" if contract["source_mode"] == "live" else "disabled"
    assert f'web_search="{expected_search}"' in observed['args']
    for feature in ('shell_tool', 'unified_exec', 'apps', 'plugins', 'view_image', 'browser_use', 'computer_use', 'multi_agent'):
        i = observed['args'].index(feature)
        assert observed['args'][i - 1] == '--disable'
    assert status['observed_model'] == 'gpt-6.1-sol'
    assert len(status['session_log_sha256']) == 64
    assert review.run(root, workers=1, limit=1, executable='must-not-start') == [('0001', 'reused')]


def test_failed_child_earns_no_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'account'))
    _, root, contract = example(tmp_path)
    backend = stub_backend(root, contract, exit_code=7)
    assert review.run(root, workers=1, limit=1, executable=backend) == [('0001', 'failed')]
    assert review.collect(root)['output_reviewed'] == 0
    with pytest.raises(ValueError, match='incomplete'):
        review.collect(root, require_complete=True)


def test_cancelled_review_cannot_start_a_late_worker(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'account'))
    _, root, _ = example(tmp_path)

    class LateWorkerPool:
        """Model a worker that has started but has not yet entered its job."""
        def __init__(self, **kwargs):
            self.jobs = []

        def submit(self, function, *args):
            self.jobs.append((function, args))
            return object()

        def shutdown(self, **kwargs):
            for function, args in self.jobs:
                function(*args)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    def unexpected_child(*args, **kwargs):
        pytest.fail('Cancellation started another review process')

    monkeypatch.setattr(review, 'ThreadPoolExecutor', LateWorkerPool)
    monkeypatch.setattr(review, 'wait', interrupt)
    monkeypatch.setattr(review.subprocess, 'Popen', unexpected_child)
    with pytest.raises(KeyboardInterrupt):
        review.run(root, workers=1, limit=1)
    assert review.collect(root)['output_reviewed'] == 0


def test_cancelled_running_review_kills_child_and_retains_interrupted_state(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'account'))
    _, root, contract = example(tmp_path)
    backend = stub_backend(root, contract, delay=30)

    def interrupt_after_child_started(*args, **kwargs):
        deadline = time.monotonic() + 5
        observed = root / 'jobs/0001/attempt-001/observed.json'
        while not observed.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        assert observed.exists(), 'Review child did not start'
        raise KeyboardInterrupt

    monkeypatch.setattr(review, 'wait', interrupt_after_child_started)
    with pytest.raises(KeyboardInterrupt):
        review.run(root, workers=1, limit=1, executable=backend)
    state = json.loads((root / 'jobs/0001/status.json').read_bytes())
    assert state['status'] == 'interrupted'
    assert state['returncode'] != 0
    assert review.collect(root)['output_reviewed'] == 0
    assert not list(root.glob('codex-home/auth.json'))
