import json
import tempfile
import unittest
from pathlib import Path
from harness.web_fetch.report import table, cell, link
from harness.web_fetch.report_methods import PROFILES
from harness.web_fetch.report_capabilities import CAPABILITIES, SOURCES, render_capabilities
from harness.web_fetch.report_interfaces import SPECS


class CapabilityTests(unittest.TestCase):
    def test_native_cohort_discloses_native_document_output_without_external_processing(self):
        from harness.web_fetch.report_capabilities import task_capabilities
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            (out/'dataset.json').write_text(json.dumps({'cases':[
                {'id':'body','url':'https://example.com/','track':'web','task_kind':'body'},
                {'id':'pdf','url':'https://example.com/a.pdf','track':'document','task_kind':'body'}]}))
            names=['moli','lightpanda','obscura','chromium','kitesurf']
            original_sources = dict(SOURCES)
            claims=task_capabilities(out,names,[{'provider_metadata':{'output_contract':'readable_document_text'}}])
            self.assertEqual(SOURCES, original_sources)
            self.assertEqual(claims['moli']['document']['status'],'有限支持')
            self.assertIn('直接 PDF/DOCX 未提供可读正文',claims['moli']['document']['detail'])
            for name in ('lightpanda','obscura','kitesurf'):
                self.assertEqual(claims[name]['document']['status'],'原生输出')
            self.assertEqual(claims['chromium']['document']['status'],'共同转换')
            for name in ('moli','lightpanda','obscura','kitesurf'):
                self.assertIn('网页内嵌媒体与正文统一走原生 Markdown',claims[name]['notes'])
            self.assertIn('固定标准包',claims['chromium']['notes'])
            for name in names:
                self.assertIn('不使用外部',claims[name]['document']['detail'])
                self.assertNotIn('没有 PDF / Office 解析器',claims[name]['document']['detail'])
            self.assertIn('官方 JS 实现',claims['obscura']['scope'])
            self.assertIn('原生 Markdown',claims['moli']['scope'])
            self.assertIn('browser=kitesurf',claims['kitesurf']['scope'])
            self.assertEqual(CAPABILITIES['lightpanda']['document']['status'],'未声明')
            self.assertIsNone(task_capabilities(out,['firecrawl'],[{'provider_metadata':{'output_contract':'markdown'}}]))

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
        self.assertTrue(set(PROFILES) <= set(CAPABILITIES))
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
        from harness.web_fetch.methods import report_method_catalog
        for source in report_method_catalog()['sources'].values():
            self.assertRegex(source['checked_at'], r'^\d{4}-\d{2}-\d{2}$')
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
        self.assertIn('统一使用 Moli 原生 Markdown', CAPABILITIES['moli-webfetch']['tested'])
        self.assertIn('网页媒体只核对同一 Markdown 输出', CAPABILITIES['moli-webfetch']['tested'])
        self.assertIn('selector', CAPABILITIES['moli-webfetch']['tested'])

    def test_moli_document_capabilities_never_credit_external_parsing(self):
        for name in ('moli-1.0.6', 'moli', 'moli-1.1.3', 'moli-1.1.11'):
            from harness.web_fetch.methods import report_method
            row = report_method(name, {"provider_metadata": {"engine": "moli"}})["capabilities"]
            self.assertIn('原生 Markdown 接口直接交付', row['notes'])
            self.assertIn('不再外部解析', row['notes'])
            self.assertNotIn('评测在同一次预算内解析', row['notes'])

    def test_official_task_routes_are_not_collapsed_into_webpage_markdown(self):
        cloudflare = CAPABILITIES['cloudflare-markdown']
        for route in ('/markdown', '/scrape', '/ai/tomarkdown'):
            self.assertIn(route, cloudflare['scope'])
            self.assertIn(route, cloudflare['tested'])
        self.assertEqual(cloudflare['document']['status'], '需配置')
        self.assertEqual(cloudflare['image']['status'], '仅链接')
        self.assertEqual(cloudflare['av']['status'], '仅链接')
        self.assertEqual(cloudflare['file']['status'], '无此能力')
        self.assertIn('客户端下载不是 Cloudflare 独立成绩', cloudflare['notes'])
        self.assertEqual(set(cloudflare['sources']),
                         {'cf', 'cf-scrape', 'cf-files', 'cf-formats'})
        self.assertIn('video', CAPABILITIES['firecrawl']['tested'])
        self.assertIn('fire-api', CAPABILITIES['firecrawl']['sources'])
        self.assertIn('video', SPECS['firecrawl']['request']['video_resource']['formats'])
        self.assertIn('/ai/tomarkdown', SPECS['cloudflare-markdown']['request']['direct_pdf_or_docx']['endpoint'])

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


def test_source_version_mismatch_is_visible_and_exported(tmp_path):
    from harness.web_fetch.report_i18n import localize_html
    (tmp_path/'adoption').mkdir()
    record = {'provider': 'moli-webfetch', 'provider_metadata': {'reported_version': 'moli 9.9.9'}}
    html = render_capabilities(tmp_path, ['moli-webfetch'], table=table, cell=cell, link=link,
                               records=[record])
    exported = json.loads((tmp_path/'adoption/capabilities.json').read_text())
    checks = exported['source_version_checks']['moli-webfetch']
    assert checks
    assert all(row['status'] == 'mismatch' for row in checks)
    assert all(row['checked_version'] == '1.1.4' for row in checks)
    assert '核对版本 1.1.4；实测版本 9.9.9' in html
    from pathlib import PurePosixPath
    english = localize_html(html, PurePosixPath('adoption/index.html'))
    assert 'checked version 1.1.4; measured version 9.9.9' in english
    assert '/blob/v1.1.4/' in english  # Preserve the actual source binding.


def test_source_version_controls_preserve_unknown_and_mixed_versions():
    from harness.web_fetch.report_capabilities import source_version_checks
    def statuses(versions):
        records = [{'provider': 'moli-webfetch', 'provider_metadata': {
            'reported_version': version}} for version in versions]
        return {row['status'] for row in source_version_checks(['moli-webfetch'], records)['moli-webfetch']}
    assert statuses(['MOLI 1.1.4']) == {'matched'}
    assert statuses(['moli 1.1.4', 'moli 9.9.9']) == {'mismatch'}
    assert statuses(['moli 1.1.4-build.1']) == {'mismatch'}
    assert statuses(['moli 1.1.4', None]) == {'unverified'}
    assert statuses([]) == {'unverified'}


def test_native_capabilities_retain_version_bound_sources(tmp_path):
    (tmp_path/'adoption').mkdir()
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [
        {'id': 'web', 'track': 'web', 'url': 'https://example.test/'}]}))
    records = [{'provider': 'moli', 'case_id': 'web', 'provider_metadata': {
        'reported_version': 'moli 9.9.9', 'output_contract': 'markdown'}}]
    html = render_capabilities(tmp_path, ['moli'], table=table, cell=cell, link=link,
                               records=records)
    assert '核对版本 1.1.5；实测版本 9.9.9' in html
    evidence = json.loads((tmp_path/'adoption/capabilities.json').read_text())
    assert 'headless-moli-1.1.5' in evidence['methods']['moli']['sources']
    assert 'task-native-moli' in evidence['methods']['moli']['sources']


def test_capability_fields_render_english_without_catalog_reverse_lookup(tmp_path, monkeypatch):
    from pathlib import PurePosixPath
    from harness.web_fetch.methods import report_method_catalog
    from harness.web_fetch.report_i18n import localize_html
    (tmp_path/'adoption').mkdir()
    page = render_capabilities(tmp_path, ['moli'], table=table, cell=cell, link=link)
    english = localize_html(page, PurePosixPath('adoption/index.html'))
    from html import unescape
    english = unescape(english)
    registered = report_method_catalog('en')['methods']['moli']['capabilities']
    assert registered['notes'] in english
    assert registered['web']['detail'] in english
    assert registered['tested'] in english
    assert 'Official capability documentation is not a measured result.' in english
    assert '官方能力资料不是实测成绩' not in english


def test_source_notice_distinguishes_missing_version_from_mismatch():
    from harness.web_fetch.report_capabilities import render_source_version_notice, source_version_checks
    def notice(metadata):
        checks = source_version_checks(['moli-webfetch'], [{'provider': 'moli-webfetch', 'provider_metadata': metadata}])
        return render_source_version_notice(checks['moli-webfetch'])
    missing = notice({})
    assert '资料适用版本未确认' in missing
    assert 'Source applicability is unverified' in missing
    mismatch = notice({'version': '9.9.9'})
    assert '资料版本与实测版本不一致' in mismatch
    assert 'Source and measured versions differ' in mismatch


def test_configured_capability_legend_is_fully_localized(tmp_path):
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html, untranslated_english_narrative
    (tmp_path/'adoption').mkdir()
    (tmp_path/'dataset.json').write_text(json.dumps({'cases': [
        {'id': 'web', 'track': 'web', 'url': 'https://example.test/'}]}))
    records = [{'provider': 'moli', 'case_id': 'web', 'provider_metadata': {
        'reported_version': 'moli 9.9.9', 'output_contract': 'markdown'}}]
    page = render_capabilities(tmp_path, ['moli'], table=table, cell=cell, link=link, records=records)
    english = localize_html(page, PurePosixPath('adoption/index.html'))
    assert 'configured interfaces and processing boundaries' in english
    assert not untranslated_english_narrative(english)
