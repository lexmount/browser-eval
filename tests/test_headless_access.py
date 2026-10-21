import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.report_interfaces import HEADLESS_ACCESS, SPECS, render_interfaces


class HeadlessAccessTests(unittest.TestCase):
    def test_all_measured_engines_have_account_and_official_sources(self):
        from harness.web_fetch.methods import browser_method_registry
        from harness.web_fetch.report_interfaces import method_access
        for name in browser_method_registry():
            self.assertTrue(method_access(name, [])['links'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'adoption').mkdir()
            (root/'body.txt').write_text('Public body')
            row={'provider':'kitesurf','case_id':'static-simple','attempt_id':'kitesurf-example',
                 'artifact_path':'body.txt','provider_metadata':{'api_token':'credential-canary',
                 'headers':{'Authorization':'Bearer credential-canary'},'engine':'kitesurf'}}
            body = render_interfaces(root, [row])
            self.assertEqual(body.count('<h3>账号与获取入口</h3>'), len(set(SPECS) & set(HEADLESS_ACCESS)))
            for name, access in HEADLESS_ACCESS.items():
                if name in SPECS:
                    saved = json.loads((root / f'adoption/interfaces/{name}.json').read_text())
                    self.assertEqual(saved['access']['account'], access['account'])
                self.assertTrue(access['configuration'])
                self.assertTrue(access['links'])
                self.assertTrue(all(url.startswith('https://') for _, url in access['links']))
            self.assertIn('&lt;API_TOKEN&gt;', body)
            self.assertNotIn('credential-canary', body)
            self.assertNotIn('credential-canary',(root/'adoption/interfaces/kitesurf.json').read_text())


    def test_exported_chromium_interface_uses_observed_completion(self):
        for wait in ('load', 'domcontentloaded', 'networkidle'):
            with self.subTest(wait=wait), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'adoption').mkdir()
                (root / 'body.txt').write_text('Body')
                record = {'provider': 'chromium', 'case_id': 'static-simple',
                          'attempt_id': 'chromium__static-simple__r001',
                          'artifact_path': 'body.txt',
                          'provider_metadata': {'navigation_wait_until': wait}}
                render_interfaces(root, [record])
                interface = json.loads((root / 'adoption/interfaces/chromium.json').read_text())['interface']
                self.assertEqual(interface['request']['wait_until'], wait)
                self.assertEqual(next(row[2] for row in interface['params'] if row[0] == 'wait_until'), wait)
        self.assertEqual(SPECS['chromium']['request']['wait_until'], 'load')

    def test_executable_version_is_not_replaced_by_cdp_compatibility_version(self):
        from harness.web_fetch.report_interfaces import method_version
        for name,version,compat in [('moli','1.1.15','145.0.0.0'),('lightpanda','1.0.0','124.0.6367.29'),('obscura','0.2.4','145.0.0.0')]:
            row={'provider':name,'provider_metadata':{'binary_sha256':'hash','browser_version':compat}}
            self.assertEqual(method_version(row,{'hash':{'version':version}}),version)
            self.assertIsNone(method_version(row))
            row['provider_metadata']['reported_version']=version
            self.assertEqual(method_version(row),version)
        self.assertEqual(method_version({'provider':'chromium','provider_metadata':{'browser_version':'155.0.8059.39'}}),'155.0.8059.39')
        self.assertIsNone(method_version({'provider':'kitesurf','provider_metadata':{'browser_version':'145.0.0.0'}}))
        self.assertEqual(method_version({'provider':'kitesurf','provider_metadata':{'reported_version':'service-build-9'}}),'service-build-9')


if __name__ == '__main__':
    unittest.main()
