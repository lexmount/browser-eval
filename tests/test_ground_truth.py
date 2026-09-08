import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path
from harness.web_fetch.config import load_dataset
from harness.web_fetch.reference_quality import audit_reference
from harness.web_fetch.metrics import normalize_for_match


class GroundTruthTest(unittest.TestCase):
    def test_current_task_set_and_reference_integrity(self):
        data,cases=load_dataset(Path('evaluation/dataset.json'))
        self.assertEqual(data['id'],'webfetch')
        self.assertEqual(Counter(c.track for c in cases),dict(web=100,dynamic=10,document=10,image=10,video=10,challenge=10))
        self.assertEqual(len({c.id for c in cases}),150)
        for c in data['cases']:
            self.assertTrue(c['task_prompt'],c['id'])
            if c['track'] in {'web','dynamic','document'}:
                if c['source_verification'].get('state') == 'unavailable':
                    self.assertEqual(c['ground_truth_main_content'],'',c['id'])
                    self.assertIsNone(c['source_verification']['reference_sha256'])
                    self.assertTrue(c['required_facts'],c['id'])
                    self.assertNotIn('body_requirements',c)
                    continue
                self.assertTrue(c['ground_truth_main_content'],c['id'])
                self.assertEqual(hashlib.sha256(c['ground_truth_main_content'].encode()).hexdigest(),c['source_verification']['reference_sha256'])
                if c['track']=='dynamic':
                    from harness.web_fetch.render_evidence import validate_render_reference
                    folder=Path('evaluation/sources')/c['id']
                    audit=validate_render_reference(c,(folder/'initial.html.txt').read_bytes(),(folder/'rendered.txt').read_text())
                    self.assertTrue(audit['valid'],audit['errors'])
                else:self.assertEqual(audit_reference(c['ground_truth_main_content'],c['required_facts'],normalize=normalize_for_match),[],c['id'])
            elif c['track']=='challenge':
                self.assertEqual(c['task_kind'],'structure')
                self.assertEqual(c['required_facts'],[])
                self.assertEqual(c['source_benchmark']['minimum_entries'],3)
                self.assertEqual(c['source_benchmark']['verification_scope'],'first_three_name_detail_pairs')
                reference=json.loads((Path('evaluation/list-references')/(c['id']+'.json')).read_text())
                self.assertGreaterEqual(len(reference['items']),3,c['id'])
            elif c['task_kind']=='file':self.assertFalse(c['content_score_applicable'])
            elif c['task_kind']=='resource':self.assertTrue(c['expected_resources'])

    def test_restricted_tasks_retain_existing_evidence(self):
        data,_=load_dataset(Path('evaluation/dataset.json'))
        by={c['id']:c for c in data['cases']}
        for cid,count in [('challenge-04',30),('challenge-06',15)]:
            self.assertEqual(by[cid]['source_verification']['state'],'restricted')
            self.assertEqual(len(json.loads((Path('evaluation/sources')/cid/'items.json').read_text())),count)
            self.assertNotIn('需用户辅助',by[cid]['source_verification']['note'])

    def test_document_references_match_frozen_sources_and_embedded_provenance(self):
        from harness.web_fetch.document_text import extract_document_text
        data,_=load_dataset(Path('evaluation/dataset.json'))
        cases={c['id']:c for c in data['cases']}
        sources=Path('evaluation/sources')
        direct={
            'document-01':'source.pdf','document-02':'source.pdf','document-03':'source.pdf',
            'document-04':'source.pdf','document-05':'source.docx','document-06':'source.pdf',
            'document-09':'source.bin','document-10':'source.pdf',
        }
        for identifier,name in direct.items():
            raw=(sources/identifier/name).read_bytes()
            if identifier=='document-09':
                extracted=raw.decode('utf-8')
            else:
                extracted,_=extract_document_text(raw)
            self.assertEqual(normalize_for_match(extracted),
                             normalize_for_match(cases[identifier]['ground_truth_main_content']),identifier)
        self.assertIn('Table Of Content',cases['document-03']['ground_truth_main_content'])
        self.assertIn('page 3 / 3',cases['document-03']['ground_truth_main_content'])
        for identifier in ('document-07','document-08'):
            evidence=json.loads((sources/identifier/'evidence.json').read_text())
            embedded=evidence['embedded_document']
            reference=cases[identifier]['ground_truth_main_content']
            self.assertEqual(embedded['text_chars'],len(reference),identifier)
            self.assertEqual(embedded['text_sha256'],hashlib.sha256(reference.encode()).hexdigest(),identifier)
        embedded07=json.loads((sources/'document-07'/'evidence.json').read_text())['embedded_document']
        linked=(sources/'document-07'/embedded07['evidence_path']).resolve()
        self.assertEqual(hashlib.sha256(linked.read_bytes()).hexdigest(),embedded07['sha256'])
        embedded08=json.loads((sources/'document-08'/'evidence.json').read_text())['embedded_document']
        self.assertIn('/pdf/sample-3pp.pdf',(sources/'document-08'/'source.html.txt').read_text())

    def test_feishu_help_acceptance_is_content_not_table_markup(self):
        data,_=load_dataset(Path('evaluation/dataset.json'))
        case=next(c for c in data['cases'] if c['id']=='lm-feishu-help')
        self.assertEqual(case['required_patterns'],[])
        reference=normalize_for_match(case['ground_truth_main_content'])
        for text in ['AI 功能','指标名称','指标含义','所有飞书 aily 工作流应用中，使用次数排行前 10 的应用。']:
            self.assertIn(normalize_for_match(text),reference)
