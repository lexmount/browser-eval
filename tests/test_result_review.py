import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.result_review import require_result_review
from scripts.merge_result_review_revision import merge, merge_replacement
from scripts.revise_body_scoring import (
    IndependentReviewRequired, archive_superseded_result_review,
    rebind_unchanged_result_review,
)


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
            with self.assertRaisesRegex(IndependentReviewRequired,'verdict changed'):
                rebind_unchanged_result_review(
                    path,dataset,new_bytes,dataset,(json.dumps(changed)+'\n').encode(),rebound_at='later')

    def test_nonjudgmental_dataset_metadata_can_rebind_but_cases_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            before={'id':'old','name':'Old','coverage_plan':{'web':['p']},
                    'comparison':{'conversion':'old'},
                    'cases':[{'id':'a','task_kind':'body'}]}
            after=json.loads(json.dumps(before));after['id']='current';after['name']='Current'
            after['comparison']['conversion']='corrected description'
            row={'attempt_id':'p__a__r001','provider':'p','case_id':'a',
                 'artifact_path':'artifacts/a.txt','ok':True,'error_type':None,
                 'metrics':{'content_pass':True,'content_sha256':'a'*64}}
            before_bytes=(json.dumps(before)+'\n').encode()
            after_bytes=(json.dumps(after)+'\n').encode()
            results=(json.dumps(row)+'\n').encode()
            review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256(before_bytes).hexdigest(),
                'results_sha256':hashlib.sha256(results).hexdigest()},
                'review_protocol':{'scope':'all'},
                'attempts':[{'attempt_id':row['attempt_id'],'machine_verdict':'pass',
                             'agent_verdict':'pass','final_verdict':'pass'}]}
            path=root/'result-review.json';path.write_text(json.dumps(review))
            self.assertTrue(rebind_unchanged_result_review(
                path,before_bytes,results,after_bytes,results,rebound_at='now'))
            rebound=json.loads(path.read_text())
            self.assertEqual(rebound['bindings']['dataset_sha256'],hashlib.sha256(after_bytes).hexdigest())
            self.assertEqual(
                rebound['review_protocol']['rebound_after_nonjudgmental_metadata_change']['changed_top_level_fields'],
                ['comparison','id','name'])
            changed=json.loads(json.dumps(after));changed['cases'][0]['task_kind']='file'
            with self.assertRaisesRegex(IndependentReviewRequired,'review inputs changed'):
                rebind_unchanged_result_review(
                    path,after_bytes,results,(json.dumps(changed)+'\n').encode(),results,
                    rebound_at='later')
            semantic=json.loads(json.dumps(after))
            semantic['comparison']['reference_binding_mode']='relaxed'
            with self.assertRaisesRegex(IndependentReviewRequired,'Comparison semantics changed'):
                rebind_unchanged_result_review(
                    path,after_bytes,results,(json.dumps(semantic)+'\n').encode(),results,
                    rebound_at='later')
            changed_row=json.loads(json.dumps(row))
            changed_row['provider_metadata']={
                'raw_stdout_path':'artifacts/raw.bin','raw_stdout_sha256':'b'*64,
                'file_evaluation':{'files':[{'artifact_path':'artifacts/file.bin',
                                             'sha256':'c'*64,'bytes':10,
                                             'mime':'application/pdf','pass':True}]},
            }
            changed_results=(json.dumps(changed_row)+'\n').encode()
            with self.assertRaisesRegex(IndependentReviewRequired,'Review-visible input changed'):
                rebind_unchanged_result_review(
                    path,after_bytes,results,after_bytes,changed_results,rebound_at='later')

    def test_superseded_review_is_hash_addressed_and_binding_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset,records,review=self.fixture(root)
            path=root/'result-review.json';path.write_text(json.dumps(review))
            archived=archive_superseded_result_review(
                path, before_dataset=(root/'dataset.json').read_bytes(),
                before_results=(root/'results.jsonl').read_bytes())
            archive=root/archived['prior_result_review_path']
            self.assertFalse(path.exists())
            self.assertTrue(archive.is_file())
            self.assertEqual(archived['prior_result_review_sha256'],
                             hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertTrue(archived['independent_review_required'])

            path.write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, 'not bound'):
                archive_superseded_result_review(
                    path, before_dataset=b'different',
                    before_results=(root/'results.jsonl').read_bytes())

    def test_changed_case_requires_complete_supplemental_blind_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); evidence=root/'evidence'; supplement=root/'supplement'
            evidence.mkdir();supplement.mkdir()
            old_dataset={'comparison':{'require_independent_result_review':True},
                         'cases':[{'id':'a','task_kind':'body','required_patterns':[{'id':'layout'}]},
                                  {'id':'b','task_kind':'body'}]}
            new_dataset=json.loads(json.dumps(old_dataset));new_dataset['cases'][0]['required_patterns']=[]
            old_records=[{'attempt_id':'p__a__r001','case_id':'a','ok':True,'metrics':{'content_pass':False}},
                         {'attempt_id':'p__b__r001','case_id':'b','ok':True,'metrics':{'content_pass':True}}]
            new_records=json.loads(json.dumps(old_records));new_records[0]['metrics']['content_pass']=True
            old_dataset_bytes=json.dumps(old_dataset).encode();old_results_bytes=(''.join(json.dumps(x)+'\n' for x in old_records)).encode()
            (evidence/'dataset.json').write_text(json.dumps(new_dataset))
            (evidence/'results.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in new_records))
            prior={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256(old_dataset_bytes).hexdigest(),
                'results_sha256':hashlib.sha256(old_results_bytes).hexdigest()},
                'reviewer':{'agent':'Codex'},
                'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':'p__a__r001','machine_verdict':'fail','agent_verdict':'fail','final_verdict':'fail'},
                            {'attempt_id':'p__b__r001','machine_verdict':'pass','agent_verdict':'pass','final_verdict':'pass'}]}
            prior_path=root/'prior.json';prior_path.write_text(json.dumps(prior))
            receipt={'input_dataset_sha256':prior['bindings']['dataset_sha256'],
                     'input_results_sha256':prior['bindings']['results_sha256'],
                     'changed_cases':[{'case_id':'a','fields':['required_patterns']}]}
            (evidence/'scoring-revisions.json').write_text(json.dumps([receipt]))
            subset_dataset={'comparison':{'require_independent_result_review':True},'cases':[new_dataset['cases'][0]]}
            (supplement/'dataset.json').write_text(json.dumps(subset_dataset))
            (supplement/'results.jsonl').write_text(json.dumps(new_records[0])+'\n')
            supplemental={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((supplement/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((supplement/'results.jsonl').read_bytes()).hexdigest()},
                'reviewer':{'agent':'Codex'},'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':'p__a__r001','machine_verdict':'pass','agent_verdict':'pass','final_verdict':'pass'}]}
            (supplement/'result-review.json').write_text(json.dumps(supplemental))
            merge(evidence,prior_path,supplement)
            merged=json.loads((evidence/'result-review.json').read_text())
            self.assertEqual(merged['attempts'][0]['machine_verdict'],'pass')
            self.assertEqual(merged['review_protocol']['scoring_rule_revision_review']['reviewed_attempts'],1)

    def test_task_replacement_reuses_only_unchanged_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prior_root=root/'prior'; current_root=root/'current'; supplement=root/'supplement'
            prior_root.mkdir();current_root.mkdir();supplement.mkdir()
            old_dataset={'comparison':{'require_independent_result_review':True},
                         'cases':[{'id':'a','task_kind':'body','url':'https://old.example'},
                                  {'id':'b','task_kind':'body','url':'https://same.example'}]}
            new_dataset=json.loads(json.dumps(old_dataset));new_dataset['cases'][0]['url']='https://fixed.example'
            old_records=[{'attempt_id':'p__a__r001','case_id':'a','ok':True,'metrics':{'content_pass':False}},
                         {'attempt_id':'p__b__r001','case_id':'b','ok':True,'metrics':{'content_pass':True}}]
            new_records=json.loads(json.dumps(old_records));new_records[0]['metrics']['content_pass']=True
            for path,dataset,records in ((prior_root,old_dataset,old_records),(current_root,new_dataset,new_records)):
                (path/'dataset.json').write_text(json.dumps(dataset))
                (path/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
            prior_review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((prior_root/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((prior_root/'results.jsonl').read_bytes()).hexdigest()},
                'reviewer':{'agent':'Codex'},'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':'p__a__r001','machine_verdict':'fail','agent_verdict':'fail','final_verdict':'fail'},
                            {'attempt_id':'p__b__r001','machine_verdict':'pass','agent_verdict':'pass','final_verdict':'pass'}]}
            (prior_root/'result-review.json').write_text(json.dumps(prior_review))
            subset_dataset={'comparison':{'require_independent_result_review':True},'cases':[new_dataset['cases'][0]]}
            (supplement/'dataset.json').write_text(json.dumps(subset_dataset))
            (supplement/'results.jsonl').write_text(json.dumps(new_records[0])+'\n')
            supplemental={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((supplement/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((supplement/'results.jsonl').read_bytes()).hexdigest()},
                'reviewer':{'agent':'Codex'},'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':'p__a__r001','machine_verdict':'pass','agent_verdict':'pass','final_verdict':'pass'}]}
            (supplement/'result-review.json').write_text(json.dumps(supplemental))
            merge_replacement(current_root,prior_root,supplement)
            merged=json.loads((current_root/'result-review.json').read_text())
            self.assertEqual([row['machine_verdict'] for row in merged['attempts']],['pass','pass'])
            self.assertEqual(merged['review_protocol']['task_replacement_review']['reviewed_attempts'],1)

            changed=json.loads(json.dumps(new_records));changed[1]['metrics']['content_pass']=False
            (current_root/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in changed))
            with self.assertRaisesRegex(ValueError,'Unchanged case result differs'):
                merge_replacement(current_root,prior_root,supplement)


if __name__=='__main__':unittest.main()
