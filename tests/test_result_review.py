import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.result_review import (
    IndependentReviewRequired,
    archive_superseded_result_review,
    install_result_review,
    merge_refresh_reviews,
    merge_scoring_reviews,
    rebase_review,
    rebind_unchanged_result_review,
    require_blind_result_review,
    require_result_review,
    review_input_identity,
)


class ResultReviewTests(unittest.TestCase):
    def test_rebase_cannot_overwrite_inputs_or_prior_review(self):
        for name in ('dataset.json', 'results.jsonl', 'result-review.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, _, review = self.fixture(root)
                review['attempts'][0]['agent_evidence'] = 'Observed synthetic answer'
                (root / 'result-review.json').write_text(json.dumps(review))
                before = {path.name: path.read_bytes() for path in root.iterdir()}
                with self.assertRaisesRegex(ValueError, 'must not overwrite its source evidence'):
                    rebase_review(root, root, root / name, None, install=True)
                self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, before)

    def test_review_install_failure_preserves_previous_file_and_cleans_staging(self):
        for failure in ('write', 'replace', 'input-change', 'output-change'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, _, review = self.fixture(root)
                target = root / 'result-review.json'
                target.write_text('previous review')
                dumps = json.dumps

                def serialize(value, **kwargs):
                    if failure == 'write':
                        raise OSError('synthetic serialization failure')
                    if failure == 'input-change':
                        (root / 'results.jsonl').write_text('{}\n')
                    if failure == 'output-change':
                        target.write_text('concurrent review')
                    return dumps(value, **kwargs)

                if failure == 'replace':
                    context = patch.object(Path, 'replace', side_effect=OSError('synthetic replace failure'))
                else:
                    context = patch('harness.web_fetch.result_review.json.dumps', side_effect=serialize)
                with context, self.assertRaises((OSError, ValueError)):
                    install_result_review(root, review)
                self.assertEqual(target.read_text(),
                                 'concurrent review' if failure == 'output-change' else 'previous review')
                self.assertEqual(list(root.glob('.result-review-*.tmp')), [])
                if failure == 'input-change':
                    self.assertEqual((root / 'results.jsonl').read_text(), '{}\n')

    def test_review_install_rejects_linked_target_without_touching_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, review = self.fixture(root)
            outside = root / 'outside.json'
            outside.write_text('unrelated file')
            (root / 'result-review.json').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'Symlinked evidence'):
                install_result_review(root, review)
            self.assertEqual(outside.read_text(), 'unrelated file')

    def test_rebase_rejection_preserves_installed_review_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, reviewed = root / 'current', root / 'reviewed'
            current.mkdir()
            reviewed.mkdir()
            dataset, records, prior = self.fixture(reviewed)
            prior['schema_version'] = 999
            prior['attempts'][0]['agent_evidence'] = 'Observed synthetic answer'
            (reviewed / 'result-review.json').write_text(json.dumps(prior))
            (current / 'dataset.json').write_bytes((reviewed / 'dataset.json').read_bytes())
            (current / 'results.jsonl').write_bytes((reviewed / 'results.jsonl').read_bytes())
            installed = current / 'result-review.json'
            installed.write_text('previous installed review')
            output = root / 'output.json'
            output.write_text('previous staged review')
            with self.assertRaisesRegex(ValueError, 'Unsupported result review schema'):
                rebase_review(current, reviewed, output, None, install=True)
            self.assertEqual(installed.read_text(), 'previous installed review')
            self.assertEqual(output.read_text(), 'previous staged review')

    def test_merge_does_not_follow_a_preexisting_temporary_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, prior, supplement, _ = self.multistage_revision_fixture(root)
            outside = root / 'outside.json'
            outside.write_text('unrelated file')
            temporary = evidence / '.result-review.json.merge-tmp'
            temporary.symlink_to(outside)
            merge_scoring_reviews(evidence, prior, supplement)
            self.assertEqual(outside.read_text(), 'unrelated file')
            self.assertTrue(temporary.is_symlink())
            self.assertFalse((evidence / 'result-review.json').is_symlink())

    def test_review_identity_binds_raw_response_stdout_and_files(self):
        record={'provider':'p','case_id':'a','provider_metadata':{
            'raw_response_path':'private_artifacts/p/a.response.bin',
            'raw_response_sha256':'a'*64,'raw_response_bytes':123,
            'raw_stdout_path':'artifacts/p/a.bin','raw_stdout_sha256':'b'*64,
            'file_evaluation':{'files':[{'artifact_path':'files/a.pdf','sha256':'c'*64,
                                         'bytes':456,'mime':'application/pdf','pass':True}]}}}
        identity=review_input_identity(record)
        self.assertEqual([item['kind'] for item in identity['raw_artifacts']],
                         ['raw_response','raw_stdout','file'])
        self.assertEqual(identity['raw_artifacts'][0]['bytes'],123)

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

    def test_declared_full_review_rejects_failure_only_even_with_no_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset, records, review = self.fixture(root)
            dataset['comparison']['independent_result_review_scope'] = 'all'
            (root / 'dataset.json').write_text(json.dumps(dataset))
            review['bindings']['dataset_sha256'] = hashlib.sha256(
                (root / 'dataset.json').read_bytes()).hexdigest()
            review['review_protocol']['scope'] = 'machine-failures'
            review['attempts'] = []
            with self.assertRaisesRegex(ValueError, 'declared full review'):
                require_result_review(root, dataset, records, review)
            review['review_protocol']['scope'] = 'all'
            review['attempts'] = [{'attempt_id': records[0]['attempt_id'],
                                  'machine_verdict': 'pass', 'agent_verdict': 'pass',
                                  'final_verdict': 'pass'}]
            result = require_result_review(root, dataset, records, review)
            self.assertEqual(result['reviewed_attempts'], 1)
            self.assertEqual(result['accepted_unreviewed_passes'], 0)
            dataset['comparison']['independent_result_review_scope'] = 'unsupported'
            (root / 'dataset.json').write_text(json.dumps(dataset))
            review['bindings']['dataset_sha256'] = hashlib.sha256(
                (root / 'dataset.json').read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'Unsupported declared'):
                require_result_review(root, dataset, records, review)

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

    def test_anomaly_gate_is_only_mandatory_when_dataset_explicitly_requires_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dataset,records,review=self.fixture(root)
            (root/'result-review.json').write_text(json.dumps(review))
            self.assertIsNotNone(require_result_review(root,dataset,records))
            dataset['comparison']['require_anomaly_review']=True
            (root/'dataset.json').write_text(json.dumps(dataset))
            review['bindings']['dataset_sha256']=hashlib.sha256(
                (root/'dataset.json').read_bytes()).hexdigest()
            (root/'result-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, 'cover every'):
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

    def test_completed_blind_review_rebases_across_score_only_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);reviewed=root/'reviewed';current=root/'current'
            reviewed.mkdir();current.mkdir()
            dataset={'comparison':{'require_independent_result_review':True},
                     'cases':[{'id':'a','task_kind':'body'}]}
            old={'attempt_id':'p__a__r001','provider':'p','case_id':'a',
                 'artifact_path':'artifacts/a.txt','ok':True,'error_type':None,
                 'metrics':{'content_pass':True,'content_sha256':'a'*64}}
            new=json.loads(json.dumps(old));new['metrics']['content_pass']=False
            for evidence,row in ((reviewed,old),(current,new)):
                (evidence/'dataset.json').write_text(json.dumps(dataset))
                (evidence/'results.jsonl').write_text(json.dumps(row)+'\n')
            review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((reviewed/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((reviewed/'results.jsonl').read_bytes()).hexdigest()},
                'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':old['attempt_id'],'machine_verdict':'pass',
                             'agent_verdict':'fail','agent_evidence':'Body is too short.',
                             'reviewer_anomalies':['unexpectedly short output'],
                             'final_verdict':None,'adjudication':None}]}
            (reviewed/'result-review.json').write_text(json.dumps(review))
            output=root/'rebased.json'
            result=rebase_review(current,reviewed,output,None,install=True)
            self.assertEqual(result['attempts'][0]['machine_verdict'],'fail')
            self.assertEqual(result['attempts'][0]['agent_verdict'],'fail')
            self.assertEqual(result['attempts'][0]['reviewer_anomalies'],
                             ['unexpectedly short output'])
            self.assertEqual(result['unresolved'],0)
            self.assertIsNotNone(require_result_review(current,dataset,[new]))

            changed=json.loads(json.dumps(new));changed['metrics']['content_sha256']='b'*64
            (current/'results.jsonl').write_text(json.dumps(changed)+'\n')
            with self.assertRaisesRegex(ValueError,'Reviewer-visible evidence changed'):
                rebase_review(current,reviewed,output,None)

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

    def test_equal_empty_answers_do_not_reuse_changed_runtime_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset, records, review = self.fixture(root)
            record = records[0]
            record.update(ok=False, error_type='tool_error', error_message='Engine panic A')
            record['provider_metadata'] = {
                'diagnostic_log_path': 'private_artifacts/current.stderr.log',
                'diagnostic_log_sha256': 'a' * 64,
                'diagnostic_log_bytes': 100,
            }
            record['metrics'].update(content_pass=False, content_sha256='e' * 64)
            before = (json.dumps(record) + '\n').encode()
            review['bindings']['results_sha256'] = hashlib.sha256(before).hexdigest()
            review['attempts'][0].update(machine_verdict='fail', agent_verdict='fail',
                                         final_verdict='fail')
            path = root / 'result-review.json'
            path.write_text(json.dumps(review))
            self.assertTrue(rebind_unchanged_result_review(
                path, (root / 'dataset.json').read_bytes(), before,
                (root / 'dataset.json').read_bytes(), before, rebound_at='now'))
            for change in ('diagnostic_hash', 'error_message'):
                changed = json.loads(json.dumps(record))
                if change == 'diagnostic_hash':
                    changed['provider_metadata']['diagnostic_log_sha256'] = 'b' * 64
                else:
                    changed['error_message'] = 'Transport reset B'
                after = (json.dumps(changed) + '\n').encode()
                path.write_text(json.dumps(review))
                original = path.read_bytes()
                with self.subTest(change=change), self.assertRaisesRegex(
                        IndependentReviewRequired, 'Review-visible input changed'):
                    rebind_unchanged_result_review(
                        path, (root / 'dataset.json').read_bytes(), before,
                        (root / 'dataset.json').read_bytes(), after, rebound_at='now')
                self.assertEqual(path.read_bytes(), original)

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
                     'output_dataset_sha256':hashlib.sha256((evidence/'dataset.json').read_bytes()).hexdigest(),
                     'output_results_sha256':hashlib.sha256((evidence/'results.jsonl').read_bytes()).hexdigest(),
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
            merge_scoring_reviews(evidence,prior_path,supplement)
            merged=json.loads((evidence/'result-review.json').read_text())
            self.assertEqual(merged['attempts'][0]['machine_verdict'],'pass')
            self.assertEqual(merged['review_protocol']['scoring_rule_revision_review']['reviewed_attempts'],1)

    def multistage_revision_fixture(self, root):
        evidence=root/'evidence';supplement=root/'supplement'
        evidence.mkdir();supplement.mkdir()
        dataset={'comparison':{'require_independent_result_review':True},
                 'cases':[{'id':case_id,'task_kind':'body'} for case_id in ('a','b','c')]}
        old_records=[]
        for provider in ('p','q'):
            for case_id in ('a','b','c'):
                passed=case_id=='c'
                old_records.append({
                    'attempt_id':f'{provider}__{case_id}__r001',
                    'provider':provider,'case_id':case_id,'ok':True,
                    'metrics':{'content_pass':passed}})
        middle_records=json.loads(json.dumps(old_records))
        current_records=json.loads(json.dumps(old_records))
        for row in middle_records:
            if row['case_id']=='a':
                row['metrics']['content_pass']=True
        for row in current_records:
            if row['case_id'] in {'a','b'}:
                row['metrics']['content_pass']=True

        dataset_bytes=json.dumps(dataset).encode()
        old_bytes=(''.join(json.dumps(row)+'\n' for row in old_records)).encode()
        middle_bytes=(''.join(json.dumps(row)+'\n' for row in middle_records)).encode()
        current_bytes=(''.join(json.dumps(row)+'\n' for row in current_records)).encode()
        (evidence/'dataset.json').write_bytes(dataset_bytes)
        (evidence/'results.jsonl').write_bytes(current_bytes)
        prior={'schema_version':1,
               'bindings':{'dataset_sha256':hashlib.sha256(dataset_bytes).hexdigest(),
                           'results_sha256':hashlib.sha256(old_bytes).hexdigest()},
               'reviewer':{'agent':'Codex'},
               'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
               'attempts':[]}
        for row in old_records:
            verdict='pass' if row['metrics']['content_pass'] else 'fail'
            prior['attempts'].append({'attempt_id':row['attempt_id'],
                                      'machine_verdict':verdict,
                                      'agent_verdict':verdict,
                                      'final_verdict':verdict})
        prior_path=root/'prior.json';prior_path.write_text(json.dumps(prior))
        dataset_hash=hashlib.sha256(dataset_bytes).hexdigest()
        receipts=[
            {'input_dataset_sha256':dataset_hash,
             'input_results_sha256':hashlib.sha256(old_bytes).hexdigest(),
             'output_dataset_sha256':dataset_hash,
             'output_results_sha256':hashlib.sha256(middle_bytes).hexdigest(),
             'changed_cases':[{'case_id':'a','fields':['forbidden_any']}]},
            {'input_dataset_sha256':dataset_hash,
             'input_results_sha256':hashlib.sha256(middle_bytes).hexdigest(),
             'output_dataset_sha256':dataset_hash,
             'output_results_sha256':hashlib.sha256(current_bytes).hexdigest(),
             'changed_cases':[{'case_id':'b','fields':['body_regions']}]},
        ]
        (evidence/'scoring-revisions.json').write_text(json.dumps(receipts))
        changed_cases=[case for case in dataset['cases'] if case['id'] in {'a','b'}]
        changed_records=[row for row in current_records if row['case_id'] in {'a','b'}]
        (supplement/'dataset.json').write_text(json.dumps({
            'comparison':dataset['comparison'],'cases':changed_cases}))
        (supplement/'results.jsonl').write_text(
            ''.join(json.dumps(row)+'\n' for row in changed_records))
        supplemental={'schema_version':1,
                      'bindings':{
                          'dataset_sha256':hashlib.sha256((supplement/'dataset.json').read_bytes()).hexdigest(),
                          'results_sha256':hashlib.sha256((supplement/'results.jsonl').read_bytes()).hexdigest()},
                      'reviewer':{'agent':'Codex'},
                      'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                      'attempts':[{
                          'attempt_id':row['attempt_id'],'machine_verdict':'pass',
                          'agent_verdict':'pass','final_verdict':'pass'}
                          for row in changed_records]}
        (supplement/'result-review.json').write_text(json.dumps(supplemental))
        return evidence,prior_path,supplement,receipts

    def test_multistage_revision_reviews_union_of_changed_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            evidence,prior_path,supplement,_=self.multistage_revision_fixture(root)
            merge_scoring_reviews(evidence,prior_path,supplement)
            merged=json.loads((evidence/'result-review.json').read_text())
            revision=merged['review_protocol']['scoring_rule_revision_review']
            self.assertEqual(revision['changed_cases'],['a','b'])
            self.assertEqual(revision['reviewed_attempts'],4)
            self.assertEqual(revision['revision_receipts'],2)
            self.assertEqual(merged['reviewed_attempts'],6)
            self.assertEqual(merged['accepted_unreviewed_passes'],0)

    def test_multistage_revision_rejects_broken_receipt_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            evidence,prior_path,supplement,receipts=self.multistage_revision_fixture(root)
            receipts[1]['input_results_sha256']='broken'
            (evidence/'scoring-revisions.json').write_text(json.dumps(receipts))
            with self.assertRaisesRegex(ValueError,'chain is broken'):
                merge_scoring_reviews(evidence,prior_path,supplement)
            self.assertFalse((evidence/'result-review.json').exists())

    def test_multistage_revision_rejects_omitted_changed_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            evidence,prior_path,supplement,_=self.multistage_revision_fixture(root)
            subset=json.loads((supplement/'dataset.json').read_text())
            subset['cases']=[case for case in subset['cases'] if case['id']=='a']
            records=[json.loads(line) for line in
                     (supplement/'results.jsonl').read_text().splitlines()
                     if json.loads(line)['case_id']=='a']
            (supplement/'dataset.json').write_text(json.dumps(subset))
            (supplement/'results.jsonl').write_text(
                ''.join(json.dumps(row)+'\n' for row in records))
            review=json.loads((supplement/'result-review.json').read_text())
            review['bindings']={
                'dataset_sha256':hashlib.sha256((supplement/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((supplement/'results.jsonl').read_bytes()).hexdigest()}
            review['attempts']=[row for row in review['attempts']
                                if '__a__' in row['attempt_id']]
            (supplement/'result-review.json').write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError,'every current changed task'):
                merge_scoring_reviews(evidence,prior_path,supplement)
            self.assertFalse((evidence/'result-review.json').exists())

    def test_multistage_revision_rejects_current_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            evidence,prior_path,supplement,receipts=self.multistage_revision_fixture(root)
            receipts[-1]['output_results_sha256']='not-current'
            (evidence/'scoring-revisions.json').write_text(json.dumps(receipts))
            with self.assertRaisesRegex(ValueError,'does not end at current evidence'):
                merge_scoring_reviews(evidence,prior_path,supplement)
            self.assertFalse((evidence/'result-review.json').exists())

    def test_multistage_revision_rejects_each_preinstall_gate(self):
        scenarios = (
            ('prior-binding-missing', 'not bound to any scoring revision inputs'),
            ('receipt-output-missing', 'has no output binding'),
            ('supplemental-not-blind', 'not blinded to machine judgments'),
            ('supplemental-not-all', 'must cover all changed attempts'),
            ('supplemental-not-current', 'not run against current persisted results'),
            ('prior-inventory-missing', 'Prior full review does not cover'),
        )
        for scenario, message in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence, prior_path, supplement, receipts = self.multistage_revision_fixture(root)

                if scenario == 'prior-binding-missing':
                    prior = json.loads(prior_path.read_text())
                    prior['bindings'].pop('results_sha256')
                    prior_path.write_text(json.dumps(prior))
                elif scenario == 'receipt-output-missing':
                    receipts[0].pop('output_results_sha256')
                    (evidence/'scoring-revisions.json').write_text(json.dumps(receipts))
                elif scenario == 'supplemental-not-blind':
                    review = json.loads((supplement/'result-review.json').read_text())
                    review['review_protocol']['machine_judgments_withheld'] = False
                    (supplement/'result-review.json').write_text(json.dumps(review))
                elif scenario == 'supplemental-not-all':
                    review = json.loads((supplement/'result-review.json').read_text())
                    review['review_protocol']['scope'] = 'machine-failures'
                    review['attempts'] = []
                    (supplement/'result-review.json').write_text(json.dumps(review))
                elif scenario == 'supplemental-not-current':
                    records = [json.loads(line) for line in
                               (supplement/'results.jsonl').read_text().splitlines()]
                    records[0]['provider_metadata'] = {'diagnostic': 'not-current'}
                    (supplement/'results.jsonl').write_text(
                        ''.join(json.dumps(row)+'\n' for row in records))
                    review = json.loads((supplement/'result-review.json').read_text())
                    review['bindings']['results_sha256'] = hashlib.sha256(
                        (supplement/'results.jsonl').read_bytes()).hexdigest()
                    (supplement/'result-review.json').write_text(json.dumps(review))
                elif scenario == 'prior-inventory-missing':
                    prior = json.loads(prior_path.read_text())
                    prior['attempts'] = prior['attempts'][:-1]
                    prior_path.write_text(json.dumps(prior))

                with self.assertRaisesRegex(ValueError, message):
                    merge_scoring_reviews(evidence, prior_path, supplement)
                self.assertFalse((evidence/'result-review.json').exists())

    def test_matrix_refresh_reuses_only_identical_task_and_result_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prior_root=root/'prior'; current_root=root/'current'; supplement=root/'supplement'
            prior_root.mkdir();current_root.mkdir();supplement.mkdir()
            old_dataset={'comparison':{'require_independent_result_review':True},
                         'cases':[{'id':'a','task_kind':'body','url':'https://old.example'},
                                  {'id':'b','task_kind':'body','url':'https://same.example'}]}
            new_dataset=json.loads(json.dumps(old_dataset));new_dataset['cases'][1]['url']='https://fixed.example'
            old_records=[]
            for provider in ('p','q'):
                for case_id in ('a','b'):
                    old_records.append({
                        'attempt_id':f'{provider}__{case_id}__r001',
                        'provider':provider,'case_id':case_id,'ok':True,
                        'metrics':{'content_pass':case_id == 'a'},
                    })
            new_records=json.loads(json.dumps(old_records))
            # A provider-only rerun on unchanged task a invalidates only p/a.
            new_records[0]['provider_metadata']={'images':['https://example.test/new.png']}
            # A task-definition change on b invalidates both providers, even
            # though only one result object happened to change.
            new_records[1]['metrics']['content_pass']=True
            # A staged blind review may already identify a score-only error.
            # The corrected score can then reuse that judgment even though
            # the old machine_verdict field itself is stale.
            new_records[2]['metrics']['content_pass']=True
            for path,dataset,records in ((prior_root,old_dataset,old_records),(current_root,new_dataset,new_records)):
                (path/'dataset.json').write_text(json.dumps(dataset))
                (path/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
            prior_review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((prior_root/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((prior_root/'results.jsonl').read_bytes()).hexdigest()},
                'reviewer':{'agent':'Codex'},'review_protocol':{
                    'machine_judgments_withheld':True,
                    'production_scorer_forbidden':True,
                    'direct_artifact_evidence_required':True,
                    'scope':'all'},
                'unresolved':0,
                'attempts':[]}
            for row in old_records:
                verdict='pass' if row['metrics']['content_pass'] else 'fail'
                reviewed_verdict=('pass' if row['attempt_id']=='q__a__r001' else verdict)
                prior_review['attempts'].append({
                    'attempt_id':row['attempt_id'],'machine_verdict':verdict,
                    'agent_verdict':reviewed_verdict,'agent_evidence':'direct artifact evidence',
                    'final_verdict':reviewed_verdict,
                    'adjudication':('confirmed score error' if reviewed_verdict!=verdict else None)})
            (prior_root/'result-review.json').write_text(json.dumps(prior_review))
            required_ids={'p__a__r001','p__b__r001','q__b__r001'}
            required_records=[row for row in new_records if row['attempt_id'] in required_ids]
            subset_dataset={'comparison':{'require_independent_result_review':True},
                            'cases':new_dataset['cases']}
            (supplement/'dataset.json').write_text(json.dumps(subset_dataset))
            (supplement/'results.jsonl').write_text(
                ''.join(json.dumps(row)+'\n' for row in required_records))
            supplemental={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((supplement/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((supplement/'results.jsonl').read_bytes()).hexdigest()},
                'reviewer':{'agent':'Codex'},'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[]}
            for row in required_records:
                verdict='pass' if row['metrics']['content_pass'] else 'fail'
                supplemental['attempts'].append({
                    'attempt_id':row['attempt_id'],'machine_verdict':verdict,
                    'agent_verdict':verdict,'final_verdict':verdict})
            (supplement/'result-review.json').write_text(json.dumps(supplemental))
            merge_refresh_reviews(current_root,prior_root,supplement,
                          prior_review_path=prior_root/'result-review.json')
            merged=json.loads((current_root/'result-review.json').read_text())
            refresh=merged['review_protocol']['matrix_refresh_review']
            self.assertEqual(refresh['changed_task_cases'],['b'])
            self.assertEqual(refresh['rerun_only_attempts'],['p__a__r001'])
            self.assertEqual(refresh['retained_attempts'],1)
            self.assertEqual(refresh['reviewed_attempts'],3)
            self.assertEqual(merged['reviewed_attempts'],4)
            self.assertEqual(merged['accepted_unreviewed_passes'],0)
            self.assertEqual(merged['unresolved'],0)
            self.assertEqual(merged['confirmed_scoring_errors'],0)

            changed=json.loads(json.dumps(new_records));changed[2]['provider_metadata']={'images':['https://example.test/also-new.png']}
            (current_root/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in changed))
            with self.assertRaisesRegex(ValueError,'exactly every non-reusable current attempt'):
                merge_refresh_reviews(current_root,prior_root,supplement)

    def test_matrix_refresh_stages_blind_review_before_full_anomaly_investigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prior=root/'prior';current=root/'current';supplement=root/'supplement'
            for path in (prior,current,supplement):
                path.mkdir();(path/'a.txt').write_text('')
            old_dataset={'comparison':{'require_independent_result_review':True},
                         'cases':[{'id':'a','task_kind':'body','track':'web','url':'https://old.example'}]}
            new_dataset=json.loads(json.dumps(old_dataset))
            new_dataset['comparison']['require_anomaly_review']=True
            new_dataset['cases'][0]['url']='https://new.example'
            record={'attempt_id':'p__a__r001','provider':'p','case_id':'a','ok':True,
                    'error_type':None,'artifact_path':'a.txt','metrics':{'content_pass':False}}
            for path,dataset in ((prior,old_dataset),(current,new_dataset),(supplement,new_dataset)):
                (path/'dataset.json').write_text(json.dumps(dataset))
                (path/'results.jsonl').write_text(json.dumps(record)+'\n')
                review={'schema_version':1,'bindings':{
                    'dataset_sha256':hashlib.sha256((path/'dataset.json').read_bytes()).hexdigest(),
                    'results_sha256':hashlib.sha256((path/'results.jsonl').read_bytes()).hexdigest()},
                    'reviewer':{'agent':'Codex'},
                    'review_protocol':{'scope':'all','machine_judgments_withheld':True,
                                       'production_scorer_forbidden':True,
                                       'direct_artifact_evidence_required':True},
                    'accepted_unreviewed_passes':0,'unresolved':0,
                    'attempts':[{'attempt_id':record['attempt_id'],'machine_verdict':'fail',
                                 'agent_verdict':'fail','agent_evidence':'observed empty answer',
                                 'final_verdict':'fail'}]}
                if path != current:
                    (path/'result-review.json').write_text(json.dumps(review))
            merge_refresh_reviews(current,prior,supplement)
            merged=json.loads((current/'result-review.json').read_text())
            require_blind_result_review(current,new_dataset,[record],merged)
            with self.assertRaisesRegex(ValueError,'cover every'):
                require_result_review(current,new_dataset,[record],merged)


if __name__=='__main__':unittest.main()
