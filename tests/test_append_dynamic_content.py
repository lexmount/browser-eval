import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.append_dynamic_content import append


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class AppendDynamicContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.reference, self.measured = [self.root/name for name in ('source', 'reference', 'measured')]
        self.previous = {'attempt_id': 'old-run', 'provider': 'p', 'case_id': 'old'}
        dump(self.source/'dataset.json', {'cases': [{'id': 'old', 'track': 'web'}]})
        # Nonstandard whitespace is intentional: existing records stay byte-identical.
        self.previous_text = json.dumps(self.previous, indent=None, separators=(', ', ': '))+'\n'
        (self.source/'results.jsonl').write_text(self.previous_text)
        dump(self.source/'execution.json', {'batches': [{'attempt_ids': ['old-run']}]})
        (self.source/'unrelated.txt').write_text('retain me')
        self.cases, self.checks, self.records = [], [], []
        for key in ('one', 'two'):
            rendered = f'{key} complete record'
            initial = '<main>Loading</main>'
            case = {
                'id': key, 'name': key, 'url': f'https://example.test/{key}',
                'category': 'async', 'track': 'dynamic', 'task_kind': 'body',
                'expected_any': [], 'required_facts': [{'id': 'record', 'alternatives': [rendered]}],
                'min_content_chars': 5, 'fact_coverage_threshold': 1.0,
                'ground_truth_main_content': rendered,
                'source_benchmark': {'capture_scope': 'one complete record', 'expected_entries': 1},
                'source_verification': {'reference_sha256': digest(rendered)},
            }
            self.cases.append(case)
            folder = self.reference/'sources'/key
            folder.mkdir(parents=True)
            (folder/'initial.html.txt').write_text(initial)
            (folder/'rendered.txt').write_text(rendered)
            (folder/'reference.txt').write_text(rendered)
            dump(folder/'evidence.json', {
                'initial_sha256': digest(initial), 'rendered_sha256': digest(rendered),
                'reference_sha256': digest(rendered),
            })
            self.checks.append({'id': key, 'entries': [rendered], 'satisfied': True,
                                'timeout_ms': 1000, 'elapsed_ms': 10, 'checked_at': '2026-09-03'})
            artifact = f'artifacts/p/{key}/run.txt'
            (self.measured/artifact).parent.mkdir(parents=True)
            (self.measured/artifact).write_text(rendered)
            self.records.append({'attempt_id': f'p-{key}', 'provider': 'p', 'case_id': key,
                                 'artifact_path': artifact,
                                 'metrics': {'content_sha256': digest(rendered), 'content_pass': True}})
        self.save_datasets()
        self.save_records()
        self.manifest = {'providers': [{'name': 'p'}], 'dataset': {'case_count': 2},
                         'attempt_ids': [r['attempt_id'] for r in self.records], 'attempt_count': 2}
        dump(self.measured/'manifest.json', self.manifest)

    def save_datasets(self):
        for directory in (self.reference, self.measured):
            dump(directory/'dataset.json', {'cases': self.cases})

    def save_records(self):
        (self.measured/'results.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.records))

    def run_append(self):
        append(self.source, self.reference, self.measured, self.checks)

    def snapshot(self):
        return {str(p.relative_to(self.source)): p.read_bytes()
                for p in self.source.rglob('*') if p.is_file()}

    def assert_preflight_rejects(self, message=None):
        before = self.snapshot()
        with patch('scripts.append_dynamic_content.tempfile.mkdtemp') as stage:
            with self.assertRaises(ValueError) as caught:
                self.run_append()
            if message:
                self.assertIn(message, str(caught.exception))
            stage.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_success_preserves_records_and_ignores_new_attestation(self):
        reference_cases = copy.deepcopy(self.cases)
        reference_cases[0]['source_benchmark']['render_evidence'] = {'new_attestation': True}
        dump(self.reference/'dataset.json', {'cases': reference_cases})
        self.run_append()
        text = (self.source/'results.jsonl').read_text()
        self.assertTrue(text.startswith(self.previous_text))
        self.assertEqual([json.loads(line) for line in text.splitlines()][1:], self.records)
        self.assertEqual((self.source/'unrelated.txt').read_text(), 'retain me')
        self.assertEqual(json.loads((self.source/'dataset.json').read_text())['coverage_plan'],
                         {'web': ['p'], 'dynamic': ['p']})
        for case in self.cases:
            audit = json.loads((self.source/'sources'/case['id']/'render-audit.json').read_text())
            self.assertTrue(audit['valid'])
        self.assertEqual(list(self.root.glob('.source-append-*')), [])

    def test_already_merged_refuses_without_staging(self):
        self.run_append()
        self.assert_preflight_rejects('already present')

    def test_identity_reference_and_scoring_changes_rejected(self):
        for field, value in (
            ('url', 'https://example.test/different'), ('ground_truth_main_content', 'changed'),
            ('required_facts', []), ('min_content_chars', 1), ('fact_coverage_threshold', .5),
            ('expected_resources', [{'url': 'https://example.test/file'}]),
            ('required_patterns', [{'pattern': 'different'}]), ('task_kind', 'file'),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.cases)
                changed[0][field] = value
                dump(self.measured/'dataset.json', {'cases': changed})
                self.assert_preflight_rejects('frozen reference')

    def test_changed_scope_rejected(self):
        changed = copy.deepcopy(self.cases)
        changed[0]['source_benchmark']['expected_entries'] = 2
        dump(self.measured/'dataset.json', {'cases': changed})
        self.assert_preflight_rejects('frozen reference')

    def test_duplicate_cases_rejected(self):
        dump(self.reference/'dataset.json', {'cases': self.cases + [self.cases[0]]})
        self.assert_preflight_rejects('duplicate supplement cases')

    def test_invalid_execution_preflight(self):
        (self.source/'execution.json').write_text('invalid JSON')
        self.assert_preflight_rejects()

    def test_invalid_manifest_preflight(self):
        self.manifest['attempt_ids'] = ['wrong']
        dump(self.measured/'manifest.json', self.manifest)
        self.assert_preflight_rejects('manifest mismatch')

    def test_corrupt_artifact_preflight(self):
        (self.measured/self.records[-1]['artifact_path']).write_text('corrupt')
        self.assert_preflight_rejects('Output hash mismatch')

    def test_corrupt_evidence_hash_preflight(self):
        (self.reference/'sources/two/initial.html.txt').write_text('<main>different</main>')
        self.assert_preflight_rejects('Source evidence hash mismatch')

    def test_corrupt_reference_file_preflight(self):
        (self.reference/'sources/two/reference.txt').write_text('corrupt')
        self.assert_preflight_rejects('Frozen reference hash mismatch')

    def test_late_source_collision_preflight(self):
        dump(self.source/'sources/two/evidence.json', {'collision': True})
        self.assert_preflight_rejects('Source evidence collision')

    def test_output_collision_preflight(self):
        target = self.source/self.records[-1]['artifact_path']
        target.parent.mkdir(parents=True)
        target.write_text('existing')
        self.assert_preflight_rejects('Output collision')

    def test_duplicate_artifact_paths_preflight(self):
        self.records[-1]['artifact_path'] = self.records[0]['artifact_path']
        self.save_records()
        self.assert_preflight_rejects('Duplicate supplement artifact paths')

    def test_staging_write_failure_keeps_original(self):
        before = self.snapshot()
        with patch('scripts.append_dynamic_content.shutil.copy2', side_effect=OSError('copy failed')):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                self.run_append()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.root.glob('.source-append-*')), [])

    def test_promotion_failure_restores_original(self):
        before = self.snapshot()
        rename = Path.rename

        def fail_promotion(path, target):
            if path.name == 'staged':
                raise OSError('promotion failed')
            return rename(path, target)

        with patch.object(Path, 'rename', fail_promotion):
            with self.assertRaisesRegex(OSError, 'promotion failed'):
                self.run_append()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.root.glob('.source-append-*')), [])
        self.run_append()  # Recovered state supports a clean retry.

    def test_failed_rollback_retains_recoverable_original(self):
        before = self.snapshot()
        rename = Path.rename

        def fail_promotion_and_rollback(path, target):
            if path.name in {'staged', 'original'}:
                raise OSError('rename failed')
            return rename(path, target)

        with patch.object(Path, 'rename', fail_promotion_and_rollback):
            with self.assertRaisesRegex(RuntimeError, 'original data retained at'):
                self.run_append()
        backup, = self.root.glob('.source-append-*/original')
        self.assertEqual({str(p.relative_to(backup)): p.read_bytes()
                          for p in backup.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
