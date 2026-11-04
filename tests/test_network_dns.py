import io
import json
import socket
import threading
import unittest
from unittest.mock import patch
from harness.web_fetch.network_dns import lookup_public_dns, public_dns_context


class NetworkDnsTests(unittest.TestCase):
    def test_shared_chromium_profile_enables_strict_doh_without_system_fallback(self):
        import tempfile
        from pathlib import Path
        from harness.web_fetch.network_dns import (
            DNS_ENDPOINT,
            configure_chromium_dns_profile,
        )
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            metadata = configure_chromium_dns_profile(profile, "public_doh")
            state = json.loads((profile / "Local State").read_text(encoding="utf-8"))
            self.assertEqual(state["dns_over_https"], {
                "mode": "secure", "templates": DNS_ENDPOINT + "{?dns}",
            })
            self.assertTrue(state["built_in_dns_client_enabled"])
            self.assertFalse(metadata["system_dns_fallback"])
        with tempfile.TemporaryDirectory() as directory:
            metadata = configure_chromium_dns_profile(directory, "system")
            self.assertEqual(metadata, {"dns_mode": "system"})
            self.assertFalse((Path(directory) / "Local State").exists())

    def test_native_process_environment_records_binding_and_resolutions(self):
        import time
        from pathlib import Path
        from harness.web_fetch.network_dns import native_process_dns_environment
        observed = {}
        class Broker:
            traces = [{'hostname': 'public.example', 'addresses': ['203.0.113.1']}]
            def __enter__(self): return self
            def __exit__(self, *args): observed['closed'] = True
            def begin_call(self, remaining): observed['remaining'] = remaining
            def environment(self, library, base=None):
                observed.update(library=library, base=base)
                return {'DNS': 'public'}
        metadata = {}
        with patch('harness.web_fetch.network_dns.validate_native_dns_library',
                   return_value=(Path('/resolver.dylib'), {'library_sha256': 'a'*64})), \
             patch('harness.web_fetch.network_dns.NativeDnsBroker', return_value=Broker()):
            with native_process_dns_environment(
                'public_doh', '/resolver.dylib', time.monotonic() + 2,
                metadata, {'BASE': '1'}
            ) as environment:
                self.assertEqual(environment, {'DNS': 'public'})
        self.assertEqual(observed['library'], Path('/resolver.dylib'))
        self.assertEqual(observed['base'], {'BASE': '1'})
        self.assertGreater(observed['remaining'], 0)
        self.assertTrue(observed['closed'])
        self.assertEqual(metadata['dns_mode'], 'public_doh')
        self.assertEqual(metadata['dns_resolutions'], Broker.traces)
        self.assertEqual(metadata['native_dns_binding']['library_sha256'], 'a'*64)

    def test_resolves_both_families_without_filtering_native_safety_input(self):
        calls = []
        def open_response(request, timeout):
            calls.append(request.full_url)
            rrtype = 28 if "type=28" in request.full_url else 1
            # Private answers must reach the native guard, never be silently
            # removed so a mixed public/private answer set appears safe.
            data = "2001:db8::1" if rrtype == 28 else "10.0.0.1"
            return io.BytesIO(json.dumps({"Status": 0, "Answer": [{"type": rrtype, "data": data}]}).encode())
        self.assertEqual(lookup_public_dns('fixture.example', timeout=45, opener=open_response),
                         [('10.0.0.1', 4), ('2001:db8::1', 6)])
        self.assertEqual(len(calls), 2)

    def test_dns_failure_has_no_fallback_or_retry(self):
        calls = []
        def fail(request, timeout):
            calls.append(request.full_url)
            raise OSError('resolver unavailable')
        with self.assertRaises(OSError):
            lookup_public_dns('fixture.example', timeout=45, opener=fail)
        self.assertEqual(len(calls), 1)

    def test_rejects_malformed_truncated_and_oversized_dns_answers(self):
        for payload in [{'Status': 2}, {'Status': 0, 'TC': True},
                        {'Status': 0, 'Answer': [{'type': 1, 'data': '::1'}]},
                        {'Status': 0, 'Answer': []}]:
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, socket.gaierror)):
                    lookup_public_dns('fixture.example', timeout=45,
                                      opener=lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode()))
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            lookup_public_dns('fixture.example', timeout=45,
                              opener=lambda *args, **kwargs: io.BytesIO(b'x'*65537))

    def test_context_scopes_dns_to_calling_thread_and_restores_after_error(self):
        original = socket.getaddrinfo
        calls = []
        def resolve(host, timeout):
            calls.append(host)
            return [('127.0.0.1', 4)]
        with self.assertRaisesRegex(RuntimeError, 'end control'):
            with public_dns_context('public_doh', 45, resolver=resolve) as traces:
                result = socket.getaddrinfo('fixture.invalid', 443, type=socket.SOCK_STREAM)
                self.assertEqual(result[0][4][0], '127.0.0.1')
                other = []
                def outside():
                    try:
                        socket.getaddrinfo('fixture.invalid', 443)
                    except socket.gaierror:
                        other.append('system DNS used')
                thread = threading.Thread(target=outside)
                thread.start(); thread.join()
                self.assertEqual(calls, ['fixture.invalid'])
                self.assertEqual(other, ['system DNS used'])
                self.assertEqual(traces[0]['hostname'], 'fixture.invalid')
                raise RuntimeError('end control')
        self.assertIs(socket.getaddrinfo, original)

    def test_resolver_https_bootstrap_keeps_original_dns(self):
        original = socket.getaddrinfo
        def resolve(host, timeout):
            # The resolver's transport must not recursively call itself.
            self.assertTrue(socket.getaddrinfo('127.0.0.1', 443))
            return [('127.0.0.1', 4)]
        with public_dns_context('public_doh', 45, resolver=resolve):
            socket.getaddrinfo('fixture.example', 443)
        self.assertIs(socket.getaddrinfo, original)

    def test_expired_budget_makes_zero_dns_queries(self):
        with self.assertRaises(TimeoutError):
            lookup_public_dns('fixture.example', timeout=0,
                              opener=lambda *args, **kwargs: self.fail('No DNS queries allowed'))

    def test_independent_provider_dns_calls_do_not_share_wait_time(self):
        original = socket.getaddrinfo
        entered, release, fast_done = (threading.Event() for _ in range(3))
        errors, answers = [], {}
        def slow_resolver(host, timeout):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("slow fixture was not released")
            return [("127.0.0.1", 4)]
        def run(name, resolver, timeout):
            try:
                with public_dns_context("public_doh", timeout, resolver=resolver):
                    answers[name] = socket.getaddrinfo(name + ".invalid", 443)[0][4][0]
            except Exception as error:
                errors.append(error)
            finally:
                if name == "fast": fast_done.set()
        slow = threading.Thread(target=run, args=("slow", slow_resolver, 2))
        fast = threading.Thread(target=run, args=("fast", lambda host, timeout: [("127.0.0.2", 4)], .5))
        slow.start()
        try:
            self.assertTrue(entered.wait(1))
            fast.start()
            completed_while_slow = fast_done.wait(.2)
        finally:
            release.set()
            slow.join(2)
            if fast.ident is not None: fast.join(2)
        self.assertTrue(completed_while_slow, "Fast provider waited for the slow provider")
        self.assertEqual(errors, [])
        self.assertEqual(answers, {"fast": "127.0.0.2", "slow": "127.0.0.1"})
        self.assertIs(socket.getaddrinfo, original)

    def test_nested_dns_scopes_restore_the_outer_resolver_after_error(self):
        original = socket.getaddrinfo
        outer = lambda host, timeout: [("127.0.0.1", 4)]
        inner = lambda host, timeout: [("127.0.0.2", 4)]
        with public_dns_context("public_doh", 2, resolver=outer) as traces:
            with self.assertRaisesRegex(RuntimeError, "inner failed"):
                with public_dns_context("public_doh", 2, resolver=inner):
                    self.assertEqual(socket.getaddrinfo("inner.invalid", 443)[0][4][0], "127.0.0.2")
                    raise RuntimeError("inner failed")
            with public_dns_context("system", 2):
                socket.getaddrinfo("127.0.0.3", 443)
            self.assertEqual(socket.getaddrinfo("outer.invalid", 443)[0][4][0], "127.0.0.1")
            self.assertEqual([row["hostname"] for row in traces], ["outer.invalid"])
        self.assertIs(socket.getaddrinfo, original)

    def test_system_mode_does_not_patch_socket(self):
        original = socket.getaddrinfo
        with public_dns_context('system', 45) as traces:
            self.assertIs(socket.getaddrinfo, original)
        self.assertEqual(traces, [])


class CrawlBrowserDnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_native_arguments_strict_dns_and_cleanup(self):
        import pathlib
        import sys
        from types import SimpleNamespace
        from harness.web_fetch.network_dns import crawl4ai_public_dns_browser, DNS_ENDPOINT
        observed = {}
        class Config:
            chrome_channel = "chrome"
            def clone(self, **kwargs):
                return SimpleNamespace(**kwargs)
        class Manager:
            def __init__(self, config):
                pass
            def _build_browser_args(self):
                return {"channel": "chrome", "headless": True, "args": ["native-flag"]}
        class Context:
            browser = SimpleNamespace(version="frozen-native-version")
            async def close(self):
                observed["closed"] = True
        class Chromium:
            async def launch_persistent_context(self, directory, **kwargs):
                observed.update(directory=directory, arguments=kwargs)
                profile = pathlib.Path(directory)
                observed["settings"] = json.loads((profile / "Local State").read_text(encoding="utf-8"))
                (profile / "DevToolsActivePort").write_text("12345\n/browser-endpoint\n", encoding="utf-8")
                return Context()
        class Driver:
            async def __aenter__(self):
                return SimpleNamespace(chromium=Chromium())
            async def __aexit__(self, *args):
                observed["driver_closed"] = True
        modules = {"playwright.async_api": SimpleNamespace(async_playwright=Driver),
                   "crawl4ai.browser_manager": SimpleNamespace(BrowserManager=Manager)}
        with patch.dict(sys.modules, modules):
            with self.assertRaisesRegex(RuntimeError, "call failed"):
                async with crawl4ai_public_dns_browser(Config()) as (config, metadata):
                    self.assertEqual(config.cdp_url, "http://127.0.0.1:12345")
                    self.assertFalse(metadata["system_dns_fallback"])
                    self.assertEqual(metadata["browser_version"], "frozen-native-version")
                    raise RuntimeError("call failed")
        self.assertEqual(observed["arguments"]["channel"], "chrome")
        self.assertIn("native-flag", observed["arguments"]["args"])
        self.assertEqual(observed["settings"]["dns_over_https"],
                         {"mode": "secure", "templates": DNS_ENDPOINT + "{?dns}"})
        self.assertTrue(observed["closed"] and observed["driver_closed"])
        self.assertFalse(pathlib.Path(observed["directory"]).exists())

    async def test_rejects_changed_browser_channel(self):
        from types import SimpleNamespace
        from harness.web_fetch.network_dns import crawl4ai_public_dns_browser
        with self.assertRaisesRegex(ValueError, "frozen Chrome channel"):
            async with crawl4ai_public_dns_browser(SimpleNamespace(chrome_channel="chromium")):
                self.fail("different browser must not start")


class HostedDnsBoundaryTests(unittest.TestCase):
    kinds = ('firecrawl', 'exa_contents', 'tavily_extract', 'cloudflare_markdown',
             'brightdata_unlocker', 'browser_use_fetch', 'browserbase_fetch',
             'deepseek_fetch', 'crawl4ai_cloud')

    def provider_and_case(self, kind, mode):
        from harness.web_fetch.providers import build_provider
        from lexbench_webfetch.models import EvalCase
        provider = build_provider(kind, {'type': kind, 'api_key': 'fixture',
            'api_token': 'fixture', 'account_id': 'fixture', 'zone': 'fixture',
            'dns_mode': mode, 'uniform_native_parameters': True})
        case = EvalCase('case', 'Case', 'https://target.invalid/page', 'static', [],
                       source_benchmark={'input_media_kind': 'html_page'})
        return provider, case

    def test_all_hosted_boundaries_honor_doh_and_system_modes(self):
        from urllib.parse import urlsplit
        from harness.web_fetch.providers.http_client import HttpResponse
        for kind in self.kinds:
            for mode in ('system', 'public_doh'):
                with self.subTest(kind=kind, mode=mode):
                    provider, case = self.provider_and_case(kind, mode)
                    endpoint, kwargs = provider.native_http_arguments(case, 30)
                    hostname = urlsplit(endpoint).hostname
                    seen = []
                    def transport(url, **arguments):
                        seen.append(socket.getaddrinfo(urlsplit(url).hostname, 443,
                                                     type=socket.SOCK_STREAM)[0][4][0])
                        return HttpResponse(200, url, {}, b'{}')
                    with patch('harness.web_fetch.network_dns.lookup_public_dns',
                               return_value=[('203.0.113.5', 4)]) as doh, \
                         patch('socket.getaddrinfo', side_effect=lambda host, *a, **kw: [
                             (socket.AF_INET, socket.SOCK_STREAM, 6, '',
                              (host if host == '203.0.113.5' else '127.0.0.1', 443))]) as system:
                        response = provider.native_http_request(case, transport, endpoint, **kwargs)
                    self.assertEqual(seen, ['203.0.113.5' if mode == 'public_doh' else '127.0.0.1'])
                    self.assertEqual(response.dns_metadata['dns_mode'], mode)
                    if mode == 'public_doh':
                        self.assertEqual(doh.call_args.args, (hostname,))
                        self.assertEqual(doh.call_count, 1)
                        self.assertEqual(system.call_args.args[0], '203.0.113.5')
                        self.assertEqual(response.dns_metadata['dns_trace'][0]['hostname'], hostname)
                    else:
                        doh.assert_not_called()
                        self.assertEqual(response.dns_metadata['dns_trace'], [])

    def test_invalid_mode_and_doh_failure_never_fall_back(self):
        from unittest.mock import Mock
        for kind in self.kinds:
            with self.subTest(kind=kind):
                provider, case = self.provider_and_case(kind, 'invalid')
                endpoint, kwargs = provider.native_http_arguments(case, 30)
                transport = Mock()
                with self.assertRaises(ValueError):
                    provider.native_http_request(case, transport, endpoint, **kwargs)
                transport.assert_not_called()
                provider.config['dns_mode'] = 'public_doh'
                def resolve_transport(url, **arguments):
                    socket.getaddrinfo('api.fixture.invalid', 443)
                with patch('harness.web_fetch.network_dns.lookup_public_dns',
                           side_effect=OSError('DNS unavailable')) as doh, \
                     patch('socket.getaddrinfo') as system:
                    with self.assertRaisesRegex(OSError, 'DNS unavailable'):
                        provider.native_http_request(case, resolve_transport, endpoint, **kwargs)
                self.assertEqual(doh.call_count, 1)
                system.assert_not_called()

    def test_invalid_dns_is_rejected_before_preflight_and_ledger_capture(self):
        from unittest.mock import Mock
        for kind in ('firecrawl', 'exa_contents', 'tavily_extract',
                     'cloudflare_markdown', 'cloudflare_content'):
            with self.subTest(kind=kind):
                for mode in ('system', 'public_doh'):
                    provider, _ = self.provider_and_case(kind, mode)
                    self.assertEqual(provider.validate(), [])
                provider, case = self.provider_and_case(kind, 'public-doh')
                self.assertTrue(any('dns_mode' in error for error in provider.validate()))
                endpoint, arguments = provider.native_http_arguments(case, 30)
                transport, ledger = Mock(), Mock()
                with self.assertRaisesRegex(ValueError, 'dns_mode'):
                    provider.native_http_request(case, transport, endpoint,
                                                 native_route_ledger=ledger, **arguments)
                ledger.observe.assert_not_called()
                transport.assert_not_called()
