import json
from pathlib import Path
import shutil
import tempfile
import unittest

from harness.web_fetch.metrics import evaluate_content
from harness.web_fetch.report import cell, link, table, succeeded
from harness.web_fetch.report_explainer import render_explainer
from scripts.probe_cowagent_text import probe

ROOT = Path(__file__).resolve().parents[1]


class ExplainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c['id']: c for c in json.loads((ROOT/'evaluation/dataset.json').read_text())['cases']}
        cls.records = [json.loads(line) for line in (ROOT/'evaluation/results.jsonl').read_text().splitlines()]

    def render(self, records):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            for r in records:
                if r['provider']=='cowagent-built-in' and r['case_id']=='ecommerce-list':
                    target = out/r['artifact_path']
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(ROOT/'evaluation'/r['artifact_path'],target)
            return render_explainer(out,self.cases,records,table=table,cell=cell,link=link)

    def test_current_numbers_and_boundaries_are_explicit(self):
        page = self.render(self.records)
        cow = [r for r in self.records if r['provider']=='cowagent-built-in' and self.cases[r['case_id']].get('track','web')=='web']
        self.assertIn(f'{sum(r["ok"] for r in cow)} 次请求成功、{sum(succeeded(r,self.cases[r["case_id"]]) for r in cow)} 次任务通过',page)
        for text in ['没有 F1 最低分门槛','词频重合','没有接通统一的原文件字节验收链路','不等于捏造','历史 CowAgent 记录没有保存逐次源码指纹']:
            self.assertIn(text,page)

    def test_numbers_follow_records_not_an_editorial_constant(self):
        rows = json.loads(json.dumps(self.records))
        candidate = next(r for r in rows if r['provider']=='cowagent-built-in' and r['case_id']=='static-simple')
        candidate['ok'] = False
        page = self.render(rows)
        cow = [r for r in rows if r['provider']=='cowagent-built-in' and self.cases[r['case_id']].get('track','web')=='web']
        self.assertIn(f'{sum(r["ok"] for r in cow)} 次请求成功、{sum(succeeded(r,self.cases[r["case_id"]]) for r in cow)} 次任务通过',page)

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
