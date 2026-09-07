import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.result_review import require_result_review
from scripts.revise_body_scoring import rebind_unchanged_result_review


class ResultReviewTests(unittest.TestCase):
    def fixture(self, root):
        dataset={'comparison':{'require_independent_result_review':True},
                 'cases':[{'id':'a','task_kind':'body'}]}
        records=[{'attempt_id':'p__a__r001','case_id':'a','ok':True,
                  'metrics':{'content_pass':True}}]
        (root/'dataset.json').write_text(json.dumps(dataset))
        (root/'results.jsonl').write_text(json.dumps(records[0])+'\n')
        review={'schema_version':1,'bindings':{
            'dataset_sha256':hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest(),
            'results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()},
            'reviewer':{'agent':'fixture'},'review_protocol':{'machine_judgments_withheld':True},
            'attempts':[{'attempt_id':'p__a__r001',
            'machine_verdict':'pass','agent_verdict':'pass','final_verdict':'pass'}]}
        return dataset,records,review

    def test_requires_exact_bound_attempt_complete_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset,records,review=self.fixture(root)
            (root/'result-review.json').write_text(json.dumps(review))
            result=require_result_review(root,dataset,records)
            self.assertEqual(result['reviewed_attempts'],1)
            review['bindings']['results_sha256']='wrong'
            (root/'result-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'different results'):
                require_result_review(root,dataset,records)

    def test_missing_or_unresolved_review_blocks_required_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset,records,review=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'required but missing'):
                require_result_review(root,dataset,records)
            review['attempts'][0]['final_verdict']=None
            (root/'result-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'unresolved'):
                require_result_review(root,dataset,records)

    def test_review_cannot_publish_a_known_scoring_disagreement(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset,records,review=self.fixture(root)
            dataset['cases'][0]['task_kind']='body'
            records[0].update(ok=True,case_id='a',metrics={'content_pass':True})
            (root/'dataset.json').write_text(json.dumps(dataset))
            (root/'results.jsonl').write_text(json.dumps(records[0])+'\n')
            review['bindings']['dataset_sha256']=hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest()
            review['bindings']['results_sha256']=hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()
            review['attempts'][0]['final_verdict']='fail'
            (root/'result-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'correct and re-review'):
                require_result_review(root,dataset,records)

    def test_machine_failure_scope_reviews_only_failures_and_accounts_for_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset,records,review=self.fixture(root)
            failed={'attempt_id':'p__b__r001','case_id':'b','ok':True,
                    'metrics':{'content_pass':False}}
            dataset['cases'].append({'id':'b','task_kind':'body'})
            records.append(failed)
            (root/'dataset.json').write_text(json.dumps(dataset))
            (root/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
            review['bindings']['dataset_sha256']=hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest()
            review['bindings']['results_sha256']=hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()
            review['review_protocol']['scope']='machine-failures'
            review['attempts']=[{'attempt_id':'p__b__r001','machine_verdict':'fail',
                                 'agent_verdict':'fail','final_verdict':'fail'}]
            (root/'result-review.json').write_text(json.dumps(review))
            result=require_result_review(root,dataset,records)
            self.assertEqual(result['reviewed_attempts'],1)
            self.assertEqual(result['total_attempts'],2)
            self.assertEqual(result['accepted_unreviewed_passes'],1)

            review['attempts']=[]
            (root/'result-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'every attempt'):
                require_result_review(root,dataset,records)

    def test_metadata_only_rescore_can_rebind_but_semantic_change_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            dataset=json.dumps({'cases':[{'id':'a','task_kind':'body'}]}).encode()
            old={'attempt_id':'p__a__r001','provider':'p','case_id':'a',
                 'artifact_path':'artifacts/a.txt','ok':True,'error_type':None,
                 'metrics':{'content_pass':False,'content_sha256':'a'*64,
                            'scoring_source_sha256':'old'}}
            new=json.loads(json.dumps(old));new['metrics']['scoring_source_sha256']='new'
            old_bytes=(json.dumps(old)+'\n').encode();new_bytes=(json.dumps(new)+'\n').encode()
            review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256(dataset).hexdigest(),
                'results_sha256':hashlib.sha256(old_bytes).hexdigest()},
                'review_protocol':{'scope':'machine-failures'},
                'attempts':[{'attempt_id':old['attempt_id'],'machine_verdict':'fail',
                             'agent_verdict':'fail','final_verdict':'fail'}]}
            path=root/'result-review.json';path.write_text(json.dumps(review))
            self.assertTrue(rebind_unchanged_result_review(
                path,dataset,old_bytes,dataset,new_bytes,rebound_at='now'))
            rebound=json.loads(path.read_text())
            self.assertEqual(rebound['bindings']['results_sha256'],hashlib.sha256(new_bytes).hexdigest())

            changed=json.loads(json.dumps(new));changed['metrics']['content_pass']=True
            with self.assertRaisesRegex(ValueError,'verdict changed'):
                rebind_unchanged_result_review(
                    path,dataset,new_bytes,dataset,(json.dumps(changed)+'\n').encode(),rebound_at='later')


if __name__=='__main__':unittest.main()
