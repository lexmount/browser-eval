import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


@unittest.skipUnless(importlib.util.find_spec('bs4') and importlib.util.find_spec('requests'),'requires reference-audit extra')
class SourceExtractionTests(unittest.TestCase):
    def test_wikipedia_navbox_drift_does_not_change_article_reference(self):
        from scripts.collect_source_evidence import extract_html
        def extract(navbox,body='Stable article prose'):
            raw=('''<html><head><title>Article</title></head><body>
            <div id="mw-content-text"><div class="mw-parser-output">
            <p>'''+body+'''</p><div class="navbox-styles">style marker</div>
            <div class="navbox" role="navigation">'''+navbox+'''</div>
            <aside role="navigation">Generic navigation marker</aside>
            </div></div></body></html>''').encode()
            return extract_html(raw,
                'https://en.wikipedia.org/w/index.php?title=Article&oldid=123')[1]
        original=extract('Old transcluded navigation')
        nav_changed=extract('New transcluded navigation')
        body_changed=extract('New transcluded navigation','Changed article prose')
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),
                         hashlib.sha256(nav_changed.encode()).hexdigest())
        self.assertNotEqual(hashlib.sha256(original.encode()).hexdigest(),
                            hashlib.sha256(body_changed.encode()).hexdigest())
        self.assertNotIn('navigation',original)
        self.assertNotIn('Generic',original)

    def test_wikipedia_oldid_excludes_live_excerpt_transclusion_only(self):
        from scripts.collect_source_evidence import extract_html
        def extract(url):
            raw = b'''<html><body><div id="mw-content-text">
            <div class="mw-parser-output"><p>Stable revision prose</p>
            <div class="excerpt-block" typeof="mw:Transclusion">
            <div class="excerpt"><p>Mutable external excerpt</p></div></div>
            <div typeof="mw:Transclusion"><p>Fixed in-page template</p></div>
            </div></div></body></html>'''
            return extract_html(raw, url)[1]
        fixed = extract(
            'https://en.wikipedia.org/w/index.php?title=Article&oldid=123')
        live = extract('https://en.wikipedia.org/wiki/Article')
        self.assertIn('Stable revision prose', fixed)
        self.assertIn('Fixed in-page template', fixed)
        self.assertNotIn('Mutable external excerpt', fixed)
        self.assertIn('Mutable external excerpt', live)

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

    def test_excludes_mdn_last_modified_timestamp_but_keeps_article_body(self):
        from scripts.collect_source_evidence import extract_html
        raw = b'''<html><body><main><article>
        <p>WebSocket article body</p>
        <p class="article-footer__last-modified">This page was last modified on
        <time>Sep 12, 2026</time> by MDN contributors.</p>
        </article></main></body></html>'''
        _, text, _, _ = extract_html(
            raw, 'https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API')
        self.assertEqual(text, 'WebSocket article body')

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
