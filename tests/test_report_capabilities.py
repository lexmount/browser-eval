import json
import tempfile
import unittest
from pathlib import Path
from browser_harness_eval.report import table, cell, link
from browser_harness_eval.report_methods import PROFILES
from browser_harness_eval.report_capabilities import CAPABILITIES, SOURCES, render_capabilities


class CapabilityTests(unittest.TestCase):
    def test_all_evaluated_interfaces_have_scoped_evidence(self):
        self.assertEqual(set(PROFILES), set(CAPABILITIES))
        for name, row in CAPABILITIES.items():
            self.assertTrue(row['scope'], name)
            self.assertTrue(row['tested'], name)
            self.assertTrue(row['sources'], name)
            for source in row['sources']:
                self.assertTrue(SOURCES[source][1].startswith('https://'))
            for field in ['web', 'document', 'image', 'av', 'file', 'js']:
                self.assertIn(row[field]['status'], ['支持', '需配置', '仅链接', '有限支持', '无此能力', '未声明'])
                self.assertTrue(row[field]['detail'])

    def test_capability_is_not_inferred_from_scores_or_other_endpoints(self):
        self.assertEqual(CAPABILITIES['firecrawl']['file']['status'], '需配置')
        self.assertIn('未启用 rawBase64', CAPABILITIES['firecrawl']['tested'])
        self.assertEqual(CAPABILITIES['tavily']['document']['status'], '未声明')
        self.assertEqual(CAPABILITIES['crawl4ai']['document']['status'], '需配置')
        self.assertEqual(CAPABILITIES['deepseek-harness-built-in']['file']['status'], '无此能力')
        self.assertIn('不直接提取 img.src', CAPABILITIES['openclaw-built-in']['notes'])
        self.assertEqual(CAPABILITIES['tavily']['js']['status'], '支持')
        self.assertIn('不是读取已有 PDF', CAPABILITIES['cloudflare-markdown']['notes'])

    def test_render_has_every_evidence_target_and_download(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'adoption').mkdir()
            result = render_capabilities(out, sorted(PROFILES), table=table, cell=cell, link=link)
            for name in PROFILES:
                self.assertIn('href="#capability-'+name+'"', result)
                self.assertIn('id="capability-'+name+'"', result)
            self.assertIn('内容与资源', result)
            self.assertIn('运行与接入', result)
            self.assertNotIn('sortTable(', result)
            self.assertIn('不是实测成绩', result)
            exported = json.loads((out/'adoption/capabilities.json').read_text())
            self.assertEqual(set(exported['methods']), set(PROFILES))

    def test_unknown_provider_is_disclosed_instead_of_silently_omitting(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'adoption').mkdir()
            result = render_capabilities(out, ['new-provider'], table=table, cell=cell, link=link)
            self.assertIn('尚无已核对的官方能力资料：new-provider', result)
