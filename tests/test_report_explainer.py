import json
import re
import statistics
from pathlib import Path
import shutil
import tempfile
import unittest

from harness.web_fetch.metrics import evaluate_content
from harness.web_fetch.failure_diagnosis import normalize_empty_response
from harness.web_fetch.report import cell, link, table, succeeded
from harness.web_fetch.report_explainer import HUB, _bound_initial_audit, _dynamic_section, collect_evidence, render_explainer
from harness.web_fetch.report_style import STYLE
from scripts.probe_cowagent_text import probe

ROOT = Path(__file__).resolve().parents[1]


class ExplainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c['id']: c for c in json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']}
        cls.records = [json.loads(line) for line in (ROOT/'evaluation/results.jsonl').read_text().splitlines()]
        for record in cls.records:
            normalize_empty_response(record, (ROOT/'evaluation'/record['artifact_path']).read_text())

    def render(self, records):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            for r in records:
                if ((r['provider']=='cowagent-built-in' and r['case_id']=='ecommerce-list')
                        or self.cases[r['case_id']].get('track')=='dynamic'):
                    target = out/r['artifact_path']
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(ROOT/'evaluation'/r['artifact_path'],target)
            page = render_explainer(out,self.cases,records,table=table,cell=cell,link=link)
            self.export = (out/'guide/article.html').read_text()
            self.evidence = json.loads((out/'guide/interpretation-evidence.json').read_text())
            return page

    def test_current_counts_are_derived_per_cohort(self):
        page = self.render(self.records)
        for key, rows in [('totals', self.records), ('historical', [r for r in self.records
                if self.cases[r['case_id']].get('track') != 'dynamic']),
                ('dynamic', [r for r in self.records if self.cases[r['case_id']].get('track') == 'dynamic'])]:
            stats = self.evidence[key]
            self.assertEqual(stats['records'], len(rows))
            self.assertEqual(stats['task_pass'], sum(succeeded(r,self.cases[r['case_id']]) for r in rows))
            self.assertEqual(stats['post_response_failures'], sum(r['ok'] and not succeeded(r,self.cases[r['case_id']]) for r in rows))
            self.assertEqual(stats['task_pass'] + stats['request_failures'] + stats['post_response_failures'], len(rows))
        for anchor in ('rules', 'limits', 'dynamic-coverage', 'quality', 'evidence'):
            self.assertIn(f'id="{anchor}"', page)
        self.assertEqual(len(re.findall(r'<article\b', page)), 1)

    def test_numbers_follow_records_not_an_editorial_constant(self):
        rows = json.loads(json.dumps(self.records))
        candidate = next(r for r in rows if r['provider']=='cowagent-built-in' and r['case_id']=='static-simple')
        candidate['ok'] = False
        before = collect_evidence(self.cases, self.records)
        self.render(rows)
        self.assertEqual(self.evidence['historical']['task_pass'], before['historical']['task_pass'] - 1)
        self.assertNotEqual(self.evidence['input']['records_sha256'], before['input']['records_sha256'])
        self.assertNotIn('static-simple', self.evidence['cow_moli']['both_pass_ids'])

    def test_pair_partitions_and_matched_latency(self):
        data = collect_evidence(self.cases, self.records)
        pair = data['cow_moli']
        groups = [set(pair[k]) for k in ('both_pass_ids','cow_only_ids','moli_only_ids','neither_ids')]
        self.assertEqual(sum(map(len, groups)), len(set.union(*groups)))
        self.assertEqual(set.union(*groups), set(pair['matched_case_ids']))
        self.assertEqual(set(pair['union_pass_ids']), set.union(*groups[:3]))
        for provider, key in [('cowagent-built-in','cow_common_median_ms'), ('moli-webfetch','moli_common_median_ms')]:
            latencies = [r['elapsed_ms'] for r in self.records if r['provider']==provider
                         and r['case_id'] in pair['timed_common_case_ids']]
            self.assertEqual(pair[key], statistics.median(latencies))

    def test_f1_is_mean_of_record_f1_not_a_pass_filter(self):
        data = collect_evidence(self.cases,self.records)
        for provider, summary in data['tracks']['web']['providers'].items():
            full = [r['metrics']['word_f1'] for r in self.records if r['provider']==provider
                    and self.cases[r['case_id']].get('track','web')=='web'
                    and r['metrics'].get('word_f1') is not None]
            self.assertEqual(summary['f1_records'],len(full))
            self.assertEqual(summary['mean_f1'],statistics.mean(full) if full else None)

    def test_partial_dynamic_cohort_does_not_fill_missing_providers_with_failures(self):
        dynamic_ids = {k for k,c in self.cases.items() if c.get('track')=='dynamic'}
        self.assertTrue(dynamic_ids)
        rows = [r for r in self.records if r['case_id'] not in dynamic_ids or r['provider']=='moli-webfetch']
        data = collect_evidence(self.cases,rows)
        self.assertEqual(set(data['tracks']['dynamic']['providers']), {'moli-webfetch'})
        self.assertEqual(data['historical'], collect_evidence(self.cases,self.records)['historical'])
        page = self.render(rows)
        dynamic = page.split('id="dynamic-coverage"',1)[1].split('</section>',1)[0]
        self.assertEqual(dynamic.count('<thead>'),1)
        self.assertNotIn('>Crawl4AI</a>',dynamic)

    def test_dynamic_figure_accepts_six_methods_without_changing_historical_cohort(self):
        historical = [r for r in self.records if self.cases[r['case_id']].get('track') != 'dynamic']
        base = [r for r in self.records if r['provider']=='moli-webfetch'
                and self.cases[r['case_id']].get('track')=='dynamic']
        supplement = [dict(r, provider=f'fixture-method-{i}', attempt_id=f'fixture-{i}-{r["case_id"]}')
                      for i in range(6) for r in base]
        data = collect_evidence(self.cases,historical+supplement)
        self.assertEqual(len(data['tracks']['dynamic']['providers']),6)
        self.assertEqual(data['historical'],collect_evidence(self.cases,historical)['historical'])
        figure = _dynamic_section(data,self.cases)
        for i in range(6):
            self.assertIn(f'>fixture-method-{i}</a>',figure)
        self.assertEqual(figure.count('<th scope="col">'), 6)  # Number, method, four tasks.

    def test_article_keeps_argument_evidence_without_reprinting_all_rankings(self):
        page = self.render(self.records)
        quality = page.split('id="quality"',1)[1].split('</section>',1)[0]
        self.assertEqual(quality.count('<tbody>'),1)
        self.assertEqual(quality.count('<tr>'),4)  # Header plus three relevant methods.
        media = page.split('id="other-tasks"',1)[1].split('</section>',1)[0]
        self.assertNotIn('<table>',media)
        for case_id in ('ecommerce-list','image-03'):
            record = next(r for r in self.records if r['provider']=='cowagent-built-in' and r['case_id']==case_id)
            self.assertIn(record['attempt_id'],media)
        for key in ('price_counterfactual','probe','cow_moli','initial_reference_audit'):
            self.assertIsNotNone(self.evidence[key])
        self.assertEqual(set(self.evidence['tracks']['web']['providers']),
                         {r['provider'] for r in self.records if self.cases[r['case_id']].get('track','web')=='web'})

    def test_standalone_has_identical_article_inline_style_no_dependencies(self):
        page = self.render(self.records)
        article = lambda html: re.search(r'<article\b.*?</article>',html,re.S).group()
        self.assertEqual(article(page),article(self.export))
        self.assertIn(f'<style>{STYLE}</style>',self.export)
        self.assertNotRegex(self.export,r'<(?:script|link|iframe|img)\b')
        self.assertIn('@media print',self.export)
        self.assertIn('@media(max-width:600px)',self.export)
        self.assertIn('href="article.html" download=',page)
        self.assertNotIn('href="article.html"',self.export)

    def test_public_links_are_html_hub_routes_and_downloads_are_local(self):
        page = self.render(self.records)
        from urllib.parse import parse_qs, urlsplit
        for href in re.findall(r'href="([^"]+)"',self.export):
            self.assertTrue(href.startswith(HUB+'#page='),href)
            route = parse_qs(urlsplit(href.replace('&amp;','&')).fragment)['page'][0]
            self.assertTrue(route.endswith('.html'),href)
        for suffix in ('interpretation-evidence.json','../results.jsonl','cowagent-text-probe.json'):
            self.assertIn(f'href="{suffix}" download',page)
        for forbidden in ('10.3.150.202','/private/tmp','写作结构参考','Browser Use','humanizer','editorial/review'):
            self.assertNotIn(forbidden,self.export)

    def test_tables_have_caption_and_numbered_first_column(self):
        page = self.render(self.records)
        for fragment in re.findall(r'<table>.*?</table>',page,re.S):
            self.assertIn('<caption>',fragment)
            self.assertIn('<thead><tr><th scope="col">序号</th>',fragment)

    def test_initial_audit_is_input_bound_and_has_no_private_paths(self):
        audit = _bound_initial_audit(self.cases)
        self.assertIsNotNone(audit)
        self.assertEqual(audit['exact_reconstructions'],sum(r['exact_reconstruction'] is True for r in audit['cases']))
        self.assertNotIn('/private/',json.dumps(audit))
        changed = json.loads(json.dumps(self.cases))
        changed['static-simple']['ground_truth_main_content'] += ' altered'
        self.assertIsNone(_bound_initial_audit(changed))

    def test_dataset_text_is_escaped_and_unsafe_ids_rejected(self):
        cases = json.loads(json.dumps(self.cases))
        low = collect_evidence(cases,self.records)['lowest_f1_passing_cow']
        cases[low['case_id']]['name'] = '<script>alert(1)</script>'
        original = self.cases
        try:
            self.cases = cases
            page = self.render(self.records)
            self.assertIn('&lt;script&gt;',page)
            self.assertNotIn('<script>',page)
        finally:
            self.cases = original
        row = dict(self.records[0],attempt_id='../escape')
        with self.assertRaises(ValueError):
            collect_evidence(cases,[row])

    def test_actual_price_counterfactual_does_not_touch_evidence(self):
        c = self.cases['ecommerce-list']
        r = next(r for r in self.records if r['case_id']==c['id'] and r['provider']=='cowagent-built-in')
        path = ROOT/'evaluation'/r['artifact_path'];before = path.read_bytes()
        keys = ['required_facts','forbidden_any','min_content_chars','fact_coverage_threshold','ground_truth_main_content','required_patterns']
        kwargs = {k:c[k] for k in keys if k in c}
        original = evaluate_content(before.decode(),c['expected_any'],**kwargs)
        changed = evaluate_content(before.decode().replace('51.77','999.99'),c['expected_any'],**kwargs)
        self.assertTrue(original['content_pass'])
        self.assertFalse(changed['content_pass'])
        self.assertTrue(changed['length_pass'])
        self.assertFalse(next(f for f in changed['fact_results'] if f['id']=='first-price')['pass'])
        self.assertEqual(path.read_bytes(),before)

    def test_offline_extractor_probe_is_reproducible(self):
        saved = json.loads((ROOT/'research/cowagent-text-probe.json').read_text())
        source = 'class WebFetch:\n'+''.join('    '+line+'\n' for line in saved['extractor_source'].splitlines())
        result = probe(source)
        self.assertEqual(result['output'],saved['output'])
        self.assertEqual(result['observations'],saved['observations'])
        self.assertTrue(result['observations']['navigation_preserved'])
        self.assertFalse(result['observations']['detail_url_preserved'])

    def test_works_without_featured_provider(self):
        page=self.render([r for r in self.records if r['provider']=='moli-webfetch'])
        self.assertIn('任务通过',page)
        self.assertNotIn('97 次任务通过',page)
