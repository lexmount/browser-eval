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

    def test_system_mode_does_not_patch_socket(self):
        original = socket.getaddrinfo
        with public_dns_context('system', 45) as traces:
            self.assertIs(socket.getaddrinfo, original)
        self.assertEqual(traces, [])
