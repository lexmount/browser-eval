"""Equivalent link encodings and Markdown escapes retain card ownership."""
import unittest
from lexbench_webfetch.composite_tasks import evaluate_composite_acceptance
from lexbench_webfetch.dynamic_lists import extract_named_entries


class LinkLayoutNormalizationTests(unittest.TestCase):
    def test_escaped_list_punctuation_is_not_recommendation_prose(self):
        contract = {'hosts':['example.test'], 'entry_path':r'/article/[0-9]+'}
        for prefix in ('2026-10-08 - ', r'2026-10-08 \- ', r'\* '):
            with self.subTest(prefix=prefix):
                rows=extract_named_entries(prefix+'[Actual article](/article/1)',contract,'https://example.test/')
                self.assertEqual(len(rows),1)
        for prefix in ('Read ', r'We recommend \- ', r'2026-10-08 Read \- '):
            self.assertEqual(extract_named_entries(prefix+'[Actual article](/article/1)',
                contract,'https://example.test/'),[])

    def test_unreserved_path_encoding_retains_same_record_fields(self):
        contract={'version':1,'groups':[{'id':'offers','kind':'named_link','minimum_records':1,
            'hosts':['example.test'],'entry_path':r'/item/[0-9]+_detail',
            'required_fields':[{'id':'price','kind':'price'}]}]}
        def score(text):return evaluate_composite_acceptance(text,contract,base_url='https://example.test/')
        for path in ('/item/12_detail','/item/12%5Fdetail','/item/12%5fdetail'):
            self.assertTrue(score(f'[Actual offer]({path})\n\n$120')['composite_acceptance_pass'])
        for text in ('[Actual offer](/item/12%5Fdetail)',
                     '[Actual offer](/item/12%2F_detail)\n\n$120',
                     '[Actual offer](/item/12%5Fdetail)\n\n[Other offer](/item/13_detail)\n\n$120'):
            # The final fixture needs both cards, so the second cannot repair the first.
            contract['groups'][0]['minimum_records']=2 if 'Other offer' in text else 1
            self.assertFalse(score(text)['composite_acceptance_pass'])
