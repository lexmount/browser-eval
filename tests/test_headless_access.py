import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.report_interfaces import HEADLESS_ACCESS, SPECS, render_interfaces


class HeadlessAccessTests(unittest.TestCase):
    def test_all_measured_engines_have_account_and_official_sources(self):
        self.assertEqual(set(HEADLESS_ACCESS),
                         {'kitesurf','lightpanda','obscura','moli-1.0.6','moli','moli-1.1.3','moli-1.1.7','moli-pr643','chromium'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'adoption').mkdir()
            body = render_interfaces(root, [])
            self.assertEqual(body.count('<h3>账号与获取入口</h3>'), len(set(SPECS) & set(HEADLESS_ACCESS)))
            for name, access in HEADLESS_ACCESS.items():
                if name in SPECS:
                    saved = json.loads((root / f'adoption/interfaces/{name}.json').read_text())
                    self.assertEqual(saved['access']['account'], access['account'])
                self.assertTrue(access['configuration'])
                self.assertTrue(access['links'])
                self.assertTrue(all(url.startswith('https://') for _, url in access['links']))
            self.assertIn('&lt;API_TOKEN&gt;', body)
            self.assertIn('不公开真实凭据', body)


if __name__ == '__main__':
    unittest.main()
