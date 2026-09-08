import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.verify_artifact_hub import verify_public_files


class PublicationVerifyTests(unittest.TestCase):
    def verify(self, served=b'const report=true;', changed_version=False):
        metadata_calls=0
        def response(request, timeout):
            nonlocal metadata_calls
            if isinstance(request, str):
                return io.BytesIO(served)
            metadata_calls+=1
            return io.BytesIO(json.dumps({'files':['index.html','loader.js'],
                'file':'index.html','content':'<html>报告</html>',
                'version':{'id':'ver_second' if changed_version and metadata_calls>1 else 'ver_first'}}).encode())
        with tempfile.TemporaryDirectory() as temp:
            archive=Path(temp)/'site.zip'
            with zipfile.ZipFile(archive,'w') as bundle:
                bundle.writestr('index.html','<html>报告</html>')
                bundle.writestr('loader.js',b'const report=true;')
            with patch('scripts.verify_artifact_hub.load_token',return_value='fixture'), \
                 patch('scripts.verify_artifact_hub.urllib.request.urlopen',side_effect=response):
                return verify_public_files('test',archive)

    def test_complete_matching_file_set(self):
        self.assertEqual(self.verify()['files'],2)

    def test_modified_bytes_fail(self):
        with self.assertRaisesRegex(ValueError,'Remote file differs'):
            self.verify(served=b'changed')

    def test_concurrent_version_change_fails(self):
        with self.assertRaisesRegex(ValueError,'version changed'):
            self.verify(changed_version=True)
