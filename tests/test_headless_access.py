import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.report_interfaces import HEADLESS_ACCESS, render_interfaces


class HeadlessAccessTests(unittest.TestCase):
    def test_all_measured_engines_have_account_and_official_sources(self):
        self.assertEqual(set(HEADLESS_ACCESS), {'kitesurf','lightpanda','obscura','moli','chromium'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'adoption').mkdir()
            body = render_interfaces(root, [])
            self.assertEqual(body.count('<h3>账号与获取入口</h3>'), 5)
            for name, access in HEADLESS_ACCESS.items():
                saved = json.loads((root / f'adoption/interfaces/{name}.json').read_text())
                self.assertEqual(saved['access']['account'], access['account'])
                self.assertTrue(access['configuration'])
                self.assertTrue(access['links'])
                self.assertTrue(all(url.startswith('https://') for _, url in access['links']))
            self.assertIn('&lt;API_TOKEN&gt;', body)
            self.assertIn('不属于本轮实测路径', body)


if __name__ == '__main__':
    unittest.main()
