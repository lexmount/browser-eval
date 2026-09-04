import hashlib
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from harness.web_fetch.publication_privacy import project_file


class PublicationPrivacyTests(unittest.TestCase):
    def test_auxiliary_html_projection_has_separate_hashes(self):
        key = b'AIzaSy' + b'x' * 33
        original = b'<script>config="' + key + b'"</script>'
        result, row = project_file('artifacts/browser/case/run-001.html.txt', original)
        self.assertNotIn(key, result)
        self.assertIn(key, original)
        self.assertEqual(row['replacements'], 1)
        self.assertEqual(row['original_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(row['published_sha256'], hashlib.sha256(result).hexdigest())
        self.assertNotEqual(row['original_sha256'], row['published_sha256'])
        self.assertNotIn(key.decode(), str(row))

    def test_clean_scored_outputs_are_byte_identical(self):
        data = '正文\n\t <API_KEY>\n'.encode()
        self.assertEqual(project_file('artifacts/browser/case/run-001.md', data), (data, None))
        css = b'.task-list-item-convert-container{display:none}'
        self.assertEqual(project_file('artifacts/browser/case/run-001.txt', css), (css, None))

    def test_scored_or_structured_file_is_never_silently_redacted(self):
        key = b'AIzaSy' + b'x' * 33
        for path in ('results.jsonl', 'dataset.json', 'artifacts/browser/case/run-001.md', 'index.html'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                project_file(path, key)

    def test_non_google_secrets_and_hidden_files_fail_closed(self):
        for value in (b'ahp_' + b'x' * 32, b'-----BEGIN PRIVATE KEY-----'):
            with self.assertRaises(ValueError):
                project_file('sources/case/source.html.txt', value)
        with self.assertRaises(ValueError):
            project_file('.env', b'API_KEY=example')

    def test_packaged_downloads_and_disclosure(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('scripts/package_artifact_hub.py', 'harness/web_fetch/publication_privacy.py',
                             'harness/web_fetch/publication_target.py'):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repository / relative, target)
            (root / 'publish').mkdir()
            source = root / 'reports/webfetch'
            (source / 'assets').mkdir(parents=True)
            (source / 'sources/case').mkdir(parents=True)
            (source / 'assets/style.css').write_text('body {color:black}')
            (source / 'assets/report.js').write_text('')
            (source / '.generated-report').write_text('local marker')
            (source / 'reliability.json').write_text('{"complete":true}')
            (source / 'results.jsonl').write_text('')
            (source / 'index.html').write_text('<!doctype html><html><head><title>Test</title></head><body><a href="sources/case/source.html.txt">HTML</a></body></html>')
            key = b'AIzaSy' + b'x' * 33
            raw = b'<script>key="' + key + b'"</script>'
            raw_path = source / 'sources/case/source.html.txt'
            raw_path.write_bytes(raw)
            subprocess.run([sys.executable, str(root / 'scripts/package_artifact_hub.py')], check=True, capture_output=True)
            self.assertEqual(raw_path.read_bytes(), raw)
            with zipfile.ZipFile(root / 'publish/webfetch-sites.zip') as archive:
                manifest = json.loads(archive.read('manifest.json'))
                def unpack(name):
                    output = bytearray()
                    for key_hash in manifest['files'][name]['parts']:
                        block = manifest['blocks'][key_hash]
                        pack = gzip.decompress(archive.read(block['pack']))
                        output.extend(pack[block['offset']:block['offset'] + block['size']])
                    data = bytes(output)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), manifest['files'][name]['sha256'])
                    return data
                for name in manifest['files']:
                    self.assertNotIn(key, unpack(name))
                privacy = json.loads(unpack('publication-privacy.json'))
                self.assertEqual(len(privacy['files']), 1)
                self.assertIn(b'publication-privacy.json', unpack('index.html'))
                self.assertEqual(unpack('results.jsonl'), b'')
                self.assertNotIn('.generated-report', manifest['files'])


if __name__ == '__main__':
    unittest.main()
