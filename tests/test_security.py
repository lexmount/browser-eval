import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.providers.http_client import HttpOnlyRedirectHandler, HttpResponse, request
from harness.web_fetch.report import case_attrs, dataset_attrs, generate, link, write
from harness.web_fetch.runner import load_records, rescore_evaluation, run_evaluation
from harness.web_fetch.safety import contained_path, safe_identifier
from harness.web_fetch.webmainbench_correction import copy_contained_artifact


class SecurityTests(unittest.TestCase):
    def test_dataset_and_attempt_attributes_are_escaped(self):
        payload='x" onmouseover="alert(1)\'><script>alert(2)</script>&'
        case={'track':payload,'category':payload}
        record={'provider':payload,'ok':True,'metrics':{'content_pass':True}}
        class Tags(HTMLParser):
            def __init__(self): super().__init__(); self.tags=[]
            def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))
        for attrs in [dataset_attrs(case),case_attrs(record,case)]:
            parser=Tags(); parser.feed('<tr'+attrs+'></tr>')
            self.assertEqual(len(parser.tags),1)
            tag,values=parser.tags[0]
            self.assertEqual(tag,'tr')
            self.assertEqual(values['data-track'],payload)
            self.assertEqual(values['data-category'],payload)
            self.assertTrue(all(name.startswith('data-') for name in values))

    def test_identifiers(self):
        for bad in ['../out', '/tmp/x', '..', '.', 'a/b', 'a\\b', 'x\" onclick=\"x']:
            with self.subTest(bad=bad), self.assertRaises(ValueError): safe_identifier(bad)
        self.assertEqual(safe_identifier('fixture__case-1__r001'), 'fixture__case-1__r001')

    def test_paths_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'root'; root.mkdir()
            outside=Path(tmp)/'outside'; outside.mkdir()
            (root/'escape').symlink_to(outside, target_is_directory=True)
            for bad in ['../outside', '/tmp/out', 'C:\\out', 'escape/data.txt', '..\\out']:
                with self.subTest(bad=bad), self.assertRaises(ValueError): contained_path(root,bad)
            with self.assertRaises(ValueError): write(root,'escape/out.html','x','x')
            (root/'results.jsonl').symlink_to(outside/'results.jsonl')
            with self.assertRaises(ValueError): load_records(root)
            self.assertFalse((outside/'out.html').exists())

    def test_evidence_inheritance_confines_both_copy_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';output=root/'output';outside=root/'outside'
            (source/'nested').mkdir(parents=True);output.mkdir();outside.mkdir()
            (source/'nested/result.md').write_text('result')
            copied=copy_contained_artifact(source,'nested/result.md',output,'artifacts/result.md')
            self.assertEqual(copied.read_text(),'result')
            (output/'escape').symlink_to(outside,target_is_directory=True)
            for source_path,target_path in [
                ('../outside/result.md','artifacts/result.md'),
                ('nested/result.md','../outside/result.md'),
                ('nested/result.md','/tmp/result.md'),
                ('nested/result.md','escape/result.md'),
            ]:
                with self.subTest(source=source_path,target=target_path), self.assertRaises(ValueError):
                    copy_contained_artifact(source,source_path,output,target_path)
            self.assertFalse((outside/'result.md').exists())

    def test_runner_rejects_traversal_before_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider=FixtureProvider('../escape',{'type':'fixture'})
            with self.assertRaises(ValueError):
                run_evaluation(output_dir=Path(tmp)/'run',dataset_meta={},dataset_path=Path('data.json'),cases=[],providers=[provider],provider_config_path=Path('providers.json'),runs=1,timeout=1,delay=0)
            self.assertFalse((Path(tmp)/'run').exists())

    def test_rescore_rejects_artifact_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run';root.mkdir()
            (root/'results.jsonl').write_text('{"case_id":"c","artifact_path":"../secret.txt"}\n')
            with self.assertRaises(ValueError):
                rescore_evaluation(run_dir=root,dataset_meta={},dataset_path=Path('data.json'),cases=[EvalCase('c','c','https://example.com','static',[])])

    def test_report_ids_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'report'
            with self.assertRaises(ValueError): generate(output,{'cases':[{'id':'../escape'}]},[],[])
            self.assertFalse(output.exists())

    def test_unsafe_hrefs(self):
        for url in ['javascript:alert(1)', ' JAVASCRIPT:alert(1)', 'java\nscript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', '//evil.test', '\\evil.test']:
            with self.subTest(url=url): self.assertEqual(link(url,'label'),'<a href="#">label</a>')
        for url in ['https://example.com', '../datasets/c.html', '#facts']:
            self.assertIn('href="'+url+'"',link(url,'label'))

    def test_charset(self):
        for header in ['text/plain; charset=unknown-charset', 'text/plain; CHARSET="UTF-8"']:
            self.assertEqual(HttpResponse(200,'https://example.com',{'content-type':header},'正文'.encode()).text(),'正文')
        self.assertEqual(HttpResponse(200,'https://example.com',{'content-type':'text/plain; Charset="GBK"'},'正文'.encode('gbk')).text(),'正文')

    def test_non_http_never_opens(self):
        with patch('urllib.request.build_opener') as opener:
            for url in ['file:///etc/passwd','data:text/plain,x','ftp://example.com/x']:
                with self.assertRaises(ValueError): request(url)
            opener.assert_not_called()

    def test_redirect_scheme(self):
        handler=HttpOnlyRedirectHandler()
        with self.assertRaises(ValueError):
            handler.redirect_request(Request('https://example.com'),None,302,'Found',{},'file:///etc/passwd')

    def test_response_closes_on_size_error(self):
        response=Mock();response.read.return_value=b'too long'
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            with self.assertRaises(ValueError): request('https://example.com',max_bytes=1)
        response.close.assert_called_once()
