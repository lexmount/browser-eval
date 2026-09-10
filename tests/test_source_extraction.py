import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec('bs4') and importlib.util.find_spec('requests'),'requires reference-audit extra')
class SourceExtractionTests(unittest.TestCase):
    def test_excludes_w3schools_share_controls_from_article_reference(self):
        from scripts.collect_source_evidence import extract_html
        raw = b'''<html><body><main id="main"><h1>HTML Tables</h1>
        <div class="ws-share ws-share--float"><span>Share</span>
        <button><span class="ws-copied-toast">Link Copied</span></button></div>
        <p>Define an HTML Table</p></main></body></html>'''
        _, text, selector, _ = extract_html(
            raw, 'https://www.w3schools.com/html/html_tables.asp')
        self.assertEqual(selector, '#main')
        self.assertIn('Define an HTML Table', text)
        self.assertNotIn('Share', text)
        self.assertNotIn('Link Copied', text)

    def test_excludes_lexmount_breadcrumb_and_adjacent_page_cards(self):
        from scripts.collect_source_evidence import extract_html
        raw = b'''<html><body><article>
        <div class="text-sm text-fd-muted-foreground">WebFetch API</div>
        <h1>Quick Start</h1><div class="prose flex-1">Request body</div>
        <div class="grid grid-cols-2"><a>All-in-One Extract</a></div>
        </article></body></html>'''
        _, text, selector, _ = extract_html(
            raw, 'https://browser.lexmount.com/docs/webfetch/quickstart')
        self.assertEqual(selector, 'article')
        self.assertEqual(text, 'Quick Start\nRequest body')

    def test_feishu_uses_article_structure_not_navigation(self):
        from scripts.collect_source_evidence import extract_html
        payload={'article':[{'html':{'html':'<article>正文 <b>AI</b> <span>此处是{{icon_info_outlined}}icon</span><table><tr><td>指标</td><td>含义</td></tr></table></article>'}}], 'faq':[{'question':'问题？','answer':'折叠回答'}]}
        raw=('<title>标题</title><nav>商城会员导航</nav><script>window._templateValue = '+json.dumps(payload)+';</script>').encode()
        _,text,selector,_=extract_html(raw,'https://www.feishu.cn/hc/example')
        self.assertNotIn('商城会员导航',text)
        self.assertNotIn('折叠回答',text)
        self.assertNotIn('{{icon_',text)
        for expected in ['正文','指标','含义','问题？']:self.assertIn(expected,text)
        self.assertNotEqual(selector,'body')

    def test_saved_reference_keeps_body_and_source_integrity(self):
        from scripts.collect_source_evidence import extract_html
        path=Path('evaluation/sources/lm-feishu-help/source.html.txt');raw=path.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        _,text,_,_=extract_html(raw,'https://www.feishu.cn/hc/example')
        self.assertIn('所有飞书 aily 工作流应用中',text.replace('\n',''))
        self.assertNotIn('自助购买飞书 AI 会员',text)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
