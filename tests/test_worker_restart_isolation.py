"""Delayed pipe readers from a timed-out worker cannot alter its successor."""
import itertools
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_restarted_worker_keeps_its_own_response_and_logs(tmp_path, monkeypatch, stream):
    counter = tmp_path / 'counter'
    worker = tmp_path / 'worker.py'
    worker.write_text(
        'import json,sys,time\nfrom pathlib import Path\n'
        'counter=Path(sys.argv[1])\n'
        'generation=int(counter.read_text())+1 if counter.exists() else 1\n'
        'counter.write_text(str(generation))\n'
        'for line in sys.stdin:\n'
        ' print("worker-"+str(generation),file=sys.stderr,flush=True)\n'
        ' time.sleep(2 if generation==1 else .35)\n'
        ' print(json.dumps({"ok":True,"content":"worker-"+str(generation)}),flush=True)\n'
    )
    provider = SubprocessJsonlProvider('custom', {
        'repo_path': str(tmp_path), 'command': [sys.executable, '-u', str(worker), str(counter)],
    })
    gate, successor_started = threading.Event(), threading.Event()
    order = itertools.count()
    processes = []
    original = getattr(provider, '_drain_' + stream)

    def delayed_reader(*args):
        processes.append(args[0])
        if next(order) == 0:
            gate.wait(5)
        else:
            successor_started.set()
        original(*args)

    monkeypatch.setattr(provider, '_drain_' + stream, delayed_reader)
    case = EvalCase('one', 'One', 'https://example.invalid/', 'text', [])
    try:
        with pytest.raises(TimeoutError):
            provider.fetch(case, .3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(provider.fetch, case, 5)
            assert successor_started.wait(3)
            gate.set()
            answer = future.result(timeout=5)
        assert answer.ok and answer.content == 'worker-2'
        assert 'worker-2' in answer.metadata['stderr']
        assert 'worker-1' not in answer.metadata['stderr']
    finally:
        gate.set()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        provider.__del__()
