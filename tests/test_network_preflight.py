import socket
import unittest
from harness.web_fetch.network_preflight import inspect_system_dns, require_usable_public_dns


class NetworkPreflightTests(unittest.TestCase):
    def test_synthetic_dns_fails_before_calls_but_public_dns_passes(self):
        for address, accepted in [
            ('198.19.93.254', False),
            ('10.2.0.11', False),
            ('185.199.108.153', True),
        ]:
            with self.subTest(address=address):
                def resolve(host, port, **kwargs):
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
                audit = inspect_system_dns(['https://public.example/page'], resolver=resolve)
                self.assertEqual(audit['usable_for_guarded_local_comparison'], accepted)
                if accepted:
                    require_usable_public_dns(audit)
                else:
                    with self.assertRaisesRegex(ValueError, 'Do not disable'):
                        require_usable_public_dns(audit)

    def test_deduplicates_hosts_and_records_dns_failure_without_reclassifying_it(self):
        calls=[]
        def resolve(host, port, **kwargs):
            calls.append(host)
            raise socket.gaierror('unavailable')
        audit=inspect_system_dns(['https://public.example/a', 'https://public.example/b'], resolver=resolve)
        self.assertEqual(calls, ['public.example'])
        self.assertEqual(audit['hosts'][0]['dns_error'], 'gaierror')
        self.assertFalse(audit['usable_for_guarded_local_comparison'])
        with self.assertRaisesRegex(ValueError, 'resolution failed'):
            require_usable_public_dns(audit)


    def test_runner_rejects_synthetic_dns_before_calls_and_artifacts(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from harness.web_fetch.models import EvalCase
        from harness.web_fetch.providers.base import Provider
        from harness.web_fetch.runner import run_evaluation
        calls = []
        class GuardedFixture(Provider):
            def fetch(self, case, timeout):
                calls.append(case.id)
                raise AssertionError("No provider calls allowed in invalid DNS environment")
        provider = GuardedFixture('guarded', {'type': 'openclaw_builtin'})
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.19.0.1', 443))]
        with TemporaryDirectory() as directory, patch('socket.getaddrinfo', return_value=fake):
            output = Path(directory) / 'measurement'
            with self.assertRaisesRegex(ValueError, 'synthetic'):
                run_evaluation(output_dir=output, dataset_meta={}, dataset_path=Path('fixture.json'),
                               cases=[EvalCase('control', 'control', 'https://example.com/', 'control', [])],
                               providers=[provider], provider_config_path=Path('fixture-providers.json'),
                               runs=1, timeout=45, delay=0)
            self.assertEqual(calls, [])
            self.assertFalse(output.exists())


    def test_public_dns_mode_uses_real_resolver_and_records_mode(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        provider = SimpleNamespace(type='openclaw_builtin', config={'dns_mode': 'public_doh'})
        with patch('harness.web_fetch.network_dns.lookup_public_dns', return_value=[('185.199.108.153', 4)]) as resolve:
            audit = preflight_guarded_providers([provider], [SimpleNamespace(url='https://example.com/')])
        self.assertEqual(audit['dns_mode'], 'public_doh')
        self.assertFalse(audit['hosts'][0]['synthetic'])
        self.assertEqual(resolve.call_count, 1)

    def test_mixed_native_dns_modes_fail_before_resolution(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        providers = [SimpleNamespace(type='openclaw_builtin', config={'dns_mode': 'public_doh'}),
                     SimpleNamespace(type='langchain_webbase', config={})]
        with patch('harness.web_fetch.network_preflight.inspect_system_dns') as resolve:
            with self.assertRaisesRegex(ValueError, 'same dns_mode'):
                preflight_guarded_providers(providers, [])
        resolve.assert_not_called()

    def test_local_browser_and_native_method_must_share_dns_mode(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        providers = [
            SimpleNamespace(type='moli_webfetch', config={'dns_mode': 'public_doh'}),
            SimpleNamespace(type='headless_markdown',
                            config={'engine': 'lightpanda', 'dns_mode': 'system'}),
            SimpleNamespace(type='headless_markdown',
                            config={'engine': 'kitesurf'}),
        ]
        with patch('harness.web_fetch.network_preflight.inspect_system_dns') as resolve:
            with self.assertRaisesRegex(ValueError, 'same dns_mode'):
                preflight_guarded_providers(providers, [])
        resolve.assert_not_called()

    def test_public_dns_resolver_failure_stops_before_product_calls(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        provider = SimpleNamespace(type='cowagent_builtin', config={'dns_mode': 'public_doh'})
        with patch('harness.web_fetch.network_dns.lookup_public_dns', side_effect=socket.gaierror('failure')):
            with self.assertRaisesRegex(ValueError, 'Public DNS preflight failed'):
                preflight_guarded_providers([provider], [SimpleNamespace(url='https://example.com/')])

    def test_browser_workers_cannot_bypass_shared_dns_gate(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.0.1', 443))]
        for kind in ('moli_webfetch', 'crawl4ai'):
            with self.subTest(kind=kind), patch('socket.getaddrinfo', return_value=fake):
                with self.assertRaisesRegex(ValueError, 'synthetic'):
                    preflight_guarded_providers([SimpleNamespace(type=kind, config={})],
                                               [SimpleNamespace(url='https://example.com/')])
