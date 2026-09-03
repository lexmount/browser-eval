import copy
import hashlib
import html
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.report import (
    TRACKS, attempt_row, case_rate, generate, overview_card, ranking, write_support,
)
from harness.web_fetch.report_components import (
    body_reference_protocol, examples, metric_guide, reference_status,
)


def case(identifier='list', kind='structure', track='challenge'):
    return dict(id=identifier, name=identifier, category='test', track=track,
                task_kind=kind, url='https://example.com/'+identifier,
                content_score_applicable=kind=='body',
                ground_truth_main_content='reference' if kind=='body' else '',
                source_benchmark={}, required_facts=[])


def record(c, passed=True, ok=True, score=.8, elapsed=100):
    return dict(provider='fixture', provider_type='fixture', case_id=c['id'],
                case_name=c['name'], category=c['category'], url=c['url'], run=1,
                attempt_id='fixture__'+c['id']+'__r001', started_at='2026-09-03T00:00:00Z',
                elapsed_ms=elapsed, ok=ok, error_type=None, status_code=200,
                artifact_path='artifacts/fixture/'+c['id']+'/run-001.txt',
                metrics=dict(content_pass=passed, list_task_pass=passed,
                             word_f1=score, word_recall=score, word_precision=score,
                             length_pass=True, fact_results=[], pattern_results=[],
                             resource_results=[], confirmed_entries=3,
                             content_sha256=hashlib.sha256(b'output').hexdigest()))


def table_cells(rendered, identifier):
    table = re.search(r'<table\b[^>]*\bid="'+re.escape(identifier)+r'"[^>]*>(.*?)</table>', rendered, re.S)[1]
    body = table.split('<tbody', 1)[1]
    return [[html.unescape(re.sub('<[^>]+>', '', cell)) for cell in re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)]
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', body, re.S)]


class ReportBodyScopeTests(unittest.TestCase):
    def test_structure_metrics_never_leak_into_body_ranking(self):
        cases = {str(i):case(str(i)) for i in range(10)}
        records = [record(c) for c in cases.values()]
        before = copy.deepcopy((cases, records))
        rendered = ranking(records, cases, 'challenge')
        self.assertIn('完整参考覆盖 0/10', rendered)
        row = table_cells(rendered, 'ranking-challenge')[0]
        self.assertEqual(row[2], '100.0%')  # Transport remains measured.
        self.assertEqual(row[3:], ['—'] * 7)  # Four body metrics + three times.
        auxiliary = table_cells(rendered, 'ranking-challenge-auxiliary')[0]
        self.assertEqual(auxiliary[2], '100.0%')
        self.assertEqual(auxiliary[3], '100 ms')
        self.assertIn('列表核验率', rendered)
        self.assertIn('辅助耗时均值', rendered)
        self.assertEqual((cases, records), before)

    def test_future_body_case_in_challenge_is_measured(self):
        body, auxiliary = case('body', 'body'), case('list')
        rendered = ranking([record(body, elapsed=20), record(auxiliary, elapsed=900)],
                           {'body':body, 'list':auxiliary}, 'challenge')
        row = table_cells(rendered, 'ranking-challenge')[0]
        self.assertEqual(row[3:7], ['100.0%', '80.0%', '80.0%', '80.0%'])
        self.assertEqual(row[7], '20 ms')
        self.assertIn('完整参考覆盖 1/2', rendered)
        self.assertEqual(table_cells(rendered, 'ranking-challenge-auxiliary')[0][3], '900 ms')

    def test_zero_pass_can_have_nonzero_f1_and_no_pass_latency(self):
        c=case('body','body','web')
        row=table_cells(ranking([record(c,passed=False)], {'body':c}, 'web'),'ranking-web')[0]
        self.assertEqual(row[3:7], ['0.0%', '80.0%', '80.0%', '80.0%'])
        self.assertEqual(row[7:], ['—']*3)

    def test_request_failure_zero_and_macro_case_average(self):
        first,second=case('a','body'),case('b','body')
        rs=[record(first,score=1),record(first,ok=False,score=1),record(second,score=.2)]
        row=table_cells(ranking(rs, {'a':first,'b':second}, 'challenge'),'ranking-challenge')[0]
        self.assertEqual(row[6], '35.0%')  # mean(mean(1,0), .2), not pooled .4

    def test_full_reference_coverage_excludes_structure_and_inapplicable_body(self):
        a,b=case('a'),case('b','body')
        a['ground_truth_main_content']='not an eligible body reference'
        b['content_score_applicable']=False
        rendered=ranking([record(a),record(b)],{'a':a,'b':b},'challenge')
        self.assertIn('完整参考覆盖 0/2',rendered)
        self.assertIn('0 例完整参考',reference_status([a,b]))

    def test_home_card_never_uses_list_rate(self):
        c=case(); rendered=overview_card([record(c)],{'list':c},'challenge',TRACKS['challenge'],['fixture'])
        self.assertIn('正文未评测',rendered)
        self.assertIn('最高正文通过率 <b>—</b>',rendered)
        self.assertNotIn('100.0%',rendered)
        c=case('body','body')
        self.assertIn('100.0%',overview_card([record(c)],{'body':c},'challenge',TRACKS['challenge'],['fixture']))

    def test_case_and_attempt_labels_show_auxiliary_scope(self):
        c=case();r=record(c)
        self.assertIn('正文 —（未评测）；列表辅助核验率 100.0%',case_rate([r],c))
        attrs,rendered=attempt_row(r,c)
        self.assertIn('data-content="na"',attrs)
        self.assertIn('列表辅助：通过；正文未评测',rendered)

    def test_example_order_and_metric_explanations(self):
        rendered=examples()
        positions=[rendered.index('data-tab="'+t+'"') for t in ['web','dynamic','challenge','document','image','video']]
        self.assertEqual(positions,sorted(positions))
        guide=metric_guide()
        for phrase in ['0 次通过不等于 F1 为 0','请求失败计 0','仍按实际正文评分','不是平均完整度与平均纯净度的调和平均']:
            self.assertIn(phrase,guide)

    def test_new_capture_protocol_does_not_certify_existing_calls(self):
        rendered=body_reference_protocol()
        for phrase in ['调用前 30 分钟内','调用结束后 30 分钟内','reference SHA-256','scope','rules',
                       '缺失、过期或内容漂移','暂停正文评分','不将参考问题记为 provider 失败',
                       '不表示已有 1,800 次调用全部满足','只是操作窗口','不能证明中间未发生变化']:
            self.assertIn(phrase,rendered)

    def test_rendered_pages_preserve_auxiliary_and_show_body_unmeasured(self):
        c=case();r=record(c)
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory);artifact=out/r['artifact_path'];artifact.parent.mkdir(parents=True);artifact.write_text('output')
            with patch('harness.web_fetch.report_explainer.render_explainer',return_value='article'), patch('harness.web_fetch.report.write_support'):
                generate(out,{'cases':[c]},[r],[])
            detail=(out/'attempts'/f"{r['attempt_id']}.html").read_text()
            for phrase in ['列表辅助结果','正文未评测','辅助耗时','— / — / —','列表辅助目标（非完整正文参考）']:
                self.assertIn(phrase,detail)
            self.assertIn('列表辅助核验率 100.0%',(out/'datasets/index.html').read_text())
            self.assertIn('正文未评测',(out/'datasets/list.html').read_text())
            with patch('harness.web_fetch.report_methods.render_methods',return_value='methods'):
                write_support(out,[r],[])
            self.assertIn('body-reference-protocol',(out/'methodology/index.html').read_text())

    def test_current_canonical_challenge_has_zero_body_coverage(self):
        root=Path(__file__).resolve().parents[1]
        cases={c['id']:c for c in json.loads((root/'evaluation/dataset.json').read_text())['cases']}
        records=[json.loads(s) for s in (root/'evaluation/results.jsonl').read_text().splitlines()]
        rendered=ranking(records,cases,'challenge')
        self.assertIn('完整参考覆盖 0/10',rendered)
        rows=table_cells(rendered,'ranking-challenge')
        self.assertEqual(len(rows),12)
        self.assertTrue(all(row[3:]==['—']*7 for row in rows))


if __name__=='__main__':
    unittest.main()
