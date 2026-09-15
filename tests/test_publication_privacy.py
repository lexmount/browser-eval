import hashlib
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from harness.web_fetch.publication_privacy import project_file, publication_risk
from scripts.check_publication_bundle import scored_output_hash


class PublicationPrivacyTests(unittest.TestCase):
    def test_scored_bundle_requires_an_output_hash(self):
        self.assertEqual(scored_output_hash({'output_sha256': 'hash'}), 'hash')
        self.assertEqual(scored_output_hash({'metrics': {'content_sha256': 'fallback'}}), 'fallback')
        with self.assertRaisesRegex(ValueError, 'missing scored output hash'):
            scored_output_hash({'artifact_path': 'artifacts/case.md'})

    def test_loader_localizes_async_status_and_accessibility_copy(self):
        source=(Path(__file__).resolve().parents[1]/'scripts/package_artifact_hub.py').read_text()
        self.assertIn("function tr(zh,en)",source)
        for english in ('Failed to load the data catalog','Load complete content',
                        'Loading complete content…','Complete actual output',
                        'Preparing complete file…'):
            self.assertIn(english,source)

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

    def test_exactly_replaceable_credentials_are_audited_in_every_public_file(self):
        key = b'AIzaSy' + b'x' * 33
        for path in ('results.jsonl', 'dataset.json', 'artifacts/browser/case/run-001.md', 'index.html'):
            with self.subTest(path=path):
                projected, row = project_file(path, key)
                self.assertNotIn(key, projected)
                self.assertIn('credential', row['kinds'])

    def test_non_google_secrets_and_hidden_files_fail_closed(self):
        for value in (b'ahp_' + b'x' * 32, b'AKIA' + b'X' * 16,
                      b'xoxb-' + b'x' * 28, b'-----BEGIN PRIVATE KEY-----'):
            with self.assertRaises(ValueError):
                project_file('sources/case/source.html.txt', value)
        with self.assertRaises(ValueError):
            project_file('.env', b'API_KEY=example')
        with self.assertRaisesRegex(ValueError, 'Private evidence is not publishable'):
            project_file('private_artifacts/tavily/case/run-001.response.bin', b'raw envelope')

    def test_public_bundle_rejects_real_auth_values_and_local_user_paths(self):
        self.assertEqual(publication_risk(b'Authorization: Bearer <API_TOKEN>'), None)
        self.assertEqual(publication_risk(b'Authorization: Bearer abcdefghijklmnop'),
                         'authorization header value')
        self.assertEqual(publication_risk(b'/Users/example/private/file.txt'),
                         'machine-local user path')
        projected_path, path_row = project_file(
            'execution.json', b'{"source":"/Users/example/private/file.txt"}')
        self.assertNotIn(b'/Users/example/', projected_path)
        self.assertIn(b'/[REDACTED_USER]/', projected_path)
        self.assertEqual(path_row['replacements'], 1)
        public_example = b'Proxy-Authorization: basic aGVsbG86d29ybGQ='
        self.assertEqual(publication_risk(public_example), None)
        self.assertEqual(project_file('artifacts/browser/case/run-001.txt', public_example),
                         (public_example, None))
        projected, row = project_file(
            'artifacts/browser/case/run-001.html.txt',
            b'Authorization: Basic QUJDREVGR0hJSktMTU5P')
        self.assertNotIn(b'QUJDREVGR0hJSktMTU5P', projected)
        self.assertEqual(row['replacements'], 1)
        auth_projected, auth_row = project_file(
            'results.jsonl', b'Authorization: Bearer abcdefghijklmnop')
        self.assertNotIn(b'abcdefghijklmnop', auth_projected)
        self.assertIn('credential', auth_row['kinds'])

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
            (source / 'guide').mkdir(parents=True)
            (source / 'attempts').mkdir(parents=True)
            (source / 'datasets').mkdir(parents=True)
            (source / 'references').mkdir(parents=True)
            (source / 'artifacts/browser/case').mkdir(parents=True)
            (source / 'call-logs/browser/case').mkdir(parents=True)
            (source / 'assets/style.css').write_text('body {color:black}')
            (source / 'assets/report.js').write_text(
                "window.addEventListener('report-ready',initReport);\n"
                "window.addEventListener('DOMContentLoaded',initReport);\n"
            )
            (source / '.generated-report').write_text('local marker')
            (source / 'reliability.json').write_text('{"complete":true}')
            (source / 'result-review.json').write_text('{"status":"passed","failures":[]}')
            call_logs=[b'{"event":"failed"}\n',b'{"event":"complete"}\n']
            call_log_paths=['call-logs/browser/case/run-001-physical-001.json',
                            'call-logs/browser/case/run-001-physical-002.json']
            call_log_bindings=[]
            for log_path,call_log in zip(call_log_paths,call_logs):
                (source/log_path).write_bytes(call_log)
                call_log_bindings.append({'path':log_path,'sha256':hashlib.sha256(call_log).hexdigest(),
                                          'bytes':len(call_log)})
            record = {'attempt_id': 'browser__case__r001', 'case_id': 'case',
                      'artifact_path': 'artifacts/browser/case/run-001.txt',
                      'provider_metadata': {'route':'markdown',
                                            'raw_response_path':'private_artifacts/secret.bin',
                                            'retry':{'attempts':[{'call_log':binding}
                                                                 for binding in call_log_bindings]},
                                            'call_log':call_log_bindings[-1]}}
            file_record = {'attempt_id': 'browser__file-case__r001', 'case_id': 'file-case',
                           'artifact_path': 'artifacts/browser/file-case/run-001.txt',
                           'provider_metadata': {}}
            results = json.dumps(record) + '\n' + json.dumps(file_record) + '\n'
            (source / 'results.jsonl').write_text(results)
            (source / 'references/case.txt').write_text('complete reference')
            (source / 'references/file-case.txt').write_text('file acceptance rule')
            (source / 'references/md-case.md').write_text('markdown ground truth')
            unusual_case = r'backslash\1'
            (source / 'references' / f'{unusual_case}.txt').write_text('literal backslash reference')
            (source / 'artifacts/browser/case/run-001.txt').write_text('actual output')
            (source / 'artifacts/browser/file-case').mkdir(parents=True)
            (source / 'artifacts/browser/file-case/run-001.txt').write_text('')
            (source / 'attempts/browser__case__r001.html').write_text(
                '<html><body><pre data-kind="reference">complete reference</pre>'
                '<pre data-kind="output">actual output</pre>'
                '<pre data-kind="reference">derived body region</pre>'
                '<pre data-kind="metadata">{}</pre>'
                '<dialog><pre data-kind="call-output">actual output</pre>'
                '<pre data-kind="call-metadata">{}</pre>'
                '<pre data-kind="call-log">{"event":"failed"}</pre>'
                '<pre data-kind="call-log">{"event":"complete"}</pre></dialog></body></html>')
            (source / 'attempts/browser__file-case__r001.html').write_text(
                '<html><body><h2>目标文件与实际返回</h2>'
                '<pre data-kind="metadata">{}</pre></body></html>')
            (source / 'datasets/md-case.html').write_text(
                '<html><body><h2>Source metadata</h2><pre>{"level":"hard"}</pre>'
                '<h2>Official human ground truth</h2><pre>markdown ground truth</pre></body></html>')
            (source / 'datasets' / f'{unusual_case}.html').write_text(
                '<html><body><h2>Official human ground truth</h2>'
                '<pre>literal backslash reference</pre></body></html>')
            (source / 'index.html').write_text('<!doctype html><html><head><title>Test</title></head><body><a href="sources/case/source.html.txt">HTML</a></body></html>')
            (source / 'guide/article.html').write_text('<!doctype html><html><body>Article</body></html>')
            key = b'AIzaSy' + b'x' * 33
            raw = b'<script>key="' + key + b'"</script>'
            raw_path = source / 'sources/case/source.html.txt'
            raw_path.write_bytes(raw)
            malicious = 'https://example.test/" onmouseover="alert(1)'
            env = {**os.environ, 'WEBFETCH_REPORT_PUBLIC_URL': malicious}
            subprocess.run([sys.executable, str(root / 'scripts/package_artifact_hub.py')], check=True,
                           capture_output=True, env=env)
            self.assertEqual(raw_path.read_bytes(), raw)
            with zipfile.ZipFile(root / 'publish/webfetch-sites.zip') as archive:
                manifest = json.loads(archive.read('manifest.json'))
                self.assertEqual(manifest['architecture']['kind'],
                                 'single_html_shell_with_lazy_virtual_routes')
                self.assertEqual(manifest['architecture']['logical_routes'], 6)
                self.assertEqual(
                    archive.read('report.js').decode().count(
                        "window.addEventListener('report-ready',initReport);"
                    ), 1,
                )
                self.assertNotIn(
                    "window.addEventListener('DOMContentLoaded',initReport);",
                    archive.read('report.js').decode(),
                )
                self.assertGreaterEqual(manifest['architecture']['content_packs'], 1)
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
                self.assertNotIn(b'publication-privacy.json', unpack('index.html'))
                self.assertNotIn('接口账号使用占位符'.encode(), unpack('index.html'))
                article = unpack('guide/article.html')
                self.assertNotIn(malicious.encode(), article)
                self.assertEqual(unpack('results.jsonl'), results.encode())
                attempt = unpack('attempts/browser__case__r001.html')
                self.assertEqual(attempt.count(b'data-file="references/case.txt"'), 1)
                self.assertEqual(attempt.count(b'data-file="artifacts/browser/case/run-001.txt"'), 2)
                self.assertEqual(attempt.count(b'data-record="1"'), 2)
                for call_log_path in call_log_paths:
                    self.assertEqual(attempt.count(f'data-file="{call_log_path}"'.encode()), 1)
                self.assertIn(b'derived body region', attempt)
                self.assertNotIn(b'>actual output</pre>', attempt)
                dataset_page = unpack('datasets/md-case.html')
                self.assertIn(b'<pre>{"level":"hard"}</pre>', dataset_page)
                self.assertIn(b'data-file="references/md-case.md"', dataset_page)
                self.assertNotIn(b'>markdown ground truth</pre>', dataset_page)
                unusual_page = unpack(f'datasets/{unusual_case}.html')
                self.assertIn(f'data-file="references/{unusual_case}.txt"'.encode(), unusual_page)
                self.assertNotIn(b'>literal backslash reference</pre>', unusual_page)
                self.assertNotIn('.generated-report', manifest['files'])


if __name__ == '__main__':
    unittest.main()
