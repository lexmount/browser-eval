import base64
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from harness.web_fetch.publication_privacy import project_file, publication_risk
from scripts.check_publication_bundle import check as check_bundle
from scripts.check_publication_bundle import scored_output_hash


class PublicationPrivacyTests(unittest.TestCase):
    def test_entity_escaped_fields_inside_json_logs_preserve_json_and_mask_values(self):
        for quote in ('&quot;','&#34;','&#x22;','&apos;','&#39;','&#x27;'):
            with self.subTest(quote=quote):
                text=f'embedded configuration {{{quote}token{quote}: {quote}canary-private-123{quote}}}'
                raw=json.dumps({'stderr':text,'records':26}).encode()
                self.assertEqual(publication_risk(raw),'sensitive field value')
                projected,receipt=project_file('logs/call.json',raw)
                self.assertNotIn(b'canary-private-123',projected)
                self.assertEqual(json.loads(projected)['records'],26)
                self.assertIsNone(publication_risk(projected))
                self.assertEqual(receipt['replacements'],1)
                self.assertEqual(project_file('logs/call.json',projected),(projected,None))

    def test_json_url_redaction_preserves_escaped_quotes_and_line_boundaries(self):
        value = {'log': 'src="https://example.test/map?signature=secret"\n下一行\u2028正文'}
        original = (json.dumps(value, ensure_ascii=True) + '\n').encode()
        projected, receipt = project_file('results.jsonl', original)
        self.assertEqual(len(projected.splitlines()), 1)
        self.assertEqual(json.loads(projected)['log'],
                         'src="https://example.test/map?signature=[REDACTED]"\n下一行\u2028正文')
        self.assertIsNotNone(receipt)
        self.assertNotIn(b'secret', projected)
        self.assertEqual(json.loads(original), value)

    def test_json_escaped_url_queries_are_scanned_and_projected(self):
        for url in (
            'https://example.org/新闻?token=REVIEW_SECRET_123',
            'https://example.org/path?\u0074oken=REVIEW_SECRET_123',
            'https://example.org/path?token=值_REVIEW_SECRET_123',
        ):
            for slash_escape in (False, True):
                with self.subTest(url=url, slash_escape=slash_escape):
                    raw = (json.dumps({'url': url, 'note': 'next "line"\n正文'}, ensure_ascii=True)+'\n').encode()
                    if slash_escape:
                        raw=raw.replace(b'/',b'\\/')
                    self.assertEqual(publication_risk(raw), 'sensitive URL query value')
                    projected, receipt=project_file('results.jsonl',raw)
                    self.assertNotIn(b'REVIEW_SECRET_123',projected)
                    self.assertIsNotNone(receipt)
                    self.assertIsNone(publication_risk(projected))
                    self.assertEqual(json.loads(projected)['note'],'next "line"\n正文')
                    self.assertIn('[REDACTED]',json.loads(projected)['url'])
                    self.assertEqual(len(projected.splitlines()),1)
        clean=(json.dumps({'url':'https://example.org/新闻?category=公开'},ensure_ascii=True)+'\n').encode()
        self.assertIsNone(publication_risk(clean))
        self.assertEqual(project_file('results.jsonl',clean),(clean,None))

    def test_json_escaped_parameter_name_cannot_bypass_url_scan(self):
        raw=br'{"url":"https:\/\/example.org/\u65b0?\u0074oken=REVIEW_SECRET_123"}'
        self.assertEqual(publication_risk(raw),'sensitive URL query value')
        projected, receipt=project_file('metadata.json',raw)
        self.assertEqual(json.loads(projected)['url'],'https://example.org/新?token=[REDACTED]')
        self.assertEqual(receipt['replacements'],1)
        self.assertIsNone(publication_risk(projected))

    def test_entity_escaped_inline_fields_are_scanned_and_projected(self):
        original=(b'<html><pre data-kind="metadata">{&quot;records&quot;: 26}</pre>'
                  b'<pre data-kind="metadata">{&quot;access_token&quot;: &quot;canary-private-123&quot;}</pre></html>')
        self.assertEqual(publication_risk(original),'sensitive field value')
        projected, receipt=project_file('attempts/p__case.html',original)
        self.assertNotIn(b'canary-private-123',projected)
        self.assertIsNone(publication_risk(projected))
        self.assertEqual(receipt['replacements'],1)
        soup=BeautifulSoup(projected,'html.parser')
        self.assertEqual(json.loads(soup.select('pre')[0].text),{'records':26})
        self.assertEqual(json.loads(soup.select('pre')[1].text),{'access_token':'[REDACTED]'})
        self.assertIn(b'canary-private-123',original)

    def test_scored_bundle_requires_an_output_hash(self):
        self.assertEqual(scored_output_hash({'output_sha256': 'hash'}), 'hash')
        self.assertEqual(scored_output_hash({'metrics': {'content_sha256': 'fallback'}}), 'fallback')
        with self.assertRaisesRegex(ValueError, 'missing scored output hash'):
            scored_output_hash({'artifact_path': 'artifacts/case.md'})

    def test_loader_localizes_async_status_and_accessibility_copy(self):
        source=(Path(__file__).resolve().parents[1]/'harness/web_fetch/assets/report-loader.js').read_text()
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
                         'sensitive field value')
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

    def test_bounded_sensitive_key_family_covers_common_real_credentials(self):
        samples = (
            b'access_token=custom-private-token',
            b'refresh_token: private-refresh-token',
            b'AWS_SECRET_ACCESS_KEY=private-secret',
            b'{"access_token":"private-token"}',
            b'X-Api-Key: private-key',
            b'X-Auth-Token: private-token',
            b'Private-Token: private-token',
            b'credentials=private-credentials',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(publication_risk(sample), 'sensitive field value')
                projected, row = project_file('call-logs/provider/case.json', sample)
                self.assertNotIn(b'private', projected)
                self.assertIn(b'[REDACTED]', projected)
                self.assertIn('credential', row['kinds'])

        for public in (
            b'access_token=<REDACTED>',
            b'{"refresh_token":"[REDACTED]"}',
            b'Traceback: RuntimeError at worker.py:42',
        ):
            with self.subTest(public=public):
                self.assertIsNone(publication_risk(public))
                self.assertEqual(project_file('call-logs/provider/case.json', public),
                                 (public, None))

        root_path, row = project_file(
            'call-logs/provider/case.json', b'failed at /root/private/session.log')
        self.assertNotIn(b'/root/', root_path)
        self.assertIn('local_path', row['kinds'])

    def test_embedded_url_queries_are_redacted_without_losing_diagnostics(self):
        samples = (
            b'curl failed: https://example.test/api?access_token=canary-private-123&view=full',
            b'fetch https://example.test/api?view=full&api_key=canary-private-123#detail failed',
            b'fetch https://example.test/api?view=full&amp;access%5Ftoken=canary-private-123',
            b'fetch https://example.test/api?X-Amz-Signature=canary-private-123',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(publication_risk(sample), 'sensitive URL query value')
                projected, row = project_file('call-logs/x.json', sample)
                self.assertNotIn(b'canary-private-123', projected)
                self.assertIn(b'[REDACTED]', projected)
                self.assertIn('credential', row['kinds'])
                self.assertIsNone(publication_risk(projected))

        harmless = b'Traceback: RuntimeError at worker.py:42; https://example.test/api?design=grid&view=full'
        self.assertEqual(project_file('call-logs/x.json', harmless), (harmless, None))
        placeholder = b'https://example.test/api?access_token=%5BREDACTED%5D'
        self.assertEqual(project_file('call-logs/x.json', placeholder), (placeholder, None))

    def test_packaged_downloads_and_disclosure(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
            (source / 'dataset.json').write_text('{}')
            (source / 'reliability.json').write_text('{"complete":true}')
            (source / 'report-manifest.json').write_text('{}')
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
                      'output_sha256':hashlib.sha256(b'actual output').hexdigest(),
                      'provider_metadata': {'route':'markdown',
                                            'raw_response_path':'private_artifacts/secret.bin',
                                            'retry':{'attempts':[{'call_log':binding}
                                                                 for binding in call_log_bindings]},
                                            'call_log':call_log_bindings[-1]}}
            file_record = {'attempt_id': 'browser__file-case__r001', 'case_id': 'file-case',
                           'artifact_path': 'artifacts/browser/file-case/run-001.txt',
                           'output_sha256':hashlib.sha256(b'').hexdigest(),
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
                '<pre data-kind="metadata">{"field":"parsed"}</pre>'
                '<pre data-kind="metadata">{&quot;access_token&quot;:&quot;canary-private-123&quot;}</pre>'
                '<pre data-kind="metadata">{"field":"response"}</pre></body></html>')
            (source / 'datasets/md-case.html').write_text(
                '<html><body><h2>Source metadata</h2><pre>{"level":"hard"}</pre>'
                '<h2>Official human ground truth</h2><pre>markdown ground truth</pre></body></html>')
            (source / 'datasets' / f'{unusual_case}.html').write_text(
                '<html><body><h2>Official human ground truth</h2>'
                '<pre>literal backslash reference</pre></body></html>')
            (source / 'changes').mkdir()
            (source / 'changes/index.html').write_text('<html><body>Old history</body></html>')
            (source / 'index.html').write_text('<!doctype html><html><head><title>Test</title></head><body><pre>DEBUG href="../../missing.css" data-file="missing-log.txt"</pre><a href="sources/case/source.html.txt">HTML</a></body></html>')
            (source / 'guide/article.html').write_text('<!doctype html><html><body>Article</body></html>')
            key = b'AIzaSy' + b'x' * 33
            raw = b'<script>key="' + key + b'"</script>'
            raw_path = source / 'sources/case/source.html.txt'
            raw_path.write_bytes(raw)
            malicious = 'https://example.test/" onmouseover="alert(1)'
            env = {**os.environ, 'WEBFETCH_REPORT_PUBLIC_URL': malicious}
            subprocess.run([sys.executable, str(repository / 'scripts/package_artifact_hub.py'),
                            str(source), str(root / 'publish/webfetch-sites.zip')], check=True,
                           capture_output=True, env=env)
            self.assertEqual(raw_path.read_bytes(), raw)
            with zipfile.ZipFile(root / 'publish/webfetch-sites.zip') as archive:
                manifest = json.loads(archive.read('manifest.json'))
                shell = BeautifulSoup(archive.read('index.html'), 'html.parser')
                self.assertEqual(
                    [node.get('src') or node.get('href')
                     for node in shell.select('script[src],link[rel="stylesheet"][href]')],
                    ['style.css', 'report.js', 'loader.js'],
                )
                self.assertEqual(manifest['architecture']['kind'],
                                 'single_html_shell_with_lazy_virtual_routes')
                self.assertEqual(manifest['architecture']['logical_routes'], 7)
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
                    self.assertNotIn(b'canary-private-123',unpack(name))
                privacy = json.loads(unpack('publication-privacy.json'))
                self.assertGreaterEqual(len(privacy['files']), 1)
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
                file_attempt = unpack('attempts/browser__file-case__r001.html')
                self.assertIn(b'{"field":"parsed"}', file_attempt)
                self.assertIn(b'{"field":"response"}', file_attempt)
            check_bundle(root / 'publish/webfetch-sites.zip',source)
            # A small correction keeps all original packed evidence. Only the
            # loader and complete manifest are sent to the existing site.
            from unittest.mock import patch
            import importlib.util
            with patch.object(sys, 'argv', ['package_artifact_hub.py']):
                module_spec = importlib.util.spec_from_file_location('incremental_package', Path(__file__).parents[1] / 'scripts/package_artifact_hub.py')
                module = importlib.util.module_from_spec(module_spec)
                module_spec.loader.exec_module(module)
            archive_path = root / 'publish/webfetch-sites.zip'
            baseline_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            old_home = (source / 'index.html').read_bytes()
            (source / 'index.html').write_text('<html><body>45 秒；实际调用示例</body></html>')
            (source / 'report-manifest.json').write_text(json.dumps({'presentation_refresh': {
                'scores_changed': False, 'provider_calls_added': 0, 'changed_routes': ['index.html', 'changes/index.html']}}))
            (source / 'changes/index.html').write_text('<html><body>New release history</body></html>')
            delta = root / 'delta'
            module.build_incremental(source, archive_path, delta)
            self.assertEqual({path.name for path in delta.iterdir()}, {'loader.js', 'manifest.json', 'update-receipt.json'})
            patched_manifest = json.loads((delta / 'manifest.json').read_text())
            self.assertEqual(patched_manifest['blocks'], manifest['blocks'])
            self.assertEqual(patched_manifest['files']['results.jsonl'], manifest['files']['results.jsonl'])
            self.assertIn('inline_base64', patched_manifest['files']['index.html'])
            self.assertIn(b'New release history', base64.b64decode(patched_manifest['files']['changes/index.html']['inline_base64']))
            self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(), baseline_digest)
            check_bundle(archive_path, source, delta)
            for index, broken in enumerate(('<a href="missing.html">missing</a>',
                                           '<pre data-file="missing.json"></pre>',
                                           '<img src="missing.png">',
                                           '<img src="results.jsonl">')):
                with self.subTest(incremental=broken):
                    (source / 'index.html').write_text('<html><body>' + broken + '</body></html>')
                    with self.assertRaises((ValueError, AssertionError)):
                        module.build_incremental(source, archive_path, root / f'broken-delta-{index}')
                    # Independently reject a hash-valid overlay that bypassed the packager.
                    poisoned = json.loads((delta / 'manifest.json').read_text())
                    data = ('<html><body>' + broken + '</body></html>').encode()
                    poisoned['files']['index.html'].update(inline_base64=base64.b64encode(data).decode(),
                        size=len(data), sha256=hashlib.sha256(data).hexdigest())
                    overlay = root / f'poisoned-overlay-{index}'
                    overlay.mkdir()
                    (overlay / 'manifest.json').write_text(json.dumps(poisoned))
                    with self.assertRaisesRegex(ValueError, 'Invalid internal'):
                        check_bundle(archive_path, source, overlay)
            (source / 'index.html').write_text('<html><body>45 秒；实际调用示例</body></html>')
            # Reject a different cohort or an attempt/log rewrite.
            saved_results = (source / 'results.jsonl').read_bytes()
            (source / 'results.jsonl').write_bytes(b'changed results')
            with self.assertRaisesRegex(ValueError, 'cannot replace measured results'):
                module.build_incremental(source, archive_path, root / 'wrong-cohort')
            (source / 'results.jsonl').write_bytes(saved_results)
            (source / 'report-manifest.json').write_text(json.dumps({'presentation_refresh': {
                'scores_changed': False, 'provider_calls_added': 0, 'changed_routes': ['attempts/browser__case__r001.html']}}))
            with self.assertRaisesRegex(ValueError, 'Not an explanatory route'):
                module.build_incremental(source, archive_path, root / 'wrong-route')
            (source / 'index.html').write_bytes(old_home)
            shutil.rmtree(module.DEST)

            for broken in ('<a href="missing.html">missing</a>',
                           '<pre data-file="missing.txt"></pre>',
                           '<img src="missing.png">'):
                with self.subTest(broken=broken):
                    (source / 'index.html').write_text('<html><body>' + broken + '</body></html>')
                    rejected = subprocess.run(
                        [sys.executable, str(root / 'scripts/package_artifact_hub.py')],
                        capture_output=True, env=env,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(b'ValueError', rejected.stderr)
            (source / 'results.jsonl').write_text(results.replace('"route": "markdown"', '"route": "changed"'))
            with self.assertRaisesRegex(ValueError, 'results differ from the current source'):
                check_bundle(root / 'publish/webfetch-sites.zip', source)


if __name__ == '__main__':
    unittest.main()
