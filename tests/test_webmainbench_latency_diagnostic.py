import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.webmainbench_latency_diagnostic import _resource_samples, load_diagnostic, top_positive_gap_share


class LatencyDiagnosticTests(unittest.TestCase):
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
            with self.assertRaisesRegex(ValueError, 'unchanged official matrix'):
                load_diagnostic(root, {'cases': []}, [], [])
