import io
import json
import socket
import threading
import unittest
from unittest.mock import patch
from harness.web_fetch.network_dns import lookup_public_dns, public_dns_context


class NetworkDnsTests(unittest.TestCase):
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
                observed["settings"] = json.loads((profile / "Local State").read_text())
                (profile / "DevToolsActivePort").write_text("12345\n/browser-endpoint\n")
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
