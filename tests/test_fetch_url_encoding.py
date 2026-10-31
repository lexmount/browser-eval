import unittest
from unittest.mock import patch

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.native_routes import encode_http_target_url, normalized_http_parameters
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.http_client import HttpResponse


class TargetUrlEncodingTest(unittest.TestCase):
    def test_preserves_signed_ascii_url_and_existing_escapes(self):
        url = 'https://example.com/a%2Fb?q=a+b&sig=A%2FZ%3D&x=1&x=2#part'
        self.assertEqual(encode_http_target_url(url), url)
        encoded = encode_http_target_url('https://example.com/中文?q=空 格&x=%2F')
        self.assertEqual(encoded, 'https://example.com/%E4%B8%AD%E6%96%87?q=%E7%A9%BA%20%E6%A0%BC&x=%2F')
        self.assertEqual(encode_http_target_url(encoded), encoded)
        self.assertEqual(encode_http_target_url('https://例子.测试/中文'),
                         'https://xn--fsqu00a.xn--0zwm56d/%E4%B8%AD%E6%96%87')

    def test_rejects_non_http_inputs(self):
        for url in ('file:///tmp/file', '/relative', 'https:///missing-host'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                encode_http_target_url(url)

    def test_all_hosted_native_requests_encode_the_same_target(self):
        case = EvalCase('unicode', 'Unicode URL', 'https://example.com/中文?q=%2F', 'static', [])
        kinds = ('brightdata_unlocker', 'browser_use_fetch', 'browserbase_fetch',
                 'deepseek_fetch', 'crawl4ai_cloud', 'firecrawl', 'tavily_extract',
                 'exa_contents', 'cloudflare_markdown')
        for kind in kinds:
            with self.subTest(kind=kind):
                provider = build_provider(kind, {'type': kind, 'api_key': 'test-key',
                    'api_token': 'test-token', 'account_id': 'test-account', 'zone': 'test-zone',
                    'uniform_native_parameters': True})
                endpoint, options = provider.native_http_arguments(case, 30)
                body = options['json_body']
                sent = body.get('url', (body.get('urls') or [None])[0])
                self.assertEqual(sent, encode_http_target_url(case.url))
                normalized_http_parameters(case, options)
                self.assertEqual(case.url, 'https://example.com/中文?q=%2F')
                if 'url' in body:
                    body['url'] = 'https://other.example/'
                else:
                    body['urls'] = ['https://other.example/']
                with self.assertRaises(ValueError):
                    normalized_http_parameters(case, options)

    def test_browserbase_transport_receives_uri_not_iri(self):
        case = EvalCase('unicode', 'Unicode URL', 'https://example.com/中文', 'static', [])
        provider = build_provider('browserbase_fetch', {'type': 'browserbase_fetch',
            'api_key': 'test-key', 'uniform_native_parameters': True})
        def strict_service(endpoint, **options):
            self.assertTrue(options['json_body']['url'].isascii())
            self.assertTrue(options['json_body']['proxies'])
            return HttpResponse(200, endpoint, {}, b'{"content":"native answer","statusCode":200}')
        with patch('harness.web_fetch.providers.hosted_fetch.request', side_effect=strict_service) as call:
            result = provider.fetch(case, 30)
        call.assert_called_once()
        self.assertEqual(result.content, 'native answer')
        self.assertTrue(result.ok)
