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

import pytest

from bs4 import BeautifulSoup

from harness.web_fetch.publication_check import check as check_bundle
from harness.web_fetch.publication_check import scored_output_hash
from harness.web_fetch.publication_privacy import (
    project_file,
    project_redundant_result_streams,
    publication_risk,
)


@pytest.mark.parametrize('interrupted', [False, True])
def test_proposed_evidence_reads_are_scoped_and_hash_checked(tmp_path, interrupted):
    from harness.web_fetch.publication_privacy import read_evidence_binding, staged_evidence_reads
    source = tmp_path / 'source'
    other = tmp_path / 'other'
    source.mkdir()
    other.mkdir()
    original = source / 'receipt.json'
    original.write_bytes(b'original')
    (other / original.name).write_bytes(b'original')
    proposed = tmp_path / 'proposed.json'
    proposed.write_bytes(b'proposed')
    def binding(raw):
        return {'path': original.name, 'sha256': hashlib.sha256(raw).hexdigest()}
    try:
        with staged_evidence_reads(source, lambda path: proposed):
            assert read_evidence_binding(source, binding(b'proposed')) == proposed
            assert read_evidence_binding(other, binding(b'original')) == other / original.name
            # A matching old file must never hide a corrupt proposed replacement.
            with pytest.raises(ValueError, match='Staged revision evidence binding changed'):
                read_evidence_binding(source, binding(b'original'))
            with pytest.raises(ValueError):
                read_evidence_binding(source, {'path': '../proposed.json', 'sha256': binding(b'proposed')['sha256']})
            if interrupted:
                raise RuntimeError('interrupted validation')
    except RuntimeError:
        assert interrupted
    assert read_evidence_binding(source, binding(b'original')) == original
    with pytest.raises(ValueError):
        read_evidence_binding(source, binding(b'proposed'))


@pytest.mark.parametrize('allow_public_view', [False, True])
def test_evidence_reader_requires_original_bytes_when_requested(tmp_path, monkeypatch, allow_public_view):
    from harness.web_fetch import publication_privacy as privacy
    path = tmp_path / 'answer.txt'
    path.write_bytes(b'original')
    binding = {'path': path.name, 'sha256': hashlib.sha256(b'original').hexdigest()}
    calls = []
    def public_view(source, requested):
        calls.append(requested)
        return path, {'original_bytes': 8}
    monkeypatch.setattr(privacy, 'bound_public_view', public_view)
    assert privacy.read_evidence_binding(tmp_path, binding, allow_public_view=allow_public_view) == path
    assert not calls
    path.write_bytes(b'public projection')
    if allow_public_view:
        assert privacy.read_evidence_binding(tmp_path, binding) == path
        assert calls == [binding]
    else:
        with pytest.raises(ValueError, match='evidence binding changed'):
            privacy.read_evidence_binding(tmp_path, binding, allow_public_view=False)
        assert not calls


class PublicationPrivacyTests(unittest.TestCase):
    def test_result_streams_are_omitted_only_when_a_public_call_log_owns_them(self):
        with_log = {
            'attempt_id': 'browser__case__r001',
            'provider_metadata': {
                'stderr': 'large diagnostic stream',
                'browser_stderr': 'browser stream',
                'call_log': {'path': 'call-logs/browser/case/run-001.json', 'sha256': 'abc'},
            },
            'metrics': {'content_pass': False},
        }
        without_log = {
            'attempt_id': 'browser__other__r001',
            'provider_metadata': {'stderr': 'only retained copy'},
        }
        raw = (json.dumps(with_log) + '\n' + json.dumps(without_log) + '\n').encode()
        projected, receipt = project_redundant_result_streams('results.jsonl', raw)
        rows = [json.loads(line) for line in projected.splitlines()]
        self.assertNotIn('stderr', rows[0]['provider_metadata'])
        self.assertNotIn('browser_stderr', rows[0]['provider_metadata'])
        self.assertEqual(rows[0]['provider_metadata']['call_log'], with_log['provider_metadata']['call_log'])
        self.assertEqual(rows[0]['metrics'], with_log['metrics'])
        self.assertEqual(rows[1], without_log)
        self.assertEqual(receipt['omitted_fields'], {'stderr': 1, 'browser_stderr': 1})
        self.assertEqual(project_redundant_result_streams('dataset.json', raw), (raw, None))

    def test_truncated_quoted_sensitive_values_are_redacted_at_text_boundaries(self):
        import html
        fragments=["{'cookie':'PRIVATE_CANARY", '{"authorization":"Bearer PRIVATE_CANARY',
                   '"api_key":"PRIVATE_CANARY', "'refresh_token':'PRIVATE_CANARY"]
        for fragment in fragments:
            for encoding in (lambda value:value,html.escape):
                with self.subTest(fragment=fragments.index(fragment),encoded=encoding is html.escape):
                    raw=('<p>Recorded failure: '+encoding(fragment)+'</p><p>Independent explanation.</p><a href="../result.html">Evidence</a>').encode()
                    self.assertEqual(publication_risk(raw),'sensitive field value')
                    projected,receipt=project_file('attempts/example.html',raw)
                    self.assertNotIn(b'PRIVATE_CANARY',projected)
                    self.assertIn(b'Independent explanation.',projected)
                    self.assertIn(b'href="../result.html"',projected)
                    self.assertIsNone(publication_risk(projected))
                    self.assertEqual(project_file('attempts/example.html',projected),(projected,None))
                    self.assertTrue(receipt)
        # Ordinary partial JSON and already-redacted values remain intact.
        for fragment in ("{'status':'unavailable", "{'cookie':'[REDACTED]", "{'authorization':'<TOKEN>"):
            raw=('<p>'+html.escape(fragment)+'</p>').encode()
            self.assertIsNone(publication_risk(raw))
            self.assertEqual(project_file('attempts/example.html',raw),(raw,None))

    def test_public_cors_boolean_and_abnf_are_not_credentials(self):
        for policy in (b'Access-Control-Allow-Credentials: true',
                       b'Access-Control-Allow-Credentials = %s"true"',
                       b'Access-Control-Allow-Credentials = %i"false"',
                       b'Access&#45;Control&#45;Allow&#45;Credentials = %s"true"'):
            with self.subTest(policy=policy):
                self.assertIsNone(publication_risk(policy))
                self.assertEqual(project_file('response.txt',policy),(policy,None))
                mixed=policy+b'\nAuthorization: Bearer PRIVATE_CANARY\n'
                projected,receipt=project_file('response.txt',mixed)
                self.assertIn(policy,projected)
                self.assertNotIn(b'PRIVATE_CANARY',projected)
                self.assertIsNone(publication_risk(projected))
                self.assertIsNotNone(receipt)
        for secret in (b'Access-Control-Allow-Credentials: PRIVATE_CANARY',
                       b'Credentials: PRIVATE_CANARY',b'Authorization: Bearer PRIVATE_CANARY'):
            with self.subTest(secret=secret):
                self.assertEqual(publication_risk(secret),'sensitive field value')
                projected,_=project_file('response.txt',secret)
                self.assertNotIn(b'PRIVATE_CANARY',projected)
                self.assertIsNone(publication_risk(projected))

    def test_header_at_end_of_json_text_cannot_consume_following_record_fields(self):
        record={'body':'RFC example: Authorization: Basic PRIVATE_CANARY',
                'metrics':{'passed':False,'count':7},'case_id':'rfc-example'}
        raw=(json.dumps(record)+'\n').encode()
        projected, receipt=project_file('results.jsonl',raw)
        result=json.loads(projected)
        self.assertEqual(result['metrics'],record['metrics'])
        self.assertEqual(result['case_id'],record['case_id'])
        self.assertNotIn('PRIVATE_CANARY',result['body'])
        self.assertIsNone(publication_risk(projected))
        self.assertEqual(project_file('results.jsonl',projected),(projected,None))
        self.assertEqual(receipt['replacements'],1)

    def test_entity_encoded_query_equals_in_rendered_text_and_links_are_projected(self):
        for equals in ('&#61;', '&#x3d;', '&equals;'):
            with self.subTest(equals=equals):
                url='https://example.test/video?category'+equals+'news&amp;x-signature'+equals+'PRIVATE_CANARY'
                raw=('<p>'+url+'</p><a href="'+url+'">video</a>').encode()
                self.assertEqual(publication_risk(raw), 'sensitive URL query value')
                projected, receipt=project_file('attempts/case.html',raw)
                self.assertNotIn(b'PRIVATE_CANARY', projected)
                self.assertIsNone(publication_risk(projected))
                self.assertEqual(receipt['replacements'],2)
                soup=BeautifulSoup(projected,'html.parser')
                self.assertEqual(soup.a['href'], 'https://example.test/video?category=news&x-signature=[REDACTED]')
                self.assertEqual(project_file('attempts/case.html',projected),(projected,None))
                clean=('<p>https://example.test/video?category'+equals+'news&amp;page'+equals+'2</p>').encode()
                self.assertEqual(project_file('attempts/case.html',clean),(clean,None))

    def test_nested_entity_escaped_query_separator_is_projected_once(self):
        raw = json.dumps({
            'url': 'https://example.test/video?category=news&amp;amp;token=PRIVATE_CANARY',
        }).encode()
        self.assertEqual(publication_risk(raw), 'sensitive URL query value')
        projected, receipt = project_file('results.jsonl', raw)
        self.assertNotIn(b'PRIVATE_CANARY', projected)
        self.assertIn('[REDACTED]', json.loads(projected)['url'])
        self.assertIsNone(publication_risk(projected))
        self.assertEqual(project_file('results.jsonl', projected), (projected, None))
        self.assertEqual(receipt['replacements'], 1)

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
                '<pre>DEBUG href="/not-a-report-link.css" '
                '&lt;link href="/another-diagnostic.css"&gt;</pre>'
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
            packaged = subprocess.run([sys.executable, str(repository / 'scripts/package_artifact_hub.py'),
                            str(source), str(root / 'publish/webfetch-sites.zip')],
                           capture_output=True, env=env)
            self.assertEqual(packaged.returncode, 0, packaged.stderr.decode())
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
            from harness.web_fetch import report_package as module
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
            from unittest.mock import patch
            # An overlay may update explanations, never attempts or packed evidence.
            for label in ('attempt', 'blocks'):
                poisoned = json.loads((delta / 'manifest.json').read_text())
                if label == 'attempt':
                    key = record['artifact_path']
                    payload = b'changed measured answer'
                    poisoned['files'][key] = {'inline_base64': base64.b64encode(payload).decode(),
                        'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'mime': 'text/plain'}
                else:
                    poisoned['blocks'][next(iter(poisoned['blocks']))]['offset'] += 1
                overlay = root / ('illegal-' + label)
                overlay.mkdir()
                (overlay / 'manifest.json').write_text(json.dumps(poisoned))
                with self.assertRaisesRegex(ValueError, 'Incremental overlay changed'):
                    check_bundle(archive_path, source, overlay)
            original_build = module._build_incremental
            def interrupted(*args):
                original_build(*args)
                raise OSError('simulated storage interruption')
            with patch.object(module, '_build_incremental', side_effect=interrupted):
                with self.assertRaisesRegex(OSError, 'storage interruption'):
                    module.build_incremental(source, archive_path, root / 'interrupted')
            self.assertFalse((root / 'interrupted').exists())
            self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(), baseline_digest)
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
            with self.assertRaises(ValueError):
                module.build_incremental(source, archive_path, root / 'wrong-cohort')
            (source / 'results.jsonl').write_bytes(saved_results)
            (source / 'report-manifest.json').write_text(json.dumps({'presentation_refresh': {
                'scores_changed': False, 'provider_calls_added': 0, 'changed_routes': ['attempts/browser__case__r001.html']}}))
            with self.assertRaisesRegex(ValueError, 'Not an explanatory route'):
                module.build_incremental(source, archive_path, root / 'wrong-route')
            (source / 'index.html').write_bytes(old_home)

            for broken in ('<a href="missing.html">missing</a>',
                           '<pre data-file="missing.txt"></pre>',
                           '<img src="missing.png">'):
                with self.subTest(broken=broken):
                    (source / 'index.html').write_text('<html><body>' + broken + '</body></html>')
                    rejected = subprocess.run(
                        [sys.executable, str(repository / 'scripts/package_artifact_hub.py'), str(source), str(root / 'publish/webfetch-sites.zip')],
                        capture_output=True, env=env,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn(b'ValueError', rejected.stderr)
            (source / 'results.jsonl').write_text(results.replace('"route": "markdown"', '"route": "changed"'))
            with self.assertRaisesRegex(ValueError, 'source hash differs'):
                check_bundle(root / 'publish/webfetch-sites.zip', source)


if __name__ == '__main__':
    unittest.main()


def test_host_metadata_projection_rebinds_dependencies_without_changing_measurements():
    import hashlib
    import json
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    config = json.dumps({'binary': '/Users/fixture/tool/bin', 'version': '1.2.3', 'environment': {'hostname': 'private-machine'}}).encode()
    config_hash = hashlib.sha256(config).hexdigest()
    result = json.dumps({'ok': True, 'duration': 1.25, 'content_sha256': 'a'*64,
                         'provider_config_sha256': config_hash,
                         'provider_metadata': {'stderr': 'at /Users/fixture/work/main.py:4'}}).encode()+b'\n'
    files = {'config.json': config, 'results.jsonl': result, 'answer.txt': b'actual answer'}
    original = files.copy()
    public, receipt = project_host_metadata_files(files, ['/Users/fixture'])
    assert files == original
    assert public['answer.txt'] == files['answer.txt']
    row = json.loads(public['results.jsonl'])
    assert row['provider_config_sha256'] == hashlib.sha256(public['config.json']).hexdigest()
    assert (row['ok'], row['duration'], row['content_sha256']) == (True, 1.25, 'a'*64)
    assert json.loads(public['config.json'])['version'] == '1.2.3'
    assert 'hostname' not in json.loads(public['config.json'])['environment']
    assert b'/Users/fixture' not in b''.join(public.values())
    assert receipt['files'][0]['original_sha256'] == config_hash
    assert receipt['files'][0]['published_sha256'] != config_hash
    assert '/Users/fixture' not in json.dumps(receipt)
    repeated, unchanged = project_host_metadata_files(public, ['/Users/fixture'])
    assert repeated == public and unchanged['files'] == []


def test_public_diagnostic_contexts_redact_queries_and_preserve_scores():
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    row = {'ok': True, 'elapsed_ms': 12.5, 'metrics': {'content_pass': True,
        'content_sha256': 'a' * 64, 'composite_group_results': [{'records': [
            {'context': '[Forum](https://example.test/thread?access_token=private-token-canary)'}]}]}}
    raw = (json.dumps(row) + '\n').encode()
    files = {'results.jsonl': raw, 'receipt.json': json.dumps({'results_sha256': hashlib.sha256(raw).hexdigest()}).encode()}
    public, receipt = project_host_metadata_files(files, ['/Users/fixture'], public_excerpt_views=True)
    projected = json.loads(public['results.jsonl'])
    assert b'private-token-canary' not in public['results.jsonl']
    assert projected['ok'] == row['ok'] and projected['elapsed_ms'] == row['elapsed_ms']
    assert projected['metrics']['content_pass'] is True
    assert projected['metrics']['content_sha256'] == 'a' * 64
    assert json.loads(public['receipt.json'])['results_sha256'] == hashlib.sha256(public['results.jsonl']).hexdigest()
    assert files['results.jsonl'] == raw
    assert any('.metrics.' in field and field.endswith('.context') for item in receipt['files'] for field in item['redacted_fields'])
    unchanged, _ = project_host_metadata_files(files, ['/Users/fixture'])
    assert unchanged == files


def test_host_metadata_projection_requires_an_explicit_non_root_prefix():
    import pytest
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    for prefixes in ([], ['/'], ['relative/path']):
        with pytest.raises(ValueError):
            project_host_metadata_files({}, prefixes)


def test_public_metadata_receipt_checks_bytes_and_excludes_scored_payloads(tmp_path):
    import hashlib
    import json
    import pytest
    from harness.web_fetch.publication_privacy import project_host_metadata_files, require_host_metadata_projection
    original={'config.json': b'{"binary":"/Users/fixture/tool"}'}
    public,receipt=project_host_metadata_files(original,['/Users/fixture'])
    (tmp_path/'config.json').write_bytes(public['config.json'])
    def bind():
        path=tmp_path/'projection.json'
        path.write_text(json.dumps(receipt))
        return {'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    require_host_metadata_projection(tmp_path,bind())
    (tmp_path/'config.json').write_text('{"binary":"changed"}')
    with pytest.raises(ValueError):require_host_metadata_projection(tmp_path,bind())
    (tmp_path/'config.json').write_bytes(public['config.json'])
    receipt['files'][0]['path']='artifacts/answer.json'
    with pytest.raises(ValueError,match='preserve scored payloads'):
        require_host_metadata_projection(tmp_path,bind())


def test_host_projection_does_not_match_another_user_with_the_same_prefix():
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    files={'config.json': b'{"binary":"/Users/fixture-other/tool"}'}
    public,receipt=project_host_metadata_files(files,['/Users/fixture'])
    assert public==files and receipt['files']==[]


def test_named_host_in_auxiliary_log_is_projected_with_bound_public_hash(tmp_path):
    import hashlib
    import json
    from harness.web_fetch.publication_privacy import project_host_metadata_files, require_host_metadata_projection
    name='logs/case.debug.txt'
    raw=b'{"hostname":"fixture.local","message":"response completed"}'
    inputs={name:raw,'answer.txt':b'unchanged scored text'}
    public,receipt=project_host_metadata_files(inputs,['/Users/fixture'],host_names=['fixture.local'])
    assert public['answer.txt']==inputs['answer.txt']
    assert b'fixture.local' not in public[name]
    assert b'response completed' in public[name]
    (tmp_path/'logs').mkdir();(tmp_path/name).write_bytes(public[name])
    path=tmp_path/'projection.json';path.write_text(json.dumps(receipt))
    require_host_metadata_projection(tmp_path,{'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})


def test_host_projection_refuses_to_rewrite_score_bearing_or_response_payloads():
    import pytest
    from harness.web_fetch.publication_privacy import project_host_metadata_files
    for name in ('artifacts/answer.json','logs/case.response.txt','answer.txt'):
        raw=b'{"host":"fixture.local"}'
        with pytest.raises(ValueError,match='must not alter scored'):
            project_host_metadata_files({name:raw},['/Users/fixture'],host_names=['fixture.local'])
def test_long_unterminated_escaped_string_preserves_content_without_rescanning_suffixes():
    from harness.web_fetch.publication_privacy import redact_url_queries
    text = '"' + ('\\"fragment' * 20000)
    assert redact_url_queries(text) == (text, 0)


def test_long_escaped_url_still_redacts_sensitive_query_value():
    from harness.web_fetch.publication_privacy import redact_url_queries
    text = 'ordinary log ' * 500 + r'"https:\/\/example.com\/image?apiKey=private-value&width=300"'
    public, count = redact_url_queries(text)
    assert count == 1
    assert 'private-value' not in public
    assert 'width=300' in public


def test_large_and_small_string_tokenization_have_identical_public_redaction_semantics():
    import random
    from harness.web_fetch.publication_privacy import redact_url_queries
    rng = random.Random(51)
    prefix = ' ' * 4097
    fragments = [
        '"abc\\"def" tail "https://example.com/?token=private-value"',
        '"unterminated\\"suffix', '"line\nfeed"', '"line\\\nfeed"',
        '"line\\\rfeed"', '"\\u0068ttps://example.com/?token=private-value"',
        '"nul\x00boundary"', '"backslash\\\\"',
    ]
    fragments += [''.join(rng.choice('ab"\\\r\n\t\x00') for _ in range(60)) for _ in range(64)]
    for text in fragments:
        expected, count = redact_url_queries(text)
        assert redact_url_queries(prefix+text) == (prefix+expected, count)


@pytest.mark.parametrize("delimiter", ["&quot;", "&apos;", "&gt;", "&#34;", "&Tab;"])
def test_encoded_url_boundary_does_not_expand_unrelated_large_html(delimiter):
    import tracemalloc
    from harness.web_fetch.publication_privacy import _redact_plain_url_queries
    suffix = "x" * 100000
    source = "https://example.test/?token=private-value" + delimiter + suffix
    tracemalloc.start()
    try:
        projected, count = _redact_plain_url_queries(source)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert projected == "https://example.test/?token=[REDACTED]" + delimiter + suffix
    assert count == 1
    # This is evidence after the URL, not a 100 KB URL character map.
    assert peak < 2_000_000


@pytest.mark.parametrize("boundary", [5000, 65535, 65536, 131071])
def test_sparse_json_scanner_matches_string_grammar_across_windows(boundary):
    import random
    from harness.web_fetch.publication_privacy import _JSON_STRING, _json_string_spans
    random_source = random.Random(90210)
    for _ in range(20):
        tail = ''.join(random_source.choice('abc"\\\n\r\t') for _ in range(150))
        text = 'x' * boundary + tail
        assert list(_json_string_spans(text)) == [m.span() for m in _JSON_STRING.finditer(text)]


@pytest.mark.parametrize('depth',range(1,7))
def test_nested_url_delimiters_keep_exact_projection_with_bounded_memory(depth):
    import html
    import tracemalloc
    from harness.web_fetch.publication_privacy import _url_entity_surface,project_file
    delimiter = '&quot;'
    for _ in range(depth):
        delimiter = html.escape(delimiter,quote=False)
    raw = 'https://api.invalid/?token=example'+delimiter+'z'*100000
    tracemalloc.start()
    try:
        _url_entity_surface(raw)
        _,peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100000
    data = ('prefix '+raw+' suffix').encode()
    assert project_file('view.html',data)[0] == data.replace(b'token=example',b'token=[REDACTED]')


def test_incomplete_nested_entity_does_not_hide_late_url_credentials():
    from harness.web_fetch.publication_privacy import project_file
    raw = b'https://api.invalid/?first=1&amp;look=&quotx'+b'z'*10000+b'&token=example'
    assert project_file('view.html',raw)[0] == raw.replace(b'token=example',b'token=[REDACTED]')


@pytest.mark.parametrize('data',[
    b'{"text":"safe"}\n{"text":"also safe"}\n',
    b'{"authorization":"Bearer example-secret"}\n{"text":"safe"}\n',
    b'{"text":"safe"}\n{"url":"https://api.invalid/?token=secret"}\n',
    b'{"authorization":\n"Bearer example-secret"}\n',
    b'{"text":"unterminated\n{"authorization":"Bearer example-secret"}\n',
    b'{"authorization":"[REDACTED]"}\n{"text":"\\u4e2d\\u6587"}\n',
])
def test_jsonl_risk_keeps_record_boundaries_and_falls_back_for_malformed_data(data):
    from harness.web_fetch.publication_privacy import publication_file_risk,publication_risk
    assert bool(publication_file_risk('records.jsonl',data)) == bool(publication_risk(data))


def test_short_plain_text_cache_preserves_sensitive_context_and_excludes_large_values():
    from harness.web_fetch.publication_privacy import (
        _cached_plain_publication_risk, _plain_publication_risk, publication_risk,
    )
    _cached_plain_publication_risk.cache_clear()
    assert _plain_publication_risk(b'public text') is None
    assert _plain_publication_risk(b'public text') is None
    assert _cached_plain_publication_risk.cache_info().hits == 1
    size = _cached_plain_publication_risk.cache_info().currsize
    assert _plain_publication_risk(b'x'*2048) is None
    assert _cached_plain_publication_risk.cache_info().currsize == size
    assert publication_risk(b'{"text":"same-value"}') is None
    assert publication_risk(b'{"password":"same-value"}') == 'sensitive field value'


def test_large_plain_risk_cache_reuses_hashes_without_retaining_payload(monkeypatch):
    import json
    from harness.web_fetch import publication_privacy as privacy
    privacy._large_plain_risks.clear()
    original = privacy._scan_plain_publication_risk
    calls = []
    def scan(data):
        calls.append(len(data))
        return original(data)
    monkeypatch.setattr(privacy,'_scan_plain_publication_risk',scan)
    value = b'unique immutable public evidence '*200
    assert privacy._plain_publication_risk(value) is None
    assert privacy._plain_publication_risk(value) is None
    assert calls == [len(value)]
    assert privacy._plain_publication_risk(value+b'changed') is None
    assert calls == [len(value),len(value)+7]
    assert all(isinstance(key[1],bytes) and len(key[1])==32 for key in privacy._large_plain_risks)
    assert privacy.publication_risk(json.dumps({'password':value.decode()}).encode()) == 'sensitive field value'


@pytest.mark.parametrize('name',['response.txt','response.json','response.log'])
def test_json_wrapped_text_is_projected_by_content_not_filename(name):
    import json
    from harness.web_fetch.publication_privacy import project_file,publication_risk
    original = json.dumps({'success':True,'result':'API example: {"clientSecret": "string"}'}).encode()
    public,receipt = project_file(name,original)
    assert receipt and receipt['original_sha256'] != receipt['published_sha256']
    assert '[REDACTED]' in json.loads(public)['result']
    assert publication_risk(public) is None
    safe = b'{ "success": true, "result": "public text" }\n'
    assert project_file(name,safe) == (safe,None)


def test_nested_sensitive_objects_and_documented_auth_placeholders_agree_at_export():
    import json
    from harness.web_fetch.publication_privacy import project_file,publication_risk
    raw = b'{"credentials":{"user":"example-user","password":"example-pass"}}'
    public,receipt = project_file('native.txt',raw)
    assert receipt and json.loads(public)['credentials'] == {'user':'[REDACTED]','password':'[REDACTED]'}
    assert publication_risk(public) is None
    placeholder = b'{"Authorization":"Bearer <YOUR_API_KEY>"}'
    assert project_file('native.txt',placeholder) == (placeholder,None)
    assert publication_risk(placeholder) is None


def test_review_layer_excerpts_project_public_examples_and_rebind_wrappers():
    from harness.web_fetch.publication_privacy import project_host_metadata_files, publication_risk
    example = '{"clientSecret":"private-example-canary","count":5}'
    nested = {'frozen_reference_files': [{'probes': [{'text': example}]}]}
    original = {'attempts': [{'agent_evidence': example, 'effective_agent_evidence': example,
        'anomaly_review': {'layers': {'reference': {**nested,
            'evidence': json.dumps(nested)}}}}]}
    raw = json.dumps(original).encode()
    files = {'local-evidence/review/supplement.json': raw,
             'result-review.json': json.dumps({'supplemental_reviews': [{'path':
                 'local-evidence/review/supplement.json', 'sha256': hashlib.sha256(raw).hexdigest()}]}).encode()}
    unchanged, _ = project_host_metadata_files(files, ['/Users/fixture'])
    assert unchanged == files and publication_risk(raw)
    public, receipt = project_host_metadata_files(files, ['/Users/fixture'], public_excerpt_views=True)
    projected = public['local-evidence/review/supplement.json']
    assert not publication_risk(projected) and b'private-example-canary' not in projected
    row = json.loads(projected)['attempts'][0]
    assert json.loads(row['agent_evidence'])['count'] == 5
    assert json.loads(row['anomaly_review']['layers']['reference']['evidence'])['frozen_reference_files'][0]['probes'][0]['text']
    binding = json.loads(public['result-review.json'])['supplemental_reviews'][0]
    assert binding['sha256'] == hashlib.sha256(projected).hexdigest()
    assert files['local-evidence/review/supplement.json'] == raw
    record = next(r for r in receipt['files'] if r['path'].endswith('supplement.json'))
    assert record['original_sha256'] == hashlib.sha256(raw).hexdigest()
    assert record['published_sha256'] == binding['sha256']


def test_review_projection_does_not_hide_risks_in_unrelated_metadata():
    from harness.web_fetch.publication_privacy import project_host_metadata_files, publication_risk
    raw = json.dumps({'unrelated': '{"clientSecret":"private-example-canary"}'}).encode()
    files = {'local-evidence/review/supplement.json': raw}
    public, _ = project_host_metadata_files(files, ['/Users/fixture'], public_excerpt_views=True)
    assert public == files and publication_risk(public['local-evidence/review/supplement.json'])


def test_signed_thumbnail_redaction_preserves_outer_record_and_following_text():
    """A private image query must not consume the record's closing Markdown."""
    from lexbench_webfetch.scoring import score_attempt
    from lexbench_webfetch.metrics import task_passed
    case={'url':'https://example.test/','acceptance_contract':{'version':1,
        'schema':'named_link','minimum_records':2,'hosts':['example.test'],
        'entry_path':r'/boards/[0-9]+','stable_text_anchors':[{'id':'feature','alternatives':['Collect and organize']} ]}}
    original=(
        '[![Cover](https://cdn.example.test/cover.jpg?auth_key=fictional-one)Collection One](https://example.test/boards/1)\n'
        '[![Cover](https://cdn.example.test/cover2.jpg?auth_key=fictional-two)Collection Two](https://example.test/boards/2)\n'
        'Collect and organize\n').encode()
    projected,receipt=project_file('artifacts/sample/boards.txt',original)
    assert b'fictional-one' not in projected and b'fictional-two' not in projected
    assert receipt['replacements']==2
    assert task_passed(True,score_attempt(case,original,ok=True))
    scored=score_attempt(case,projected,ok=True)
    assert task_passed(True,scored)
    assert scored['stable_unique_record_count']==2
    broken=projected.replace(b')Collection One](https://example.test/boards/1)',b'')
    assert not task_passed(True,score_attempt(case,broken,ok=True))
