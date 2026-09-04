import asyncio
import io
import json
import base64
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class NativeFileWorkerTests(unittest.TestCase):
    def load_worker(self, strategy):
        def config(**kwargs):return SimpleNamespace(**kwargs)
        modules={'crawl4ai':SimpleNamespace(AsyncWebCrawler=object, BrowserConfig=config,
                    CrawlerRunConfig=config, CacheMode=object),
                 'crawl4ai.async_crawler_strategy':SimpleNamespace(AsyncHTTPCrawlerStrategy=strategy),
                 'crawl4ai.async_configs':SimpleNamespace(HTTPCrawlerConfig=config)}
        spec=importlib.util.spec_from_file_location('file_worker_test',Path(__file__).resolve().parents[1]/'scripts/oss/crawl4ai_worker.py')
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,modules):spec.loader.exec_module(module)
        return module

    def test_native_strategy_returns_files_without_extra_fetch(self):
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'file.png'; raw=b'\x89PNG\r\n\x1a\n'+b'x'*40;path.write_bytes(raw)
            class Strategy:
                def __init__(self,**kwargs):calls.append(kwargs)
                async def __aenter__(self):return self
                async def __aexit__(self,*args):pass
                async def crawl(self,url,config):
                    calls.append((url,config.page_timeout))
                    return SimpleNamespace(status_code=200,html='',redirected_url=url,downloaded_files=[str(path)])
            worker=self.load_worker(Strategy)
            result=asyncio.run(worker.fetch_file({'url':'https://example.com/file.png','timeout_seconds':45},directory))
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[1],('https://example.com/file.png',45000))
            self.assertTrue(result['ok'])
            self.assertEqual(base64.b64decode(result['files'][0]['base64']),raw)
            self.assertEqual(result['metadata']['implementation'],'crawl4ai.AsyncHTTPCrawlerStrategy')

    def test_download_paths_cannot_escape(self):
        worker=self.load_worker(object)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'allowed').mkdir(); other=root/'secret';other.write_bytes(b'private')
            with self.assertRaises(ValueError):worker.encode_downloads([other],root/'allowed')

    def test_file_only_worker_does_not_start_browser(self):
        class Strategy:
            def __init__(self,browser_config):self.directory=browser_config.downloads_path
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def crawl(self,url,config):
                path=Path(self.directory)/'file.png';path.write_bytes(b'\x89PNG\r\n\x1a\n'+b'x'*40)
                return SimpleNamespace(status_code=200,html='',redirected_url=url,downloaded_files=[str(path)])
        worker=self.load_worker(Strategy)
        def forbidden_browser(**kwargs):raise AssertionError('File path must not launch Chrome')
        worker.AsyncWebCrawler=forbidden_browser
        request={'url':'https://example.com/file.png','timeout_seconds':45,'task_kind':'file'}
        output=io.StringIO()
        with patch('sys.stdin',io.StringIO(json.dumps(request)+'\n')), patch('sys.stdout',output):
            asyncio.run(worker.main())
        self.assertTrue(json.loads(output.getvalue())['ok'])
