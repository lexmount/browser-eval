import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from harness.web_fetch.publication import put_file, updated_receipt, upload_large_archive


class ArtifactPublisherTests(unittest.TestCase):
    def test_updated_receipt_uses_activated_version_as_next_baseline(self):
        receipt = updated_receipt(
            {'slug': 'current', 'version': 'ver_legacy', 'versionId': 'ver_stale'},
            {'slug': 'current', 'versionId': 'ver_new'},
        )
        self.assertNotIn('version', receipt)
        self.assertEqual(receipt['versionId'], 'ver_new')
        self.assertEqual(receipt['last_update']['versionId'], 'ver_new')
        with self.assertRaisesRegex(ValueError, 'versionId'):
            updated_receipt(receipt, {'slug': 'current'})

    @patch('harness.web_fetch.publication.time.sleep')
    def test_rate_limit_retries_identical_put(self, sleep):
        request = Mock(side_effect=[HTTPError('https://hub/file', 429, 'limited', {'Retry-After':'2'}, None), (200,b'ok')])
        self.assertEqual(put_file(request, '/file', b'bytes'), (200,b'ok'))
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        sleep.assert_called_once_with(2)

    @patch('harness.web_fetch.publication.time.sleep')
    def test_rate_limit_retry_is_bounded(self, sleep):
        request = Mock(side_effect=HTTPError('https://hub/file', 429, 'limited', {}, None))
        with self.assertRaises(HTTPError):
            put_file(request, '/file', b'bytes')
        self.assertEqual(request.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    @patch('harness.web_fetch.publication.time.sleep')
    def test_long_wait_and_non_retryable_errors_are_not_retried(self, sleep):
        for code, header in [(429,'300'), (429,'NaN'), (403,'1')]:
            request = Mock(side_effect=HTTPError('https://hub/file', code, 'error', {'Retry-After':header}, None))
            with self.assertRaises(HTTPError):
                put_file(request, '/file', b'bytes')
            self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    @patch('harness.web_fetch.publication.time.sleep')
    def test_transient_server_error_retries_once(self, sleep):
        request=Mock(side_effect=[HTTPError('https://hub/file',500,'error',{},None),(200,b'ok')])
        self.assertEqual(put_file(request,'/file',b'bytes'),(200,b'ok'))
        self.assertEqual(request.call_args_list[0],request.call_args_list[1])
        sleep.assert_called_once_with(1.0)

    @patch('harness.web_fetch.publication.time.sleep')
    def test_timeout_retries_identical_put_with_bounded_backoff(self, sleep):
        request=Mock(side_effect=[TimeoutError('lost response'),URLError('network'),(200,b'ok')])
        self.assertEqual(put_file(request,'/file',b'bytes'),(200,b'ok'))
        self.assertEqual(request.call_args_list[0],request.call_args_list[1])
        self.assertEqual(request.call_args_list[1],request.call_args_list[2])
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[1,2])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / 'site.zip'
        with zipfile.ZipFile(self.archive, 'w') as bundle:
            bundle.writestr('packs/output.gz', b'\x00\xffevidence')
            bundle.writestr('index.html', '<head></head>')
        self.calls = []
        self.versions = iter(['ver_old', 'ver_old'])

    def request(self, path, data=None, content_type=None, **kwargs):
        self.calls.append((path, data, kwargs))
        if path == '/api/sites/current':
            result = {'version': {'id': next(self.versions)}}
        elif path.startswith('/api/uploads?'):
            result = {'versionId': 'ver_upload'}
        else:
            result = {'slug': 'current', 'versionId': 'ver_new'}
        return 200, json.dumps(result).encode()

    def test_preserves_paths_bytes_and_existing_site(self):
        result = json.loads(upload_large_archive(self.request, self.archive, 'current', 'ver_old'))
        self.assertEqual(result['versionId'], 'ver_new')
        puts = [call for call in self.calls if call[2].get('method') == 'PUT']
        self.assertEqual(len(puts), 2)
        self.assertEqual(puts[0][0], '/api/uploads/ver_upload/files/index.html')
        self.assertEqual(puts[1][0], '/api/uploads/ver_upload/files/packs/output.gz')
        self.assertEqual(puts[1][1], b'\x00\xffevidence')
        self.assertEqual(json.loads(self.calls[1][1]), {'slug': 'current'})
        self.assertEqual(self.calls[-1][0], '/api/uploads/ver_upload/commit?expected_version=ver_old')

    def test_parallel_upload_finishes_all_files_before_commit(self):
        result = json.loads(upload_large_archive(self.request, self.archive, 'current', 'ver_old', max_workers=2))
        self.assertEqual(result['versionId'], 'ver_new')
        puts = [call for call in self.calls if call[2].get('method') == 'PUT']
        self.assertEqual(puts[0][0], '/api/uploads/ver_upload/files/index.html')
        self.assertEqual({call[0] for call in puts}, {
            '/api/uploads/ver_upload/files/index.html',
            '/api/uploads/ver_upload/files/packs/output.gz',
        })
        self.assertEqual(self.calls[-1][0], '/api/uploads/ver_upload/commit?expected_version=ver_old')

    def test_changed_remote_never_commits(self):
        self.versions = iter(['ver_old', 'ver_someone_else'])
        with self.assertRaisesRegex(RuntimeError, 'Remote version changed'):
            upload_large_archive(self.request, self.archive, 'current', 'ver_old')
        self.assertFalse(any('/commit' in call[0] for call in self.calls))

    def test_upload_failure_never_commits(self):
        def fail_put(*args, **kwargs):
            if kwargs.get('method') == 'PUT':
                raise OSError('Upload failed')
            return self.request(*args, **kwargs)
        with self.assertRaises(OSError):
            upload_large_archive(fail_put, self.archive, 'current', 'ver_old')
        self.assertFalse(any('/commit' in call[0] for call in self.calls))

    def test_unsafe_path_fails_before_starting_session(self):
        with zipfile.ZipFile(self.archive, 'a') as bundle:
            bundle.writestr('../secret', 'no')
        with self.assertRaisesRegex(ValueError, 'Unsafe bundle path'):
            upload_large_archive(self.request, self.archive, 'current', 'ver_old')
        self.assertEqual(len(self.calls), 1)
