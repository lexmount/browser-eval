"""Supplement packaging preserves observations before canonical append validation."""
import hashlib
import json
import unittest

from scripts.merge_dynamic_supplement import merge
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
