import base64
import hashlib
import json
import re
import statistics
from pathlib import Path
import shutil
import tempfile
import unittest

from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.report import cell, link, table, succeeded, measured
from harness.web_fetch.report_explainer import HUB, _bound_initial_audit, _dynamic_section, collect_evidence, render_explainer
from harness.web_fetch.report_style import STYLE
from scripts.probe_cowagent_text import probe
from harness.web_fetch.storage import current_evidence_dir

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = current_evidence_dir()


@unittest.skipUnless((EVIDENCE/'results.jsonl').is_file(), 'requires persisted benchmark evidence')
class ExplainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c['id']: c for c in json.loads((EVIDENCE/'dataset.json').read_text())['cases']}
        cls.records = [json.loads(line) for line in (EVIDENCE/'results.jsonl').read_text().splitlines()]

    def render(self, records, *, figures=False):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            if figures:
                shutil.copytree(ROOT/'harness/web_fetch/assets/methodology', out/'assets/methodology')
            for r in records:
                if ((r['provider']=='cowagent-built-in' and r['case_id']=='ecommerce-list')
                        or self.cases[r['case_id']].get('track')=='dynamic'):
                    target = out/r['artifact_path']
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(EVIDENCE/r['artifact_path'],target)
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
            self.assertEqual(stats['post_response_failures'], sum(r['ok'] and measured(r,self.cases[r['case_id']]) and not succeeded(r,self.cases[r['case_id']]) for r in rows))
            self.assertEqual(stats['task_pass'] + stats['request_failures'] + stats['post_response_failures'] + stats['task_unmeasured'], len(rows))
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

    def test_opening_failure_partition_only_counts_website_body(self):
        page = self.render(self.records)
        stats = self.evidence['tracks']['web']['summary']
        figure = page.split('id="failure-figure"', 1)[1].split('</figure>', 1)[0]
        for key in ('task_pass', 'post_response_failures', 'request_failures'):
            self.assertIn(f'{stats[key]:,} / {stats["records"]:,}', figure)
        # Other task families must not alter the website-only analysis.
        rows = json.loads(json.dumps(self.records))
        for row in rows:
            if self.cases[row['case_id']].get('track') == 'challenge':
                row['ok'] = False
        changed = self.render(rows)
        self.assertEqual(figure, changed.split('id="failure-figure"', 1)[1].split('</figure>', 1)[0])

    def test_dynamic_headline_and_case_counts_follow_revised_inputs(self):
        rows = json.loads(json.dumps(self.records))
        for row in rows:
            if self.cases[row['case_id']].get('track') == 'dynamic' and row['provider'] == 'moli-webfetch':
                row['ok'] = False
        page = self.render(rows)
        stats = self.evidence['dynamic']
        dynamic = page.split('id="dynamic-coverage"', 1)[1].split('</section>', 1)[0]
        self.assertIn(f'{stats["records"]} 次调用', dynamic)
        self.assertIn(f'{stats["task_pass"]} 次任务通过', dynamic)
        moli = self.evidence['tracks']['dynamic']['providers']['moli-webfetch']
        lead = re.search(r'<p class="lead">(.*?)</p>', page, re.S).group(1)
        self.assertIn(f'{moli["task_pass"]}/{moli["records"]}', lead)
        for item in stats['cases']:
            outcome = f'{sum(r["task_pass"] for r in item["results"])}/{len(item["results"])}'
            self.assertIn(outcome, dynamic)

    def test_popular_contract_results_are_visible_without_overlap_reference(self):
        page = self.render(self.records)
        popular = page.split('id="limits"', 1)[1].split('</section>', 1)[0]
        stats = self.evidence['tracks']['challenge']
        self.assertNotIn('<details', popular)
        self.assertIn(f'{stats["summary"]["task_pass"]} 次满足', popular)
        self.assertIn(f'{len(stats["union_pass_ids"])}/{stats["case_count"]}', popular)
        self.assertNotIn('均为<strong>未评测', popular)
        self.assertNotIn('body_reference_case_ids', stats)
        self.assertIn('文本', popular)
        self.assertIn('结构', popular)

    def test_zero_pass_positive_f1_example_uses_observed_metrics(self):
        page = self.render(self.records)
        sample = self.evidence['dynamic']['zero_pass_nonzero_f1']
        self.assertIsNotNone(sample)
        provider = self.evidence['tracks']['dynamic']['providers'][sample['provider']]
        self.assertEqual(provider['task_pass'], 0)
        self.assertGreater(sample['metrics']['word_f1'], 0)
        record = next(r for r in self.records if r['attempt_id'] == sample['attempt_id'])
        self.assertEqual(sample['metrics'], record['metrics'])
        dynamic = page.split('id="dynamic-coverage"', 1)[1].split('</section>', 1)[0]
        self.assertIn(sample['attempt_id'], dynamic)
        self.assertIn(f'{sample["metrics"]["word_f1"]:.1%}', dynamic)
        # If no positive-overlap failures remain, do not fabricate the example.
        rows = json.loads(json.dumps(self.records))
        for row in rows:
            if self.cases[row['case_id']].get('track') == 'dynamic':
                row['metrics']['word_f1'] = 0
        self.assertIsNone(collect_evidence(self.cases, rows)['dynamic']['zero_pass_nonzero_f1'])

    def test_formal_scope_explains_stable_contract_not_freshness_blocking(self):
        page = self.render(self.records)
        for label in ('网站正文', '动态网站正文', '热门网站正文'):
            self.assertIn(label, page)
        for phrase in ('历史组', '补充组', '补测', '新加入', '另加',
                       '旧身份规则', '旧快照规则', '以实际为准', '兼容旧', '若某记录仍'):
            self.assertNotIn(phrase, self.export)
        scope = page.split('id="study-scope"', 1)[1].split('</section>', 1)[0]
        for track in self.evidence['tracks'].values():
            if track['time_window']['first_start_utc']:
                self.assertIn(track['time_window']['first_start_utc'][:10], scope)
        self.assertNotIn('list_snapshot_difference', self.evidence)
        for phrase in ('比较就应暂停', '调用后再次独立观察', '列表核验（辅助）'):
            self.assertNotIn(phrase, page)
        for concept in ('数量', '字段', '结构', '事实准确', '数据最新'):
            self.assertIn(concept, page)

    def test_mixed_contract_keeps_content_checks_and_scoped_text_quality(self):
        page = self.render(self.records)
        for concept in ('内容核对点', '栏目', '说明', '参考范围', '局部参考'):
            self.assertIn(concept, page)
        self.assertIn('content and structure', self.evidence['tracks']['challenge']['score_scope'])
        template = (ROOT/'harness/web_fetch/templates/interpretation.html').read_text()
        for slot in ('annotation_figure', 'validation_figure', 'scoring_figure'):
            self.assertEqual(template.count('${' + slot + '}'), 1)
            self.assertNotIn('${' + slot + '}', self.export)

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
        self.assertIn('6 种已运行方法',figure)
        self.assertEqual(figure.count('<th scope="col">'), 4)  # Number, task, mechanism, result.
        self.assertEqual(figure.count('<tr>'),len(base)+1)

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

    def test_delayed_outcome_is_derived_not_a_fixed_failure_claim(self):
        data = collect_evidence(self.cases,self.records)
        delayed = next(x for x in data['dynamic']['cases'] if x['case_id']=='dynamic-delayed')
        for row in delayed['results']:
            row['task_pass']=True
        count=len(delayed['results'])
        self.assertIn(f'{count} 次调用中，{count} 次满足完整内容规则',_dynamic_section(data,self.cases))

    def test_standalone_has_identical_article_inline_style_no_dependencies(self):
        page = self.render(self.records)
        article = lambda html: re.search(r'<article\b.*?</article>',html,re.S).group()
        self.assertEqual(article(page),article(self.export))
        self.assertIn(f'<style>{STYLE}</style>',self.export)
        self.assertNotRegex(self.export,r'<(?:script|link|iframe)\b')
        for img in re.findall(r'<img\b[^>]*>', self.export):
            self.assertRegex(img, r'src="data:image/[^\"]+"')
            self.assertRegex(img, r'alt="[^\"]+"')
        self.assertIn('@media print',self.export)
        self.assertIn('@media(max-width:600px)',self.export)
        self.assertIn('href="article.html" download=',page)
        self.assertNotIn('href="article.html"',self.export)

    def test_three_real_figures_use_assets_online_and_identical_embedded_bytes_offline(self):
        page = self.render(self.records, figures=True)
        images = re.findall(r'<img\b[^>]*>', page)
        embedded = re.findall(r'<img\b[^>]*>', self.export)
        self.assertEqual(len(images), 3)
        self.assertEqual(len(embedded), 3)
        for name in ('annotation', 'validation', 'scoring'):
            asset = f'../assets/methodology/{name}.png'
            original = (ROOT/'harness/web_fetch/assets/methodology'/f'{name}.png').read_bytes()
            uri = 'data:image/png;base64,' + base64.b64encode(original).decode('ascii')
            self.assertIn(f'src="{asset}"', page)
            self.assertIn(f'href="{asset}" download="{name}.png"', page)
            self.assertNotIn(asset, self.export)
            self.assertIn(f'src="{uri}"', self.export)
            self.assertIn(f'href="{uri}" download="{name}.png"', self.export)
            info = next(row for row in self.evidence['methodology_figures'] if row['path'].endswith(f'/{name}.png'))
            self.assertTrue(info['available'])
            self.assertEqual(info['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(info['bytes'], len(original))
            # Normalize only serialization differences; all authored text stays identical.
            self.export = self.export.replace(uri, asset)
        article = lambda html: re.search(r'<article\b.*?</article>', html, re.S).group()
        self.assertEqual(article(page), article(self.export))
        for img in images + embedded:
            self.assertRegex(img, r'alt="[^\"]+"')
            self.assertIn('max-width:100%;height:auto', img)
        self.assertIn('.editorial-figure,.editorial-table tr{break-inside:avoid}', STYLE)
        self.assertEqual(len(re.findall(r'<figure id="methodology-[^\"]+"', page)), 3)
        for figure in re.findall(r'<figure id="methodology-[^\"]+".*?</figure>', page, re.S):
            self.assertIn('<figcaption>', figure)
            self.assertNotRegex(figure, r'<a\b[^>]*>\s*<img')
            self.assertIn('position:absolute;inset:0', figure)

    def test_missing_figures_are_reported_without_broken_image_placeholders(self):
        page = self.render(self.records)
        self.assertEqual(len(self.evidence['methodology_figures']), 3)
        self.assertFalse(any(row['available'] for row in self.evidence['methodology_figures']))
        self.assertNotIn('<img', page)

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
        path = EVIDENCE/r['artifact_path'];before = path.read_bytes()
        kwargs = {'ok': r['ok'], 'status_code': r.get('status_code'), 'metadata': r.get('provider_metadata', {})}
        original = score_attempt(c, before, **kwargs)
        changed = score_attempt(c, before.decode().replace('51.77','999.99').encode(), **kwargs)
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
