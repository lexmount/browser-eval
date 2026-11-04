import json
import pytest
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from harness.reporting.publish import put_file, updated_receipt, upload_large_archive


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

    @patch('harness.reporting.publish.time.sleep')
    def test_rate_limit_retries_identical_put(self, sleep):
        request = Mock(side_effect=[HTTPError('https://hub/file', 429, 'limited', {'Retry-After':'2'}, None), (200,b'ok')])
        self.assertEqual(put_file(request, '/file', b'bytes'), (200,b'ok'))
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        sleep.assert_called_once_with(2)

    @patch('harness.reporting.publish.time.sleep')
    def test_rate_limit_retry_is_bounded(self, sleep):
        request = Mock(side_effect=HTTPError('https://hub/file', 429, 'limited', {}, None))
        with self.assertRaises(HTTPError):
            put_file(request, '/file', b'bytes')
        self.assertEqual(request.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    @patch('harness.reporting.publish.time.sleep')
    def test_long_wait_and_non_retryable_errors_are_not_retried(self, sleep):
        for code, header in [(429,'300'), (429,'NaN'), (403,'1')]:
            request = Mock(side_effect=HTTPError('https://hub/file', code, 'error', {'Retry-After':header}, None))
            with self.assertRaises(HTTPError):
                put_file(request, '/file', b'bytes')
            self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    @patch('harness.reporting.publish.time.sleep')
    def test_transient_server_error_retries_once(self, sleep):
        request=Mock(side_effect=[HTTPError('https://hub/file',500,'error',{},None),(200,b'ok')])
        self.assertEqual(put_file(request,'/file',b'bytes'),(200,b'ok'))
        self.assertEqual(request.call_args_list[0],request.call_args_list[1])
        sleep.assert_called_once_with(1.0)

    @patch('harness.reporting.publish.time.sleep')
    def test_timeout_retries_identical_put_with_bounded_backoff(self, sleep):
        request=Mock(side_effect=[TimeoutError('lost response'),URLError('network'),(200,b'ok')])
        self.assertEqual(put_file(request,'/file',b'bytes'),(200,b'ok'))
        self.assertEqual(request.call_args_list[0],request.call_args_list[1])
        self.assertEqual(request.call_args_list[1],request.call_args_list[2])
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[1,2])

    @patch('harness.reporting.publish.time.sleep')
    def test_closed_connection_retries_same_staged_file_without_restarting_upload(self, sleep):
        from http.client import RemoteDisconnected
        request = Mock(side_effect=[RemoteDisconnected('closed before response'), (200, b'ok')])
        self.assertEqual(put_file(request, '/uploads/current/file', b'unchanged bytes'), (200, b'ok'))
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        sleep.assert_called_once_with(1)

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
        elif path.split('?', 1)[0] == '/api/uploads':
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

    def test_new_site_keeps_its_title_with_parallel_upload(self):
        upload_large_archive(self.request, self.archive, title='轨迹重放', max_workers=2)
        self.assertEqual(json.loads(self.calls[0][1]), {'title': '轨迹重放'})
        puts = [call for call in self.calls if call[2].get('method') == 'PUT']
        self.assertEqual(puts[0][0], '/api/uploads/ver_upload/files/index.html')
        self.assertEqual(len(puts), 2)
        self.assertEqual(self.calls[-1][0], '/api/uploads/ver_upload/commit')

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


class UploadResumeTests(unittest.TestCase):
    @patch('harness.reporting.publish.time.sleep')
    def test_verified_stage_is_reused_and_rate_limited_commit_is_retried(self, sleep):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'report.zip'
            contents = {'index.html': b'home', 'evidence.json': b'{"complete":true}'}
            with zipfile.ZipFile(archive, 'w') as bundle:
                for name, body in contents.items(): bundle.writestr(name, body)
            calls = []
            commits = 0
            def request(path, data=None, content_type=None, **kwargs):
                nonlocal commits
                calls.append((path, data, content_type, kwargs))
                if path == '/api/sites/current': result = {'version': {'id': 'ver_old'}}
                elif path.startswith('/api/uploads?'): result = {'versionId': 'ver_stage'}
                elif path == '/api/uploads/ver_stage':
                    result = {'files': [{'relpath': name, 'bytes': len(body),
                              'sha256': hashlib.sha256(body).hexdigest()}
                              for name, body in contents.items()]}
                elif '/commit?' in path:
                    commits += 1
                    if commits == 1:
                        raise HTTPError('https://reports.example'+path, 429, 'limited',
                                        {'Retry-After': '2'}, None)
                    result = {'slug': 'current', 'versionId': 'ver_new'}
                else: self.fail('Unexpected reupload: '+path)
                return 200, json.dumps(result).encode()
            result = upload_large_archive(request, archive, 'current', 'ver_old', resume=True)
            self.assertEqual(json.loads(result)['versionId'], 'ver_new')
            self.assertFalse(any(c[3].get('method') == 'PUT' for c in calls))
            retries = [c for c in calls if '/commit?' in c[0]]
            self.assertEqual(retries[0], retries[1])
            sleep.assert_called_once_with(2)


@patch('harness.reporting.publish.time.sleep')
def test_remote_advance_during_rate_limit_wait_prevents_second_commit(sleep, tmp_path):
    archive = tmp_path / 'site.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', 'checked')
    versions = iter(['ver_old', 'ver_old', 'ver_advanced'])
    commits = []

    def request(path, data=None, content_type=None, **kwargs):
        if path == '/api/sites/current':
            return 200, json.dumps({'version': {'id': next(versions)}}).encode()
        if '/commit?' in path:
            commits.append(path)
            raise HTTPError('https://reports.example' + path, 429, 'limited', {'Retry-After': '2'}, None)
        return 200, b'{"versionId":"ver_stage"}'

    with pytest.raises(RuntimeError, match='Remote version changed'):
        upload_large_archive(request, archive, 'current', 'ver_old')
    assert len(commits) == 1
