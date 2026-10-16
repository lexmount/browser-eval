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
        from lexbench_webfetch.models import EvalCase
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

    def test_partial_public_dns_failure_is_retained_for_the_affected_calls(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        for mode in ('system','public_doh'):
            provider = SimpleNamespace(type='cowagent_builtin', config={'dns_mode':mode})
            def resolve(host, *args, **kwargs):
                if host == 'unresolved.example':raise socket.gaierror('failure')
                return ([(socket.AF_INET,socket.SOCK_STREAM,6,'',('185.199.108.153',443))]
                        if mode=='system' else [('185.199.108.153',4)])
            target=('socket.getaddrinfo' if mode=='system' else 'harness.web_fetch.network_dns.lookup_public_dns')
            with self.subTest(mode=mode), patch(target,side_effect=resolve):
                audit = preflight_guarded_providers([provider], [
                    SimpleNamespace(url='https://public.example/'),
                    SimpleNamespace(url='https://unresolved.example/')])
                self.assertEqual(audit['unresolved_hosts'], ['unresolved.example'])
                self.assertFalse(audit['usable_for_guarded_local_comparison'])

    def test_all_public_dns_failures_stop_before_product_calls(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        for mode in ('system','public_doh'):
            provider=SimpleNamespace(type='cowagent_builtin',config={'dns_mode':mode})
            target=('socket.getaddrinfo' if mode=='system' else 'harness.web_fetch.network_dns.lookup_public_dns')
            with self.subTest(mode=mode), patch(target,side_effect=socket.gaierror('failure')):
                with self.assertRaisesRegex(ValueError,'all declared public benchmark hosts'):
                    preflight_guarded_providers([provider],[SimpleNamespace(url='https://one.example/'),
                                                            SimpleNamespace(url='https://two.example/')])

    def test_partial_dns_failure_reaches_both_measured_calls(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from lexbench_webfetch.models import EvalCase
        from harness.web_fetch.models import ProviderResult
        from harness.web_fetch.providers.base import Provider
        from harness.web_fetch.runner import run_evaluation
        for mode in ('system','public_doh'):
            calls=[]
            class GuardedFixture(Provider):
                def fetch(self, case, timeout):
                    calls.append(case.id)
                    return ProviderResult(ok=case.id=='resolved',
                        content='public body' if case.id=='resolved' else '',
                        error_type=None if case.id=='resolved' else 'dns',
                        error_message=None if case.id=='resolved' else 'name resolution failed',metadata={})
            provider=GuardedFixture('guarded',{'type':'openclaw_builtin','dns_mode':mode})
            def resolve(host,*args,**kwargs):
                if host=='unresolved.example':raise socket.gaierror('failure')
                return ([(socket.AF_INET,socket.SOCK_STREAM,6,'',('185.199.108.153',443))]
                        if mode=='system' else [('185.199.108.153',4)])
            target=('socket.getaddrinfo' if mode=='system' else 'harness.web_fetch.network_dns.lookup_public_dns')
            with self.subTest(mode=mode), TemporaryDirectory() as directory, patch(target,side_effect=resolve):
                output=Path(directory)/'measurement'
                rows,_=run_evaluation(output_dir=output,dataset_meta={},dataset_path=Path('fixture.json'),
                    cases=[EvalCase('resolved','resolved','https://public.example/','control',[]),
                           EvalCase('unresolved','unresolved','https://unresolved.example/','control',[])],
                    providers=[provider],provider_config_path=Path('fixture-providers.json'),
                    runs=1,timeout=45,delay=0,max_failure_retries=0)
                audit=__import__('json').loads((output/'network-preflight.json').read_text())
                self.assertEqual(calls,['resolved','unresolved'])
                self.assertEqual([row['error_type'] for row in rows],[None,'dns'])
                self.assertEqual(audit['unresolved_hosts'],['unresolved.example'])

    def test_partial_failure_does_not_hide_private_or_synthetic_answers(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from harness.web_fetch.network_preflight import preflight_guarded_providers
        for mode in ('system','public_doh'):
            provider=SimpleNamespace(type='cowagent_builtin',config={'dns_mode':mode})
            for unsafe in ('198.18.0.1','10.0.0.1'):
                def resolve(host,*args,**kwargs):
                    if host=='unresolved.example':raise socket.gaierror('failure')
                    return ([(socket.AF_INET,socket.SOCK_STREAM,6,'',(unsafe,443))]
                            if mode=='system' else [(unsafe,4)])
                target=('socket.getaddrinfo' if mode=='system' else 'harness.web_fetch.network_dns.lookup_public_dns')
                with self.subTest(mode=mode,address=unsafe),patch(target,side_effect=resolve):
                    with self.assertRaisesRegex(ValueError,'Do not disable'):
                        preflight_guarded_providers([provider],[SimpleNamespace(url='https://unresolved.example/'),
                                                                SimpleNamespace(url='https://unsafe.example/')])


    def test_strict_public_dns_failure_never_falls_back_to_system_resolution(self):
        from unittest.mock import Mock, patch
        from harness.web_fetch.network_dns import public_dns_context
        fallback = Mock(side_effect=AssertionError('system fallback must not run'))
        resolver = Mock(side_effect=socket.gaierror('failure'))
        with patch('harness.web_fetch.network_dns._SYSTEM_GETADDRINFO', fallback):
            with public_dns_context('public_doh', 1, resolver=resolver):
                with self.assertRaises(socket.gaierror):
                    socket.getaddrinfo('unresolved.example', 443)
        resolver.assert_called_once()
        fallback.assert_not_called()

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


def test_explicit_isolated_fixtures_reach_the_runner(tmp_path):
    from unittest.mock import patch
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.providers.base import Provider
    from harness.web_fetch.runner import run_evaluation
    class Fixture(Provider):
        def fetch(self, case, timeout):
            return ProviderResult(ok=True, content='Fixture body')
    provider = Fixture('isolated', {'type': 'headless_markdown', 'engine': 'chromium',
        'network_policy': 'loopback_only', 'allow_private_network': True})
    with patch('socket.getaddrinfo', side_effect=AssertionError('fixture requires no public DNS lookup')):
        rows, _ = run_evaluation(output_dir=tmp_path/'run', dataset_meta={},
            dataset_path=tmp_path/'dataset.json',
            cases=[EvalCase('fixture', 'Fixture', 'http://127.0.0.1:12345/fixture', 'web', [])],
            providers=[provider], provider_config_path=tmp_path/'providers.json',
            runs=1, timeout=5, delay=0, max_failure_retries=0)
    assert len(rows) == 1 and rows[0]['ok']


def test_fixture_exemption_requires_isolation_and_loopback_scope():
    from types import SimpleNamespace
    from unittest.mock import patch
    import pytest
    from harness.web_fetch.network_preflight import preflight_guarded_providers
    fixture = {'engine': 'chromium', 'allow_private_network': True, 'network_policy': 'loopback_only'}
    with pytest.raises(ValueError, match='loopback task URLs'):
        preflight_guarded_providers([SimpleNamespace(type='headless_markdown', config=fixture)],
            [SimpleNamespace(url='https://example.com/')])
    for incomplete in ({'engine':'chromium', 'allow_private_network':True},
                       {'engine':'chromium', 'network_policy':'loopback_only'}):
        with patch('socket.getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))]):
            with pytest.raises(ValueError, match='non-public'):
                preflight_guarded_providers([SimpleNamespace(type='headless_markdown', config=incomplete)],
                    [SimpleNamespace(url='http://127.0.0.1/')])
