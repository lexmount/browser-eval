"""Supplement packaging preserves observations before canonical append validation."""
import hashlib
import json
import unittest
from unittest.mock import patch

from harness.web_fetch.dynamic_cohort import append, merge
from tests import test_dynamic_append_batches as fixtures


class DynamicMergeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DynamicAppendBatchesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.output = self.fixture.root / 'assembled'

    def assemble(self, runs=None):
        merge(self.fixture.reference, runs or [self.fixture.measured], self.output)

    def test_keeps_every_measurement_and_original_batch_identity(self):
        self.assemble()
        source = self.fixture.measured
        self.assertEqual((source/'results.jsonl').read_bytes(), (self.output/'results.jsonl').read_bytes())
        before = json.loads((source/'execution.json').read_text())['batches']
        after = json.loads((self.output/'execution.json').read_text())['batches']
        for a, b in zip(before, after, strict=True):
            for key in ('batch_id', 'attempt_ids', 'started_at', 'completed_at', 'providers', 'parameters'):
                self.assertEqual(a[key], b[key])
        for record in self.fixture.records:
            self.assertEqual(hashlib.sha256((self.output/record['artifact_path']).read_bytes()).hexdigest(),
                             record['metrics']['content_sha256'])

    def test_rejects_changed_raw_evidence_before_creating_output(self):
        path = self.fixture.measured / self.fixture.records[0]['artifact_path']
        path.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_rejects_duplicate_run(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate artifact'):
            self.assemble([self.fixture.measured, self.fixture.measured])
        self.assertFalse(self.output.exists())

    def test_refuses_to_overwrite_existing_output(self):
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_text('existing')
        with self.assertRaises(FileExistsError):
            self.assemble()
        self.assertEqual(marker.read_text(), 'existing')

    def test_empty_or_nested_assembly_cannot_modify_input_evidence(self):
        before = self.fixture.snapshot(self.fixture.measured)
        with self.assertRaisesRegex(ValueError, 'At least one'):
            merge(self.fixture.reference, [], self.output)
        nested = self.fixture.measured / 'nested-output'
        with self.assertRaisesRegex(ValueError, 'nested'):
            merge(self.fixture.reference, [self.fixture.measured], nested)
        self.assertEqual(self.fixture.snapshot(self.fixture.measured), before)
        self.assertFalse(self.output.exists())
        self.assertFalse(nested.exists())

    def test_linked_input_evidence_is_rejected(self):
        linked = self.fixture.root / 'linked-run'
        linked.symlink_to(self.fixture.measured, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.assemble([linked])
        self.assertFalse(self.output.exists())

    def test_preserves_all_declared_auxiliary_and_private_evidence(self):
        row = self.fixture.records[0]
        public = 'artifacts/extra/stdout.txt'
        private = 'private_artifacts/extra/response.json'
        for relative, raw in [(public, b'raw process output'), (private, b'{"response":1}')]:
            path = self.fixture.measured / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        row['provider_metadata'].update(
            raw_stdout_path=public,
            raw_stdout_sha256=hashlib.sha256(b'raw process output').hexdigest(),
            raw_response_path=private,
            raw_response_sha256=hashlib.sha256(b'{"response":1}').hexdigest(),
            raw_response_bytes=len(b'{"response":1}'),
        )
        for kind in ('retry', 'delivered'):
            relative = f'artifacts/extra/{kind}.txt'
            raw = kind.encode()
            (self.fixture.measured / relative).write_bytes(raw)
            binding = {'artifact_path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
            if kind == 'retry':
                row['provider_metadata']['retry'] = {'attempts': [{
                    'artifact_path': relative, 'content_sha256': binding['sha256'],
                    'artifact_paths': [relative],
                }]}
            else:
                row['provider_metadata']['file_evaluation'] = {'files': [binding]}
        self.fixture.save()
        self.assemble()
        for relative in (public, private, 'artifacts/extra/retry.txt', 'artifacts/extra/delivered.txt'):
            self.assertEqual((self.output / relative).read_bytes(),
                             (self.fixture.measured / relative).read_bytes())
        append(self.fixture.source, self.fixture.reference, self.output, self.fixture.checks)
        for relative in (public, private, 'artifacts/extra/retry.txt', 'artifacts/extra/delivered.txt'):
            self.assertEqual((self.fixture.source / relative).read_bytes(),
                             (self.fixture.measured / relative).read_bytes())

    def test_rejects_missing_declared_auxiliary_evidence_before_output(self):
        self.fixture.records[0]['provider_metadata'].update(
            raw_stdout_path='artifacts/absent.txt', raw_stdout_sha256='a' * 64)
        self.fixture.save()
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_copy_failure_does_not_expose_a_partial_output(self):
        with patch('harness.web_fetch.dynamic_cohort.shutil.copy2', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.assemble()
        self.assertFalse(self.output.exists())

    def test_retains_original_record_bytes(self):
        source = self.fixture.measured / 'results.jsonl'
        raw = b'\r\n'.join(json.dumps(row, separators=(', ', ': ')).encode()
                            for row in self.fixture.records) + b'\r\n'
        source.write_bytes(raw)
        self.assemble()
        self.assertEqual((self.output / 'results.jsonl').read_bytes(), raw)

    def test_merged_batch_keeps_verifiable_reference_observations(self):
        from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
        from tests.test_reference_lifecycle import ReferenceLifecycleTests

        fixture = ReferenceLifecycleTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        manifest = fixture.verified_run()
        (fixture.run / 'execution.json').write_text(json.dumps({'batches': [manifest]}))
        merge(fixture.root, [fixture.run], self.output)
        relative = manifest['reference_checks']['postcheck']['evidence_path']
        self.assertEqual((self.output / relative).read_bytes(), (fixture.run / relative).read_bytes())
        dataset = json.loads((self.output / 'dataset.json').read_bytes())
        records = [json.loads(line) for line in (self.output / 'results.jsonl').read_bytes().splitlines()]
        batches = json.loads((self.output / 'execution.json').read_bytes())['batches']
        require_verified_reference_checks(batches, cases=dataset, records=records, source=self.output)

    def test_changed_scoring_and_retrieval_contracts_cannot_reuse_measurements(self):
        path = self.fixture.measured / 'dataset.json'
        original = path.read_bytes()
        for field, value in [
            ('acceptance_contract', {'minimum_records': 20}),
            ('body_requirements', {'minimum_coverage': 1}),
            ('composite_acceptance', {'groups': [{'id': 'required'}]}),
            ('mutable_body_policy', {'mode': 'target_only'}),
            ('evaluation_url', 'https://example.test/different-input'),
            ('expected_final_url', 'https://example.test/different-output'),
            ('target_region', 'different-region'),
        ]:
            with self.subTest(field=field):
                dataset = json.loads(original)
                dataset['cases'][0][field] = value
                path.write_text(json.dumps(dataset))
                with self.assertRaisesRegex(ValueError, 'frozen cases'):
                    self.assemble()
                self.assertFalse(self.output.exists())
        path.write_bytes(original)
