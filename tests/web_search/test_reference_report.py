"""Reference comparisons preserve sourced capabilities without inventing measurements."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.web_search.report import build_reference, check


def fixture():
    return {
        "schema": "web-search-reference/1", "checked": "2026-10-20",
        "title": {"en": "Synthetic API reference", "zh": "合成接口说明"},
        "scope": {"en": "Documented capabilities; no quality ranking.", "zh": "文档能力对照，不含质量排名。"},
        "lead": {"en": "A summary is not full content.", "zh": "摘要不等于全文。"},
        "sources": [{"id": "S1", "url": "https://example.org/spec",
                     "label": {"en": "Example spec", "zh": "示例规范"},
                     "note": {"en": "Synthetic source", "zh": "合成来源"}}],
        "sections": [{"id": "capabilities", "nav": {"en": "Capabilities", "zh": "功能"},
                      "title": {"en": "Content fields", "zh": "内容字段"},
                      "headers": {"en": ["Field", "Meaning"], "zh": ["字段", "含义"]},
                      "rows": [{"en": ["summary", "Excerpt"], "zh": ["summary", "摘要"]}],
                      "paragraphs": [{"en": "Generation method undisclosed.", "zh": "生成方式未公开。", "sources": ["S1"]}],
                      "samples": [{"label": {"en": "Request", "zh": "请求"},
                                   "note": {"en": "Illustrative", "zh": "结构示例"}, "code": '{"query":"test"}'}]}],
    }


class ReferenceReportScenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.content = self.root / 'content.json'
        self.content.write_text(json.dumps(fixture()))
        self.out = self.root / 'report'
        build_reference(self.out, self.content, 'zh')

    def test_chinese_report_has_english_not_generated_entry(self):
        self.assertTrue(check(self.out))
        self.assertIn('data-english-not-generated', (self.out / 'en/index.html').read_text())

    def test_featured_input_output_is_visible_without_expanding(self):
        from bs4 import BeautifulSoup
        data = fixture()
        data["sections"][0]["samples"][0]["open"] = True
        self.content.write_text(json.dumps(data))
        build_reference(self.out, self.content, "en")
        for route in ("index.html", "zh/index.html"):
            soup = BeautifulSoup((self.out / route).read_text(), "html.parser")
            self.assertTrue(soup.select_one("details").has_attr("open"))
            self.assertEqual(soup.select_one("details code").get_text(), '{"query":"test"}')
        page = self.out / "index.html"
        page.write_text(page.read_text().replace("<details open>", "<details>"))
        with self.assertRaisesRegex(ValueError, "visibility"):
            check(self.out)

    def test_rebuild_keeps_the_selected_primary_language(self):
        from harness.web_search.report import main
        main(['--reference-content', str(self.content), '--output', str(self.out)])
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertEqual(manifest['primary_language'], 'zh')

    def test_release_rejects_missing_operated_browser_coverage(self):
        from harness.web_search.core import digest
        from harness.web_search.report import release_check
        receipt = self.root / 'visual.json'
        receipt.write_text(json.dumps({
            'manifest_sha256': digest((self.out / 'manifest.json').read_bytes()),
            'captures': [], 'interactions': [],
        }))
        with self.assertRaises(ValueError):
            release_check(self.out, receipt)

    def test_changed_capability_is_rejected(self):
        p = self.out / 'index.html'
        p.write_text(p.read_text().replace('<td>摘要</td>', '<td>全文</td>'))
        with self.assertRaises(ValueError):
            check(self.out)

    def test_missing_source_or_qualification_is_rejected(self):
        p = self.out / 'index.html'
        original = p.read_text()
        for changed in (original.replace('id="source-S1"', 'id="absent"'),
                        original.replace('生成方式未公开。', '由大模型生成。')):
            p.write_text(changed)
            with self.assertRaises(ValueError):
                check(self.out)

    def test_missing_language_and_changed_input_are_rejected(self):
        (self.out / 'en/index.html').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            check(self.out)
        build_reference(self.out, self.content, 'en')
        (self.out / 'reference-input.json').write_text('{}')
        with self.assertRaises(ValueError):
            check(self.out)

    def test_changed_downloadable_response_is_rejected(self):
        from harness.web_search.core import digest
        artifact = self.root / 'response.json'
        artifact.write_text('{"results":[]}')
        data = fixture()
        data['artifacts'] = [{"path": artifact.name, "sha256": digest(artifact.read_bytes()),
                              "label": {"en": "Response", "zh": "响应"}}]
        self.content.write_text(json.dumps(data))
        build_reference(self.out, self.content, 'zh')
        self.assertTrue(check(self.out))
        (self.out / artifact.name).write_text('{"results":[1]}')
        with self.assertRaises(ValueError):
            check(self.out)
