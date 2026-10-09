import io
from http.client import IncompleteRead
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.verify_artifact_hub import download_export, verify_metadata, verify_public_files


class PublicationVerifyTests(unittest.TestCase):
    def test_exported_tree_verification_is_shared_and_byte_exact(self):
        from harness.web_fetch.publication import verify_published_tree
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'index.html').write_bytes(b'<head></head>Report')
            (root/'.generated-report').write_text('generated')
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
        with patch('scripts.verify_artifact_hub.load_token',return_value='fixture'), \
             patch('scripts.verify_artifact_hub.urllib.request.urlopen',
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
            with patch('scripts.verify_artifact_hub.load_token',return_value='fixture'), \
                 patch('scripts.verify_artifact_hub.urllib.request.urlopen',side_effect=response):
                return verify_public_files('test',archive)

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
            with patch('scripts.verify_artifact_hub.load_token',return_value='fixture'), \
                 patch('scripts.verify_artifact_hub.urllib.request.urlopen',side_effect=response):
                with self.assertRaisesRegex(ValueError,'Remote file differs: index.html'):
                    verify_public_files('test',archive)

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
            with patch('scripts.verify_artifact_hub.load_token',return_value='fixture'), \
                 patch('scripts.verify_artifact_hub.time.sleep'), \
                 patch('scripts.verify_artifact_hub.urllib.request.urlopen',side_effect=response):
                self.assertEqual(verify_public_files('test',archive)['files'],2)
        self.assertEqual(calls,2)

    def test_export_retries_server_error(self):
        class Export(io.BytesIO):
            headers={'x-artifact-version':'ver_first'}
        with tempfile.TemporaryDirectory() as temp, \
             patch('scripts.verify_artifact_hub.time.sleep'), \
             patch('scripts.verify_artifact_hub.urllib.request.urlopen',side_effect=[
                 __import__('urllib.error').error.HTTPError('url',502,'bad gateway',{},None),
                 Export(b'archive')]):
            version,path=download_export('request',Path(temp))
            self.assertEqual(version,'ver_first')
            self.assertEqual(path.read_bytes(),b'archive')
