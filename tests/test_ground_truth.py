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
