import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.webmainbench_latency_diagnostic import _resource_samples, load_diagnostic, stats, top_positive_gap_share


class LatencyDiagnosticTests(unittest.TestCase):
    def test_empty_timing_population_is_rejected_as_incomplete_evidence(self):
        with self.assertRaisesRegex(ValueError, 'no successful timed pages'):
            stats([])

    def test_tail_share_is_unavailable_without_net_added_time(self):
        self.assertIsNone(top_positive_gap_share([10, -10]))
        self.assertIsNone(top_positive_gap_share([2, -10]))
        self.assertEqual(top_positive_gap_share([3, 1]), 1.0)

    def test_system_samples_respect_run_window_and_swap_deltas(self):
        start = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)
        lines = []
        for seconds, idle, free, swapin, swapout in (
            (-5, 2.0, 10, 50, 100), (0, 70.0, 400, 60, 100),
            (5, 43.0, 90, 63, 100), (10, 80.0, 800, 70, 100),
            (15, 1.0, 5, 100, 200),
        ):
            lines.extend((
                (start + timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                f'CPU usage: 10% user, 10% sys, {idle}% idle',
                f'PhysMem: 20G used, {free}M unused.',
                f'Swapins: {swapin}', f'Swapouts: {swapout}',
            ))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'monitor.log'
            path.write_text('\n'.join(lines))
            summary = _resource_samples(path, {
                'started_at': start.isoformat(),
                'completed_at': (start + timedelta(seconds=10)).isoformat(),
            })
        self.assertEqual(summary, {
            'samples': 3, 'min_idle_cpu_pct': 43.0,
            'median_idle_cpu_pct': 70.0, 'min_unused_memory_mb': 90.0,
            'swapouts_delta': 0, 'swapins_delta': 10,
        })

    def test_attached_diagnostic_requires_unchanged_official_matrix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(load_diagnostic(root, {}, [], []))
            diagnostic = root / 'latency-diagnostic'
            diagnostic.mkdir()
            (diagnostic / 'manifest.json').write_text(json.dumps({
                'kind': 'moli_115_117_latency_rerun_v1',
                'official_results_sha256': 'different',
            }))
            (root / 'results.jsonl').write_text('unchanged\n')
            with self.assertRaisesRegex(ValueError, 'manifest differs'):
                load_diagnostic(root, {'cases': []}, [], [])
            expected = hashlib.sha256((diagnostic / 'manifest.json').read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'preserved original matrix'):
                load_diagnostic(root, {'cases': []}, [], [], expected)


def paired_inputs(root):
    import hashlib
    import json

    dataset = root / 'source.jsonl'
    rows = [{'track_id': name, 'html': '<p>synthetic</p>',
             'groundtruth_content': 'synthetic'} for name in ('one', 'two', 'three')]
    dataset.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    sample = root / 'sample.txt'; sample.write_text('one\ntwo\nthree\n')
    argv = ['--dataset', str(dataset), '--sample', str(sample), '--source-count', '3',
            '--source-sha256', hashlib.sha256(dataset.read_bytes()).hexdigest(),
            '--output', str(root / 'output'), '--rounds', '3', '--all-cases']
    for name, version in [('older', '2.0.0'), ('newer', '2.1.0')]:
        binary = root / name; binary.write_text('synthetic ' + version)
        prior = root / (name + '.jsonl')
        prior.write_text(''.join(json.dumps({'track_id': row['track_id'], 'ok': True,
                                            'elapsed_ms': 10}) + '\n' for row in rows))
        argv += ['--' + name, str(binary), '--prior-' + name, str(prior),
                 '--' + name + '-version', version]
    return argv


def fake_paired_environment(monkeypatch, root, *, mutate=False):
    import contextlib
    import subprocess
    from harness.web_fetch import webmainbench_runner as runner
    from harness.web_fetch import webmainbench_latency_diagnostic as diagnostic

    @contextlib.contextmanager
    def fixture(records):
        yield 'https://synthetic.invalid', root / 'synthetic-ca'

    calls = []
    def run(command, **kwargs):
        if '--version' in command:
            version = '2.0.0' if Path(command[0]).name == 'older' else '2.1.0'
            return subprocess.CompletedProcess(command, 0, 'moli ' + version, '')
        calls.append(command)
        if mutate and len(calls) == 1:
            (root / 'older').write_text('changed binary')
        return subprocess.CompletedProcess(command, 0, b'synthetic body', b'')

    monkeypatch.setattr(runner, 'fixture_server', fixture)
    monkeypatch.setattr(runner, 'verify_loopback_sandbox', lambda *args: {'synthetic': True})
    monkeypatch.setattr(diagnostic.subprocess, 'run', run)
    return calls


def test_paired_calls_use_declared_versions_scope_and_balanced_order(tmp_path, monkeypatch):
    import hashlib
    import json
    from harness.web_fetch.webmainbench_latency_diagnostic import paired_tail_main

    argv = paired_inputs(tmp_path)
    commands = fake_paired_environment(monkeypatch, tmp_path)
    paired_tail_main(argv)
    output = tmp_path / 'output'
    selection = json.loads((output / 'selection.json').read_text())
    calls = [json.loads(line) for line in (output / 'calls.jsonl').read_text().splitlines()]
    assert set(selection['case_ids']) == {'one', 'two', 'three'}
    assert set(selection['binaries']) == {'2.0.0', '2.1.0'}
    assert len(commands) == len(calls) == 18
    for index in range(0, len(calls), 2):
        pair = calls[index:index+2]
        assert pair[0]['case_id'] == pair[1]['case_id']
        assert pair[0]['round'] == pair[1]['round']
        assert {row['version'] for row in pair} == {'2.0.0', '2.1.0'}
    for name in selection['case_ids']:
        first = [row['version'] for row in calls if row['case_id'] == name]
        assert first[0:2] == first[4:6] == list(reversed(first[2:4]))
    assert hashlib.sha256((output / 'runner-source.py').read_bytes()).hexdigest() == selection['runner_sha256']


def test_paired_calls_reject_binary_drift(tmp_path, monkeypatch):
    import pytest
    from harness.web_fetch.webmainbench_latency_diagnostic import paired_tail_main

    argv = paired_inputs(tmp_path)
    fake_paired_environment(monkeypatch, tmp_path, mutate=True)
    with pytest.raises(ValueError, match='inputs or runner changed'):
        paired_tail_main(argv)


def test_historical_formal_correction_rejects_other_versions_before_calls(tmp_path, monkeypatch):
    import pytest
    from harness.web_fetch import webmainbench_latency_diagnostic as diagnostic

    argv = paired_inputs(tmp_path)
    monkeypatch.setattr(diagnostic.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('invalid formal scope must fail before calls')))
    with pytest.raises(ValueError, match='frozen versions'):
        diagnostic.paired_tail_main([*argv, '--rounds', '2', '--formal-correction'])
    assert not (tmp_path / 'output').exists()
