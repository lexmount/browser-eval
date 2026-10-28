"""Markdown format equivalence and nearby invalid inputs through public scoring."""
import unittest
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def contract(schema,count=2,**extra):
    return {"version":1,"schema":schema,"minimum_records":count,"maximum_records":count,**extra}


class WebFetchFormatNormalizationTests(unittest.TestCase):
    def test_inline_breaks_preserve_markdown_table_cells_not_missing_fields(self):
        case={'url':'https://example.test/table','task_kind':'body','acceptance_contract':{
            'version':1,'schema':'table_row','minimum_records':1,
            'table_columns':[{'name':'name','label':'Name','type':'text'},
                             {'name':'age','label':'Age','type':'integer'}],
            'record_identity_fields':['name'],'table_scope':{
                'row_selector':'tbody tr','cell_selector':'td','start_pattern':r'(?m)^(?=\|\s*Name\s*\|)',
                'end_pattern':r'(?m)^End$','plain_row_pattern':r'(?P<name>[A-Za-z]+)\s+(?P<age>\d+)'}}}
        for br in ('<br>','<br/>','<BR />'):
            text=f'| Name{br}{br} | Age{br} |\n| --- | --- |\n| Alice{br} | 25{br} |\nEnd'
            self.assertTrue(task_passed(True,score_attempt(case,text.encode(),ok=True)))
            for invalid in (text.replace('25'+br,br),text.replace('25'+br,'2'+br+'5')):
                self.assertFalse(task_passed(True,score_attempt(case,invalid.encode(),ok=True)))

    def test_named_scope_matches_markdown_headings_and_excludes_other_sections(self):
        case={'url':'https://example.test/catalog','task_kind':'body',
              'acceptance_contract':contract('named_link',1,hosts=['example.test'],entry_path=r'/item/\d+',
                  entry_scope={'html_selector':'main','start_pattern':r'^News\s*$',
                               'end_pattern':r'^Related items\s*$'})}
        for start,end in (('News','Related items'),('## News','## [Related **items**](/archive)'),
                          ('## **News**','## Related items')):
            text=f'[Outside before](/item/1)\n{start}\n[Actual article](/item/2)\n{end}\n[Outside after](/item/3)'
            measured=score_attempt(case,text.encode(),ok=True)
            self.assertTrue(task_passed(True,measured),measured)
            self.assertEqual(measured['stable_unique_record_count'],1)
            for invalid in (text.replace('[Actual article](/item/2)',''),
                            text.replace(start,'Discuss News in this sentence')):
                self.assertFalse(task_passed(True,score_attempt(case,invalid.encode(),ok=True)))

    def test_emphasized_rank_is_a_list_entry_but_recommendation_prose_is_not(self):
        case={'url':'https://example.test/catalog','task_kind':'body',
              'acceptance_contract':contract('named_link',1,hosts=['example.test'],entry_path=r'/item/\d+')}
        for rank in ('_10_','__10__','*10*','**10**'):
            for suffix in ('',' [Journal](/journal/1)'):
                with self.subTest(rank=rank,suffix=suffix):
                    measured=score_attempt(case,('* '+rank+' [A new paper](/item/10)'+suffix).encode(),ok=True)
                    self.assertTrue(task_passed(True,measured),measured)
        for text in ('* _Recommended_ [A new paper](/item/10)',
                     'Read _10_ [A new paper](/item/10)',
                     '* _10_ [![Paper cover](/cover.png)](/item/10)',
                     '* _10 [A new paper](/item/10)'):
            with self.subTest(text=text):
                self.assertFalse(task_passed(True,score_attempt(case,text.encode(),ok=True)))
