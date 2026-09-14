from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.reference_lifecycle import (preflight_reference_checks, sha256,
    require_verified_reference_checks, valid_scoring_revision)
from harness.web_fetch.report_explainer import (
    _digest, _headless_article_input, _article_speed_population,
    headless_evidence, render_explainer)
from scripts.certify_headless_cases import certify_case
from scripts.assemble_headless import (assemble, copy_bound_review, read_verified_batches,
                                       bind_assembled_scoring_revision,
                                       _prune_shadowed_batch_attempts)
from unittest.mock import patch


class HeadlessReportTests(unittest.TestCase):
    def test_article_speed_uses_all_common_tasks_of_displayed_providers(self):
        cases = {cid: {'id': cid, 'track': 'web'} for cid in ('a', 'b', 'c')}
        rows = []
        for provider, values in [('moli', [10, 100, 1000]), ('peer', [20, 50, 60]),
                                 ('undisplayed-version', [5, 5, 5])]:
            for cid, elapsed in zip(cases, values):
                passed = not (provider == 'peer' and cid == 'c') and not (
                    provider == 'undisplayed-version' and cid == 'b')
                rows.append({'provider': provider, 'case_id': cid, 'ok': passed,
                             'elapsed_ms': elapsed, 'metrics': {'content_pass': passed}})
        population = _article_speed_population(cases, rows, ['moli', 'peer'], execution_scope='test')
        self.assertEqual(population['planned_case_count'], 3)
        self.assertEqual(population['common_pass_cases'], ['a', 'b'])
        self.assertEqual(population['track_counts'], {'web': 2})
        self.assertEqual(population['rows'][0]['mean_ms'], 55)
        self.assertEqual(population['rows'][0]['p50_ms'], 55)
        self.assertEqual(population['rows'][0]['p95_ms'], 100)
        # Including another configuration legitimately changes the population;
        # its failure must not silently constrain a five-browser article.
        expanded = _article_speed_population(cases, rows,
            ['moli', 'peer', 'undisplayed-version'], execution_scope='test')
        self.assertEqual(expanded['common_pass_cases'], ['a'])
        with self.assertRaisesRegex(ValueError, 'one authoritative'):
            _article_speed_population(cases, rows + [rows[0]], ['moli', 'peer'], execution_scope='test')
        with self.assertRaisesRegex(ValueError, 'complete declared matrix'):
            _article_speed_population(cases, rows[1:], ['moli', 'peer'], execution_scope='test')

    def test_article_binding_ignores_only_report_derived_annotations(self):
        cases = {'a': {'id': 'a', 'track': 'web', 'minimum_content_chars': 10}}
        record = {'provider': 'moli', 'case_id': 'a', 'attempt_id': 'moli__a',
                  'ok': True, 'elapsed_ms': 10,
                  'metrics': {'content_pass': True, 'content_sha256': 'original'},
                  'provider_metadata': {'browser_version': 'moli 1.1.1'}}
        def binding(cs, row):
            return _digest(_headless_article_input(cs, [row], ['moli']))
        expected = binding(cases, record)
        annotated = record | {'diagnosis': {'independent_source_check': 'available'},
                              'title_quality': {'score': 0.5}, 'failure_category': 'label'}
        self.assertEqual(binding(cases, annotated), expected)
        mutations = [record | {'ok': False}, record | {'elapsed_ms': 11},
                     record | {'metrics': record['metrics'] | {'content_pass': False}},
                     record | {'metrics': record['metrics'] | {'content_sha256': 'changed'}},
                     record | {'provider_metadata': {'browser_version': 'moli 1.1.3'}}]
        for changed in mutations:
            with self.subTest(record=changed):
                self.assertNotEqual(binding(cases, changed), expected)
        changed_cases = {'a': cases['a'] | {'minimum_content_chars': 11}}
        self.assertNotEqual(binding(changed_cases, record), expected)

    def test_assembled_scoring_history_is_bound_to_exact_reviewed_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            dataset={'comparison':{'require_independent_result_review':True},
                     'cases':[{'id':'body','task_kind':'body'}]}
            record={'attempt_id':'p__body__r001','provider':'p','case_id':'body','ok':True,
                    'metrics':{'content_pass':True}}
            (root/'dataset.json').write_text(json.dumps(dataset))
            (root/'results.jsonl').write_text(json.dumps(record)+'\n')
            review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()},
                'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':record['attempt_id'],'machine_verdict':'pass',
                             'agent_verdict':'pass','final_verdict':'pass'}]}
            (root/'result-review.json').write_text(json.dumps(review))
            history=[{'kind':'audited_scoring_rule_revision','provider_calls':0,
                      'output_dataset_sha256':'old','output_results_sha256':'old',
                      'changed_cases':[],'task_decision_changes':[]}]
            (root/'scoring-revisions.json').write_text(json.dumps(history))
            self.assertTrue(bind_assembled_scoring_revision(root))
            self.assertTrue(valid_scoring_revision(root))
            bound=json.loads((root/'scoring-revisions.json').read_text())[-1]
            self.assertEqual(bound['input_results_sha256'],bound['output_results_sha256'])
            self.assertFalse(bind_assembled_scoring_revision(root))
            history=json.loads((root/'scoring-revisions.json').read_text())
            history[0]['changed_cases']=[{'case_id':'tampered','fields':[]}]
            (root/'scoring-revisions.json').write_text(json.dumps(history))
            self.assertFalse(valid_scoring_revision(root))

    def test_case_scoped_forbidden_text_correction_is_a_valid_scoring_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'dataset.json').write_text('{"cases": []}\n')
            (root / 'results.jsonl').write_text('')
            receipt = {
                'kind': 'audited_scoring_rule_revision',
                'provider_calls': 0,
                'output_dataset_sha256': hashlib.sha256(
                    (root / 'dataset.json').read_bytes()
                ).hexdigest(),
                'output_results_sha256': hashlib.sha256(
                    (root / 'results.jsonl').read_bytes()
                ).hexdigest(),
                'task_decision_changes': [],
                'changed_cases': [
                    {'case_id': 'example', 'fields': ['forbidden_any']}
                ],
            }
            (root / 'scoring-revisions.json').write_text(json.dumps([receipt]))
            self.assertTrue(valid_scoring_revision(root))

    def test_prior_review_is_copied_only_when_dataset_and_results_are_identical(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';output=root/'output'
            source.mkdir();output.mkdir()
            for target in (source,output):
                (target/'dataset.json').write_text('{"cases": []}\n')
                (target/'results.jsonl').write_text('')
            review={'bindings':{
                'dataset_sha256':hashlib.sha256((source/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()}}
            (source/'result-review.json').write_text(json.dumps(review))
            self.assertTrue(copy_bound_review(source,output))
            self.assertEqual(json.loads((output/'result-review.json').read_text()),review)
            (output/'result-review.json').unlink()
            (output/'dataset.json').write_text('{"cases": [1]}\n')
            self.assertFalse(copy_bound_review(source,output))
            self.assertFalse((output/'result-review.json').exists())

    def test_shadowed_attempt_pruning_removes_stale_case_binding(self):
        batches=[{'started_at':'2026-09-01T00:00:00+00:00','completed_at':'2026-09-01T00:00:10+00:00',
                  'attempt_ids':['old-a','kept-b'],'reference_checks':{
                      'cases':{'a':{},'b':{}},'postcheck':{'cases':{'a':{},'b':{}}}}}]
        records=[{'attempt_id':'old-a','case_id':'a','started_at':'2026-09-02T00:00:00+00:00',
                  'completed_at':'2026-09-02T00:00:01+00:00','elapsed_ms':1000},
                 {'attempt_id':'kept-b','case_id':'b','started_at':'2026-09-01T00:00:01+00:00',
                  'completed_at':'2026-09-01T00:00:02+00:00','elapsed_ms':1000}]
        result=_prune_shadowed_batch_attempts(batches,records)
        self.assertEqual(result[0]['attempt_ids'],['kept-b'])
        self.assertEqual(set(result[0]['reference_checks']['cases']),{'b'})
        self.assertEqual(set(result[0]['reference_checks']['postcheck']['cases']),{'b'})

    def test_verified_batches_accepts_prior_assembly(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            batches=[{'run_id':'one'},{'run_id':'two'}]
            (root/'execution.json').write_text(json.dumps({'batches':batches}))
            with patch('scripts.assemble_headless.require_verified_reference_checks') as verify:
                self.assertEqual(read_verified_batches(root,{'cases':[]},[]),batches)
                verify.assert_called_once_with(batches,cases={'cases':[]},records=[],source=root,
                    equivalent_capture_bindings=False)

    def test_matched_success_latency_and_unknown_quality(self):
        cases={cid:{'id':cid,'name':cid,'track':'web'} for cid in ('a','b','c')}
        records=[]
        for name,passed,times in [('chromium',[True,True,False],[10,20,30]),('moli',[True,False,True],[5,2,3])]:
            for cid,ok,ms in zip(cases,passed,times):
                records.append({'provider':name,'case_id':cid,'provider_type':'headless_html',
                    'started_at':'2026-09-03T00:00:00Z','provider_metadata':{},'ok':True,
                    'elapsed_ms':ms,'metrics':{'content_pass':ok,'word_f1':None}})
        evidence=headless_evidence(cases,records)
        pair=evidence['pairs'][0]
        self.assertEqual(pair['both_pass_ids'],['a'])
        self.assertEqual(pair['provider_p50_ms'],5)
        self.assertEqual(pair['chromium_p50_ms'],10)
        self.assertEqual(pair['provider_only'],['c'])
        self.assertEqual(pair['chromium_only'],['b'])
        self.assertIsNone(evidence['quality']['moli']['word_f1'])
        self.assertEqual(evidence['cases'], 3)
        self.assertEqual(evidence['tracks']['web']['providers']['moli']['passed'], 2)

    def test_authored_article_rejects_incomplete_browser_matrix(self):
        cases = {'a': {'id': 'a', 'track': 'web'}}
        record = {'provider': 'moli', 'case_id': 'a', 'provider_type': 'headless_html',
                  'started_at': '2026-09-03T00:00:00Z', 'provider_metadata': {},
                  'ok': True, 'elapsed_ms': 10,
                  'metrics': {'content_pass': True, 'word_f1': None}}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'missing: chromium, kitesurf, lightpanda, obscura'):
                render_explainer(Path(temp), cases, [record])
            self.assertFalse((Path(temp) / 'guide/article.html').exists())

    def test_authored_five_browser_article_rejects_changed_evidence(self):
        cases = {'a': {'id': 'a', 'track': 'web'}}
        records = [{'provider': name, 'case_id': 'a', 'attempt_id': name + '__a',
                    'provider_type': 'headless_html', 'started_at': '2026-09-03T00:00:00Z',
                    'provider_metadata': {}, 'ok': True, 'elapsed_ms': 10,
                    'metrics': {'content_pass': True, 'word_f1': None}}
                   for name in ('moli', 'chromium', 'lightpanda', 'obscura', 'kitesurf')]
        with tempfile.TemporaryDirectory() as temp:
            speed_dir = Path(temp) / 'speed-cohort'
            speed_dir.mkdir()
            (speed_dir / 'speed-comparison.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'article evidence changed'):
                render_explainer(Path(temp), cases, records)
            self.assertFalse((Path(temp) / 'guide/article.html').exists())

    def test_authored_article_rejects_missing_speed_evidence(self):
        cases = {'a': {'id': 'a', 'track': 'web'}}
        records = [{'provider': name, 'case_id': 'a', 'attempt_id': name + '__a',
                    'provider_type': 'headless_html', 'started_at': '2026-09-03T00:00:00Z',
                    'provider_metadata': {}, 'ok': True, 'elapsed_ms': 10,
                    'metrics': {'content_pass': True, 'word_f1': None}}
                   for name in ('moli', 'chromium', 'lightpanda', 'obscura', 'kitesurf')]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'requires reviewed speed evidence'):
                render_explainer(Path(temp), cases, records)
            self.assertFalse((Path(temp) / 'guide/article.html').exists())

    def prepare(self,root):
        now=datetime.now(timezone.utc)
        case={'id':'body','name':'Body','url':'https://example.test/body','track':'web',
            'task_kind':'body','ground_truth_main_content':'Reference body',
            'source_verification':{'state':'valid','method':'independent fixture',
                'checked_at':(now-timedelta(seconds=10)).isoformat(),
                'reference_sha256':sha256('Reference body')}}
        dataset={'cases':[case],'coverage_plan':{'web':['p']}}
        (root/'dataset.json').write_text(json.dumps(dataset))
        checks=preflight_reference_checks([case],root/'dataset.json',now=now-timedelta(seconds=5))
        manifest={'started_at':(now-timedelta(seconds=4)).isoformat(),'reference_checks':checks}
        (root/'manifest.json').write_text(json.dumps(manifest))
        record={'attempt_id':'p__body__r001','provider':'p','case_id':'body',
            'started_at':(now-timedelta(seconds=3)).isoformat(),
            'completed_at':(now-timedelta(seconds=2)).isoformat(),'elapsed_ms':1000,
            'metrics':{'reference_sha256':sha256('Reference body')}}
        observation={'case_id':'body','independent':True,'content_text':'Reference body',
            'checked_at':(now-timedelta(seconds=1)).isoformat(),
            'scope_identity':checks['cases']['body']['scope_identity'],
            'completion':{'satisfied':True,'elapsed_ms':100,'timeout_ms':1000}}
        return case,record,observation

    def test_case_projection_preserves_live_binding_and_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case,record,observation=self.prepare(root)
            before=(root/'manifest.json').read_bytes()
            result=certify_case(root,root/'certified',case,[record],observation)
            self.assertEqual(result['status'],'verified')
            target=root/'certified/body'
            manifest=json.loads((target/'manifest.json').read_text())
            require_verified_reference_checks(manifest,cases=[case],records=[record],source=target)
            self.assertEqual((root/'manifest.json').read_bytes(),before)
            self.assertEqual(json.loads((target/'results.jsonl').read_text()),record)
            with self.assertRaises(FileExistsError):
                certify_case(root,root/'certified',case,[record],observation)

    def test_failed_observation_stays_failed_and_incomplete_cohort_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case,record,observation=self.prepare(root)
            with self.assertRaises(ValueError):
                certify_case(root,root/'incomplete',case,[],observation)
            observation['completion']['satisfied']=False
            result=certify_case(root,root/'certified',case,[record],observation)
            self.assertEqual(result['status'],'reference_unverified')
            with self.assertRaises(FileExistsError):
                certify_case(root,root/'certified',case,[record],{**observation,'completion':{'satisfied':True,'elapsed_ms':1,'timeout_ms':2}})

    def test_diagnostic_assembly_does_not_bypass_publication_gate(self):
        from harness.web_fetch.reference_lifecycle import ReferenceLifecycleError
        from harness.web_fetch.report_build import build_report
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);case,record,observation=self.prepare(root)
            dataset=json.loads((root/'dataset.json').read_text())
            dataset['comparison']={'kind':'headless_html'}
            (root/'dataset.json').write_text(json.dumps(dataset))
            artifact=root/'artifacts/output.txt';artifact.parent.mkdir();artifact.write_text('Reference body')
            record.update(artifact_path='artifacts/output.txt',provider_type='headless_html',ok=True)
            record['metrics']['content_sha256']=sha256(artifact.read_bytes())
            (root/'results.jsonl').write_text(json.dumps(record)+'\n')
            (root/'sources').mkdir();(root/'list-references').mkdir()
            observation['completion']['satisfied']=False
            certify_case(root,root/'certified',case,[record],observation)
            with self.assertRaises(ReferenceLifecycleError):
                assemble(root,root/'certified',[],root/'published')
            self.assertFalse((root/'published').exists())
            assemble(root,root/'certified',[],root/'diagnostic',retain_unverified=True)
            preserved=json.loads((root/'diagnostic/results.jsonl').read_text())
            self.assertEqual(preserved,record)
            with self.assertRaises(ReferenceLifecycleError):
                build_report(root/'diagnostic',root/'report')
            self.assertFalse((root/'report').exists())
