import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from harness.web_fetch.publication import upload_large_archive


class ArtifactPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / 'site.zip'
        with zipfile.ZipFile(self.archive, 'w') as bundle:
            bundle.writestr('index.html', '<head></head>')
            bundle.writestr('packs/output.gz', b'\x00\xffevidence')
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
        self.assertEqual(puts[1][0], '/api/uploads/ver_upload/files/packs/output.gz')
        self.assertEqual(puts[1][1], b'\x00\xffevidence')
        self.assertEqual(json.loads(self.calls[1][1]), {'slug': 'current'})
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
