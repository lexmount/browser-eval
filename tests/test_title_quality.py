import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.title_quality import add_title_quality, evaluate_titles, reference_title
from harness.web_fetch.report import ranking
from tests.test_report_body_scope import case, record, table_cells


def item(title, url='https://movie.douban.com/subject/1/'):
    return {'title': title, 'url': url}


def output(title, url='https://movie.douban.com/subject/1/'):
    return {'fields': item(title, url)}


class TitleQualityTests(unittest.TestCase):
    def test_partial_title_and_extra_words(self):
        ref={'items': [item('red green blue')]}
        q=evaluate_titles([output('red green unwanted')], ref)
        self.assertAlmostEqual(q['precision'], 2/3, places=3)
        self.assertAlmostEqual(q['recall'], 2/3, places=3)
        self.assertAlmostEqual(q['f1'], 2/3, places=3)

    def test_order_and_membership_are_not_expected_answers(self):
        ref={'items': [item('A film'), item('Another film', 'https://movie.douban.com/subject/2')]}
        q=evaluate_titles([output('Another film', 'https://movie.douban.com/subject/2?from=top')],ref)
        self.assertEqual(q['f1'],1)
        self.assertEqual(q['returned_titles'],1)

    def test_unknown_identity_never_gets_text_only_match(self):
        q=evaluate_titles([output('A film', 'https://movie.douban.com/subject/99')], {'items':[item('A film')]})
        self.assertIsNone(q['f1'])
        self.assertEqual(q['scored_titles'],0)
        self.assertEqual(q['returned_titles'],1)

    def test_no_output_is_unmeasured_not_zero_or_perfect(self):
        self.assertIsNone(evaluate_titles([], {'items':[item('A film')]})['f1'])

    def test_aliases_and_unicode_formatting(self):
        q=evaluate_titles([output('红 色\u200b')], {'items':[item('Red'),item('红色')]})
        self.assertEqual(q['f1'],1)

    def test_title_reference_does_not_include_card_votes_or_truncation(self):
        q=evaluate_titles([output('Some title')], {'items':[item('Some title\n23 votes'),item('Some...')]})
        self.assertEqual(q['checks'][0]['reference_titles'],['Some title'])

    def test_duplicate_entity_not_weighted_twice(self):
        q=evaluate_titles([output('red'), output('red','https://movie.douban.com/subject/1?x=1')], {'items':[item('red')]})
        self.assertEqual(q['returned_titles'],1)

    def test_reference_buttons_prices_and_counts_are_not_aliases(self):
        for label in ['查看选项','单击此处查看价格','账户充值','(1.4万)','+ 2 其他颜色/图案','+5','CNY\u00a067.16\nCNY67\n.\n16']:
            self.assertIsNone(reference_title(label),label)
            q=evaluate_titles([output(label)],{'items':[item(label),item('Actual product name')]})
            self.assertEqual(q['f1'],0,label)
        self.assertEqual(reference_title('1917'),'1917')

    def test_mixed_body_and_title_measurements_both_remain_visible(self):
        a,b=case('body','body'),case('list'); ra,rb=record(a),record(b)
        rb['title_quality']=evaluate_titles([output('red')],{'items':[item('red')]})
        rendered=ranking([ra,rb],{'body':a,'list':b},'challenge')
        row=table_cells(rendered,'ranking-challenge')[0]
        self.assertEqual(row[4:7],['80.0%']*3)
        self.assertEqual(table_cells(rendered,'ranking-challenge-titles')[0][2:5],['100.0%']*3)
        self.assertIn('完整参考覆盖 1/2',rendered)

    def test_attachment_keeps_task_and_fulltext_scores(self):
        c=case('list'); c['acceptance_contract']={'schema':'named_link'}
        r=record(c,passed=False); r['metrics']['stable_records']=[output('A film')]
        before=copy.deepcopy(r)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'list-references').mkdir()
            (root/'list-references/list.json').write_text(json.dumps({'case_id':'list','items':[item('A film')]}))
            add_title_quality(r,c,root)
        self.assertEqual(r['metrics'],before['metrics'])
        self.assertEqual(r['title_quality']['mode'],'offline_title_diagnostic')
        self.assertEqual(len(r['title_quality']['scorer_sha256']),64)
        self.assertEqual(r['title_quality']['f1'],1)

    def test_main_table_names_scope_and_preserves_task_failure(self):
        c=case('list'); r=record(c,passed=False)
        r['title_quality']=evaluate_titles([output('red')],{'items':[item('red')]})
        rendered=ranking([r],{'list':c},'challenge')
        self.assertIn('标题完整度',rendered)
        self.assertIn('标题参考覆盖',rendered)
        self.assertIn('不衡量榜单召回率、实时性或整页正文质量',rendered)
        cells=table_cells(rendered,'ranking-challenge')[0]
        self.assertEqual(cells[3],'0.0%')
        self.assertEqual(cells[4:7],['—']*3)
        diagnostic=table_cells(rendered,'ranking-challenge-titles')[0]
        self.assertEqual(diagnostic[2:5],['100.0%']*3)
        self.assertEqual(diagnostic[5],'1/1 · 1/1 例')
        self.assertIn('<summary>标题文字质量 · 计算过程与结果',rendered)

    def test_case_macro_average_not_returned_title_pooled_average(self):
        a,b=case('a'),case('b'); ra,rb=record(a),record(b)
        ra['title_quality']=evaluate_titles([output('red')],{'items':[item('red')]})
        rb['title_quality']=evaluate_titles([output('bad'),output('bad','https://movie.douban.com/subject/2')],
                                          {'items':[item('blue'),item('blue','https://movie.douban.com/subject/2')]})
        row=table_cells(ranking([ra,rb],{'a':a,'b':b},'challenge'),'ranking-challenge-titles')[0]
        self.assertEqual(row[2:5],['50.0%']*3)

    def test_current_public_labels_are_consistent(self):
        from harness.web_fetch.report import TRACKS
        self.assertEqual(TRACKS['dynamic'],'动态网站正文')
        root=Path(__file__).resolve().parents[1]
        obsolete='动态'+'网页正文'
        for directory in ('harness/web_fetch','scripts'):
            for path in (root/directory).rglob('*'):
                if path.suffix in {'.py','.html','.md'}:
                    self.assertNotIn(obsolete,path.read_text(),str(path))
