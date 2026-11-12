import io
import json
import tempfile
import urllib.request
import unittest
import zipfile
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harness.web_fetch.publication_verify import (
    download_export,
    main,
    verify_metadata,
    verify_public_files,
)


class PublicationVerifyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('urllib.request.build_opener', lambda *handlers: SimpleNamespace(open=urllib.request.urlopen)))

    def test_exported_tree_verification_is_shared_and_byte_exact(self):
        from harness.web_fetch.publication import verify_published_tree
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'index.html').write_bytes(b'<head></head>Report')
            (root/'.generated-report').write_text('generated', encoding='utf-8')
            (root/'evidence').mkdir();(root/'evidence/output.txt').write_bytes(b'full saved answer')
            files={'index.html':(root/'index.html').read_bytes(),
                   'evidence/output.txt':(root/'evidence/output.txt').read_bytes()}
            def exported(items):
                stream=io.BytesIO()
                with zipfile.ZipFile(stream,'w') as archive:
                    for name,raw in items:archive.writestr(name,raw)
                return stream.getvalue()
            self.assertEqual(verify_published_tree(root,exported(files.items())),2)
            invalid=[list(files.items())[:1],list(files.items())+[('extra.txt',b'extra')],
                     [('index.html',files['index.html']),('evidence/output.txt',b'full saved WRONG')],
                     list(files.items())+[('index.html',files['index.html'])]]
            for items in invalid:
                with self.subTest(names=[name for name,_ in items]),self.assertRaises(ValueError):
                    verify_published_tree(root,exported(items))

    def test_metadata_verification_does_not_download_report_files(self):
        payload={
            'slug':'test',
            'version':{'id':'ver_first'},
            'files':['index.html','loader.js'],
            'kind':'folder',
        }
        with patch('harness.web_fetch.publication_verify.load_token',return_value='fixture'), \
             patch('harness.web_fetch.publication_verify.urllib.request.urlopen',
                   return_value=io.BytesIO(json.dumps(payload).encode())) as opened:
            result=verify_metadata('test','https://reports.example')
        self.assertEqual(result['version'],'ver_first')
        self.assertEqual(result['files'],2)
        self.assertIn('no report files downloaded',result['method'])
        self.assertEqual(opened.call_count,1)
        self.assertEqual(opened.call_args.args[0].full_url,
                         'https://reports.example/api/sites/test')

    def verify(self, served=b'const report=true;', changed_version=False,
               injected_entry=True, injection_location='prefix',
               injected_base=b'/api/preview/test/'):
        metadata_calls=0
        def response(request, timeout):
            nonlocal metadata_calls
            if isinstance(request, str):
                if request.endswith('/index.html'):
                    entry=b'<html>\xe6\x8a\xa5\xe5\x91\x8a</html>'
                    if injected_entry:
                        injection=(
                            b'<base href="'+injected_base+b'">'
                            b'<script data-artifact-bootstrap>safe()</script>'
                            b'<script data-artifact-bootstrap>alsoSafe()</script>'
                        )
                        if injection_location=='head':
                            entry=b'<html><head></head>\xe6\x8a\xa5\xe5\x91\x8a</html>'
                            return io.BytesIO(entry.replace(b'<head>',b'<head>'+injection,1))
                        return io.BytesIO(injection+entry)
                    return io.BytesIO(entry)
                return io.BytesIO(served)
            metadata_calls+=1
            entry_content=('<html><head></head>报告</html>'
                           if injection_location=='head' else '<html>报告</html>')
            return io.BytesIO(json.dumps({'files':['index.html','loader.js'],
                'file':'index.html','content':entry_content,
                'version':{'id':'ver_second' if changed_version and metadata_calls>1 else 'ver_first'}}).encode())
        with tempfile.TemporaryDirectory() as temp:
            archive=Path(temp)/'site.zip'
            with zipfile.ZipFile(archive,'w') as bundle:
                entry=('<html><head></head>报告</html>' if injection_location=='head'
                       else '<html>报告</html>')
                bundle.writestr('index.html',entry)
                bundle.writestr('loader.js',b'const report=true;')
            with patch('harness.web_fetch.publication_verify.load_token',return_value='fixture'), \
                 patch('harness.web_fetch.publication_verify.urllib.request.urlopen',side_effect=response):
                return verify_public_files('test',archive,'https://reports.example')

    def test_complete_matching_file_set(self):
        result=self.verify()
        self.assertEqual(result['files'],2)
        self.assertEqual(result['anonymous_byte_identical_files'],1)
        self.assertEqual(result['anonymous_injected_html_files'],1)
        self.assertFalse(result['anonymous_all_files_byte_identical'])
        self.assertTrue(result['authenticated_original_entry_verified'])
        self.assertTrue(result['submitted_bytes_verified_by_layer'])

    def test_byte_identical_entry_is_reported_as_fully_anonymous(self):
        result=self.verify(injected_entry=False)
        self.assertEqual(result['anonymous_byte_identical_files'],2)
        self.assertEqual(result['anonymous_injected_html_files'],0)
        self.assertTrue(result['anonymous_all_files_byte_identical'])

    def test_current_in_head_bootstrap_is_verified(self):
        result=self.verify(injection_location='head')
        self.assertEqual(result['anonymous_injected_html_files'],1)
        self.assertTrue(result['submitted_bytes_verified_by_layer'])

    def test_version_pinned_in_head_bootstrap_is_verified(self):
        result=self.verify(
            injection_location='head',
            injected_base=b'/api/preview/test~1789109474577.69P4Rn4-Z1LcSmiDMqtsFrSA1qwGXKYTNbjCCxRutHw/',
        )
        self.assertEqual(result['anonymous_injected_html_files'],1)
        self.assertTrue(result['submitted_bytes_verified_by_layer'])

    def test_version_pinned_base_for_another_site_fails(self):
        with self.assertRaisesRegex(ValueError,'Remote file differs: index.html'):
            self.verify(
                injection_location='head',
                injected_base=b'/api/preview/other~1789109474577.token/',
            )

    def test_version_pinned_base_with_path_escape_fails(self):
        with self.assertRaisesRegex(ValueError,'Remote file differs: index.html'):
            self.verify(
                injection_location='head',
                injected_base=b'/api/preview/test~version/../other/',
            )

    def test_unrecognized_entry_injection_fails(self):
        metadata_calls=0
        def response(request, timeout):
            nonlocal metadata_calls
            if isinstance(request,str):
                if request.endswith('/index.html'):
                    return io.BytesIO(b'<aside>unknown</aside><html>report</html>')
                return io.BytesIO(b'const report=true;')
            metadata_calls+=1
            return io.BytesIO(json.dumps({'files':['index.html','loader.js'],
                'file':'index.html','content':'<html>report</html>',
                'version':{'id':'ver_first'}}).encode())
        with tempfile.TemporaryDirectory() as temp:
            archive=Path(temp)/'site.zip'
            with zipfile.ZipFile(archive,'w') as bundle:
                bundle.writestr('index.html','<html>report</html>')
                bundle.writestr('loader.js',b'const report=true;')
            with patch('harness.web_fetch.publication_verify.load_token',return_value='fixture'), \
                 patch('harness.web_fetch.publication_verify.urllib.request.urlopen',side_effect=response):
                with self.assertRaisesRegex(ValueError,'Remote file differs: index.html'):
                    verify_public_files('test',archive,'https://reports.example')

    def test_modified_bytes_fail(self):
        with self.assertRaisesRegex(ValueError,'Remote file differs'):
            self.verify(served=b'changed')

    def test_concurrent_version_change_fails(self):
        with self.assertRaisesRegex(ValueError,'version changed'):
            self.verify(changed_version=True)

    def test_public_file_retries_incomplete_read(self):
        calls=0
        def response(request, timeout):
            nonlocal calls
            if isinstance(request,str):
                if request.endswith('/index.html'):
                    return io.BytesIO(b'<html>report</html>')
                calls+=1
                if calls==1:raise IncompleteRead(b'partial')
                return io.BytesIO(b'const report=true;')
            return io.BytesIO(json.dumps({'files':['index.html','loader.js'],
                'file':'index.html','content':'<html>report</html>',
                'version':{'id':'ver_first'}}).encode())
        with tempfile.TemporaryDirectory() as temp:
            archive=Path(temp)/'site.zip'
            with zipfile.ZipFile(archive,'w') as bundle:
                bundle.writestr('index.html','<html>report</html>')
                bundle.writestr('loader.js',b'const report=true;')
            with patch('harness.web_fetch.publication_verify.load_token',return_value='fixture'), \
                 patch('harness.web_fetch.publication_verify.time.sleep'), \
                 patch('harness.web_fetch.publication_verify.urllib.request.urlopen',side_effect=response):
                self.assertEqual(verify_public_files('test',archive,'https://reports.example')['files'],2)
        self.assertEqual(calls,2)

    def test_export_retries_server_error(self):
        class Export(io.BytesIO):
            headers={'x-artifact-version':'ver_first'}
        with tempfile.TemporaryDirectory() as temp, \
             patch('harness.web_fetch.publication_verify.time.sleep'), \
             patch('harness.web_fetch.publication_verify.urllib.request.urlopen',side_effect=[
                 __import__('urllib.error').error.HTTPError('url',502,'bad gateway',{},None),
                 Export(b'archive')]):
            version,path=download_export('request',Path(temp))
            self.assertEqual(version,'ver_first')
            self.assertEqual(path.read_bytes(),b'archive')


def _run_verify(profile, *extra, env=None):
    argv = ['verify', '--slug', 'site-one', '--profile', str(profile), *extra]
    with patch('sys.argv', argv), patch.dict('os.environ', env or {}), \
            patch('harness.web_fetch.publication_verify.load_token',
                  side_effect=AssertionError('must not load a credential')):
        main()


def test_verification_never_sends_a_token_outside_the_bound_host(tmp_path, monkeypatch):
    monkeypatch.delenv('ARTIFACT_SITE_BASE_URL', raising=False)
    flat = tmp_path / 'flat.json'
    flat.write_text(json.dumps({'artifact_hub_base_url': 'https://reports.example'}), encoding='utf-8')
    nested = tmp_path / 'nested.json'
    nested.write_text(json.dumps({'publication': {'base_url': 'https://reports.example'}}), encoding='utf-8')
    for profile in (flat, nested):
        with pytest.raises(ValueError, match='differs from the bound profile'):
            _run_verify(profile, '--export', '--base-url', 'https://attacker.example')
        with pytest.raises(ValueError, match='differs from the bound profile'):
            _run_verify(profile, '--export',
                        env={'ARTIFACT_SITE_BASE_URL': 'https://attacker.example'})
    unbound = tmp_path / 'unbound.json'
    unbound.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='explicit HTTPS base URL'):
        _run_verify(unbound, '--export')


def test_redirected_requests_keep_the_token_only_on_the_publication_host(tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from harness.reporting import publication
    from harness.web_fetch import publication_verify

    seen = []

    def serve(redirect):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append((self.server, self.path, self.headers.get('Authorization')))
                location = redirect(self.path)
                if location:
                    self.send_response(302)
                    self.send_header('Location', location)
                    self.end_headers()
                    return
                body = json.dumps({'slug': 'site-one', 'version': {'id': 'ver_one'},
                                   'files': ['index.html']}).encode()
                self.send_response(200)
                self.send_header('X-Artifact-Version', 'ver_one')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    other = serve(lambda path: None)
    # The publication host first redirects to itself, then to another host.
    origin = serve(lambda path: '/moved' + path if path.startswith('/api/')
                   else f'http://localhost:{other.server_port}{path}')
    base = f'http://127.0.0.1:{origin.server_port}'
    monkeypatch.setattr(publication, 'load_token', lambda *, base_url: 'ahp_fixture')
    monkeypatch.setattr(publication_verify, 'load_token', lambda *, base_url: 'ahp_fixture')
    monkeypatch.setattr(publication_verify, 'validate_service_base', lambda value: value)
    try:
        assert publication.PublicationClient(None).request(base, '/api/sites/site-one').status == 200
        assert publication_verify.verify_metadata('site-one', base)['version'] == 'ver_one'
        export = urllib.request.Request(base + '/api/sites/site-one',
                                        headers={'Authorization': 'Bearer ahp_fixture'})
        assert publication_verify.download_export(export, tmp_path)[0] == 'ver_one'
    finally:
        origin.shutdown()
        other.shutdown()
    token = 'Bearer ahp_fixture'
    hops = [('/api/sites/site-one', token), ('/moved/api/sites/site-one', token)]
    assert [(path, auth) for server, path, auth in seen if server is origin] == hops * 3
    assert [(path, auth) for server, path, auth in seen if server is other] == [
        ('/moved/api/sites/site-one', None)] * 3


@pytest.mark.parametrize('damage', [None, 'version', 'path', 'duplicate', 'size'])
def test_manifest_export_downloads_versioned_originals_without_sharing(tmp_path, damage):
    from urllib.parse import parse_qs, urlsplit
    files = {'index.html': b'<h1>Report</h1>', 'data/a.bin': b'\x00\xffraw'}
    entries = [{'path': name, 'size': len(raw)} for name, raw in files.items()]
    manifest = {'assembled': False, 'versionId': 'ver_saved', 'files': entries}
    if damage == 'version':
        manifest['versionId'] = 'ver_other'
    elif damage == 'path':
        entries[0]['path'] = '../outside'
    elif damage == 'duplicate':
        entries.append(entries[0])
    elif damage == 'size':
        entries[0]['size'] += 1
    calls = []
    def response(request, timeout):
        calls.append(request)
        assert request.get_method() == 'GET'
        assert request.get_header('Authorization') == 'Bearer fixture'
        url = urlsplit(request.full_url)
        assert url.netloc == 'reports.example'
        if url.path.endswith('/export'):
            raw = json.dumps(manifest).encode()
        else:
            assert url.path == '/artifact-site/api/sites/test/download'
            query = parse_qs(url.query)
            assert query['version'] == ['ver_saved']
            raw = files[query['file'][0]]
        stream = io.BytesIO(raw)
        stream.headers = {'x-artifact-version': 'ver_saved'}
        return stream
    request = urllib.request.Request('https://reports.example/artifact-site/api/sites/test/export',
                                     headers={'Authorization': 'Bearer fixture'})
    with patch('urllib.request.build_opener', lambda *handlers: SimpleNamespace(open=response)):
        if damage:
            with pytest.raises(ValueError):
                download_export(request, tmp_path)
            assert not list(tmp_path.glob('remote-export-*.zip'))
            if damage != 'size':
                assert len(calls) == 1
        else:
            version, path = download_export(request, tmp_path)
            assert version == 'ver_saved'
            with zipfile.ZipFile(path) as archive:
                assert {name: archive.read(name) for name in archive.namelist()} == files
            assert len(calls) == 3
