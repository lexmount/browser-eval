import json
import tempfile
import unittest
from pathlib import Path
from harness.web_fetch.report import table, cell, link
from harness.web_fetch.report_methods import PROFILES
from harness.web_fetch.report_capabilities import CAPABILITIES, CHECKED_AT, SOURCES, render_capabilities
from harness.web_fetch.report_interfaces import SPECS


class CapabilityTests(unittest.TestCase):
    def test_source_backed_capabilities_link_to_current_pinned_revisions(self):
        expected = {
            'claw': '12c4bdf7482f24c85b011e9d858fdec3c245101c',
            'claw-code': '12c4bdf7482f24c85b011e9d858fdec3c245101c',
            'claw-html': '12c4bdf7482f24c85b011e9d858fdec3c245101c',
            'claw-reader': '12c4bdf7482f24c85b011e9d858fdec3c245101c',
            'dsh': 'dd6322d604e00eec1ba5e0c8541159906a21094a',
            'dsh-text': 'dd6322d604e00eec1ba5e0c8541159906a21094a',
        }
        for source, revision in expected.items():
            self.assertIn(f'/blob/{revision}/', SOURCES[source][1], source)
        self.assertIn('/blob/libs/community/v0.3.30/', SOURCES['lc'][1])
        self.assertIn('/blob/v1.1.4/', SOURCES['moli'][1])
        self.assertIn('/blob/v1.1.4/', SOURCES['moli-recipes'][1])
        self.assertTrue(SOURCES['moli-release'][1].endswith('/releases/tag/v1.1.4'))

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
        self.assertIn('只请求 rawBase64', CAPABILITIES['firecrawl']['tested'])
        self.assertEqual(CAPABILITIES['tavily']['document']['status'], '未声明')
        self.assertEqual(CAPABILITIES['crawl4ai']['document']['status'], '需配置')
        self.assertEqual(CAPABILITIES['deepseek-harness-built-in']['file']['status'], '无此能力')
        self.assertIn('不直接提取 img.src', CAPABILITIES['openclaw-built-in']['notes'])
        self.assertEqual(CAPABILITIES['tavily']['js']['status'], '支持')
        self.assertIn('不是读取已有 PDF', CAPABILITIES['cloudflare-markdown']['notes'])

    def test_current_capability_page_matches_the_certified_routes(self):
        self.assertEqual(CHECKED_AT, '2026-09-11')
        self.assertIn('advanced + markdown', CAPABILITIES['tavily']['tested'])
        self.assertNotIn('basic + markdown', CAPABILITIES['tavily']['tested'])
        self.assertIn('text.verbosity=full', CAPABILITIES['exa']['tested'])
        self.assertIn('PDFCrawlerStrategy', CAPABILITIES['crawl4ai']['tested'])
        self.assertIn('domcontentloaded', CAPABILITIES['crawl4ai']['tested'])
        self.assertIn('wait_until="domcontentloaded"', SPECS['crawl4ai']['request'])
        self.assertIn('DOMContentLoaded', SPECS['crawl4ai']['notes'])
        self.assertNotIn('未启用专用 PDF 策略', CAPABILITIES['crawl4ai']['tested'])
        self.assertIn('Actions wait', CAPABILITIES['firecrawl']['tested'])
        self.assertIn('iframe', CAPABILITIES['firecrawl']['tested'])
        self.assertIn('Moli 1.1.4', CAPABILITIES['moli-webfetch']['tested'])
        self.assertIn('不传 --dump', CAPABILITIES['moli-webfetch']['tested'])
        self.assertIn('selector', CAPABILITIES['moli-webfetch']['tested'])

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
