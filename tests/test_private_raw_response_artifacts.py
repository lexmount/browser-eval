import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.artifacts import (
    materialize_private_artifacts, verify_record_private_artifacts,
    verify_record_public_call_logs,
)
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.artifacts import artifact_bindings, materialize_bound_artifacts


class RawEnvelopeProvider(Provider):
    def __init__(self, raw: bytes, *, digest: str | None = None, size: int | None = None):
        super().__init__('raw-envelope', {'type': 'fixture'})
        self.raw = raw
        self.digest = digest or hashlib.sha256(raw).hexdigest()
        self.size = len(raw) if size is None else size

    def fetch(self, case, timeout):
        return ProviderResult(True, 'Expected', metadata={
            '_raw_response_b64': base64.b64encode(self.raw).decode('ascii'),
            'raw_response_sha256': self.digest,
            'raw_response_bytes': self.size,
        })


class DiagnosticLogProvider(Provider):
    def __init__(self, raw: bytes):
        super().__init__('diagnostic-log', {'type': 'fixture'})
        self.raw = raw

    def fetch(self, case, timeout):
        return ProviderResult(True, 'Expected', metadata={
            '_diagnostic_log_b64': base64.b64encode(self.raw).decode('ascii'),
            'diagnostic_log_sha256': hashlib.sha256(self.raw).hexdigest(),
            'diagnostic_log_bytes': len(self.raw),
            'diagnostic_log_truncated': False,
            'diagnostic_log_process_completed': True,
        })


def run_raw(root: Path, provider: Provider):
    return run_evaluation(
        output_dir=root, dataset_meta={'id': 'test'}, dataset_path=Path('dataset.json'),
        cases=[EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])],
        providers=[provider], provider_config_path=Path('providers.json'),
        runs=1, timeout=2, delay=0,
    )[0][0]


class PrivateRawResponseArtifactTests(unittest.TestCase):
    def test_runner_persists_complete_diagnostic_log_for_success(self):
        raw = b'line one\nline two\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run_raw(root, DiagnosticLogProvider(raw))
            metadata = record['provider_metadata']
            self.assertNotIn('_diagnostic_log_b64', metadata)
            self.assertEqual(metadata['diagnostic_log_bytes'], len(raw))
            self.assertEqual(metadata['diagnostic_log_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertFalse(metadata['diagnostic_log_truncated'])
            path = Path(metadata['diagnostic_log_path'])
            self.assertEqual(path.parts[0], 'private_artifacts')
            self.assertEqual(path.name, 'run-001.stderr.log')
            self.assertEqual((root / path).read_bytes(), raw)
            call_log = json.loads(
                (root / metadata['call_log']['path']).read_text(encoding='utf-8')
            )
            self.assertEqual(
                call_log['capture']['diagnostic_log'],
                'retained_private_hash_bound_complete',
            )
            verify_record_public_call_logs(root, record)
            verify_record_private_artifacts(root, record)
            destination = root / 'aggregate'
            destination.mkdir()
            materialize_private_artifacts(destination, root, [record])
            self.assertEqual((destination / path).read_bytes(), raw)
            (destination / path).write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'byte size changed|hash changed'):
                verify_record_private_artifacts(destination, record)

    def test_runner_moves_raw_envelope_out_of_results(self):
        raw = b'{"authorization":"Bearer private-token-123456789"}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run_raw(root, RawEnvelopeProvider(raw))
            serialized = (root / 'results.jsonl').read_bytes()
            self.assertNotIn(raw, serialized)
            self.assertNotIn(base64.b64encode(raw), serialized)
            metadata = record['provider_metadata']
            self.assertNotIn('_raw_response_b64', metadata)
            self.assertEqual(metadata['raw_response_bytes'], len(raw))
            self.assertEqual(metadata['raw_response_sha256'], hashlib.sha256(raw).hexdigest())
            path = Path(metadata['raw_response_path'])
            self.assertEqual(path.parts[0], 'private_artifacts')
            self.assertEqual((root / path).read_bytes(), raw)
            verify_record_private_artifacts(root, record)

    def test_runner_rejects_wrong_provider_hash_or_size(self):
        for provider, message in [
            (RawEnvelopeProvider(b'raw', digest='0' * 64), 'hash mismatch'),
            (RawEnvelopeProvider(b'raw', size=4), 'byte size mismatch'),
        ]:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, message):
                    run_raw(Path(directory), provider)

    def test_retry_retains_each_physical_raw_envelope(self):
        class Retried(Provider):
            def __init__(self):
                super().__init__('retried', {'type': 'fixture'})
                self.calls = 0

            def fetch(self, case, timeout):
                self.calls += 1
                raw = f'private-{self.calls}'.encode()
                return ProviderResult(self.calls == 2, 'wrong' if self.calls == 1 else 'Expected',
                    metadata={
                        '_raw_response_b64': base64.b64encode(raw).decode('ascii'),
                        'raw_response_sha256': hashlib.sha256(raw).hexdigest(),
                        'raw_response_bytes': len(raw),
                    })

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run_evaluation(
                output_dir=root, dataset_meta={'id': 'test'}, dataset_path=Path('dataset.json'),
                cases=[EvalCase('case', 'Case', 'https://example.com', 'static', ['Expected'])],
                providers=[Retried()], provider_config_path=Path('providers.json'),
                runs=1, timeout=2, delay=0, max_failure_retries=1,
            )[0][0]
            attempts = record['provider_metadata']['retry']['attempts']
            self.assertEqual(len(attempts), 2)
            self.assertEqual(
                [(root / row['private_artifacts'][0]['path']).read_bytes() for row in attempts],
                [b'private-1', b'private-2'],
            )
            verify_record_private_artifacts(root, record)

    def test_aggregate_copies_verified_private_evidence(self):
        raw = b'private raw response'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'worker', root / 'aggregate'
            destination.mkdir()
            record = run_raw(source, RawEnvelopeProvider(raw))
            materialize_private_artifacts(destination, source, [record])
            relative = record['provider_metadata']['raw_response_path']
            self.assertEqual((destination / relative).read_bytes(), raw)

    def test_worker_aggregation_keeps_private_binding_out_of_public_artifact_list(self):
        raw = b'private raw response'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker, aggregate = root / 'worker', root / 'aggregate'
            aggregate.mkdir()
            record = run_raw(worker, RawEnvelopeProvider(raw))
            private = record['provider_metadata']['raw_response_path']
            self.assertNotIn(private, {path for path, _ in artifact_bindings(record)})
            materialize_bound_artifacts(aggregate, worker, [record])
            self.assertEqual((aggregate / private).read_bytes(), raw)

    def test_missing_tampered_and_traversal_private_evidence_fail_closed(self):
        raw = b'private raw response'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run_raw(root, RawEnvelopeProvider(raw))
            relative = record['provider_metadata']['raw_response_path']
            path = root / relative
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'missing or unsafe'):
                verify_record_private_artifacts(root, record)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run_raw(root, RawEnvelopeProvider(raw))
            path = root / record['provider_metadata']['raw_response_path']
            path.write_bytes(b'tampered raw response')
            with self.assertRaisesRegex(ValueError, '(byte size|hash) changed'):
                verify_record_private_artifacts(root, record)

        digest = hashlib.sha256(raw).hexdigest()
        traversal = {'provider_metadata': {
            'raw_response_path': 'private_artifacts/../../secret',
            'raw_response_sha256': digest,
            'raw_response_bytes': len(raw),
        }}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'stay inside'):
                verify_record_private_artifacts(Path(directory), traversal)

    def test_symlink_private_evidence_is_rejected(self):
        raw = b'private raw response'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = run_raw(root, RawEnvelopeProvider(raw))
            path = root / record['provider_metadata']['raw_response_path']
            outside = root / 'outside'
            outside.write_bytes(raw)
            path.unlink()
            path.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, '(escapes|missing or unsafe)'):
                verify_record_private_artifacts(root, record)

    def test_partial_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            record = {'provider_metadata': {
                'raw_response_path': 'private_artifacts/p/c/raw.bin',
                'raw_response_sha256': '0' * 64,
            }}
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                verify_record_private_artifacts(Path(directory), record)


if __name__ == '__main__':
    unittest.main()
