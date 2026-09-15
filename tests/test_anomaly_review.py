import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.anomaly_review import (
    LAYERS, TRIGGER_VERSION, detect_anomalies, require_anomaly_review,
)


class AnomalyReviewTests(unittest.TestCase):
    def test_deterministic_triggers_cover_cross_layer_suspicion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'artifacts').mkdir(); (root/'raw').mkdir()
            (root/'artifacts/empty.txt').write_text('')
            (root/'artifacts/a.txt').write_text('same short boilerplate')
            (root/'artifacts/b.txt').write_text('same short boilerplate')
            (root/'raw/doc.bin').write_bytes(b'x' * 1000)
            rows = [
                {'attempt_id':'p__empty','provider':'p','case_id':'empty','ok':True,
                 'artifact_path':'artifacts/empty.txt','metrics':{},
                 'provider_metadata':{'raw_stdout_path':'raw/doc.bin'}},
                {'attempt_id':'p__a','provider':'p','case_id':'a','ok':True,
                 'artifact_path':'artifacts/a.txt','metrics':{
                     'required_facts_pass':True, 'forbidden_phrases_pass':False}},
                {'attempt_id':'p__b','provider':'p','case_id':'b','ok':False,
                 'error_type':'tool_error','elapsed_ms':47,
                 'artifact_path':'artifacts/b.txt','metrics':{}},
            ]
            found = detect_anomalies(
                root, rows, reviewer_anomalies={'p__a':['suspicious reference']})
            self.assertIn('request-ok-empty-answer', found['p__empty'])
            self.assertIn('raw-nonempty-normalized-empty-or-tiny', found['p__empty'])
            self.assertIn('identical-short-output-across-cases', found['p__a'])
            self.assertIn('positive-structure-only-forbidden-failed', found['p__a'])
            self.assertIn('reviewer-active-anomaly', found['p__a'])
            self.assertIn('short-tool-error', found['p__b'])

    def test_real_stable_structure_forbidden_shape_triggers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'answer.txt').write_text('all product records')
            row={'attempt_id':'moli-webfetch__dynamic-react-cart__r001',
                 'provider':'moli-webfetch','case_id':'dynamic-react-cart','ok':True,
                 'artifact_path':'answer.txt','metrics':{
                     'stable_structure_pass':True, 'stable_content_pass':False,
                     'stable_task_pass':False, 'forbidden_hits':['Add to cart'],
                     'stable_task_errors':['forbidden_content']}}
            self.assertIn('positive-structure-only-forbidden-failed',
                          detect_anomalies(root,[row])[row['attempt_id']])

    def test_public_answer_with_host_local_path_triggers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'answer.txt').write_text(
                'Error\n    at execute (/Users/person/project/worker.ts:12:3)'
            )
            row = {'attempt_id': 'p__case', 'provider': 'p', 'case_id': 'case',
                   'ok': False, 'error_type': 'tool_error', 'elapsed_ms': 1000,
                   'artifact_path': 'answer.txt', 'metrics': {}}
            self.assertIn(
                'public-artifact-local-path',
                detect_anomalies(root, [row])[row['attempt_id']],
            )

    def test_persisted_raw_response_and_file_artifacts_trigger_tiny_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'answer.txt').write_text('')
            row={'attempt_id':'p__doc','provider':'p','case_id':'doc','ok':True,
                 'artifact_path':'answer.txt','metrics':{},'provider_metadata':{
                     'raw_response_path':'private_artifacts/p/doc.response.bin',
                     'raw_response_sha256':'a'*64,'raw_response_bytes':1000,
                     'file_evaluation':{'files':[{'artifact_path':'files/doc.bin','bytes':900}]}}}
            self.assertIn('raw-nonempty-normalized-empty-or-tiny',
                          detect_anomalies(root,[row])[row['attempt_id']])

            (root/'wrapped.bin').write_bytes(b'<html><body>PK\x03\x04' + b'x'*800)
            wrapped={**row, 'attempt_id':'p__wrapped', 'provider_metadata':{
                'route':'raw_document','raw_stdout_path':'wrapped.bin'}}
            self.assertIn('raw-document-response-mismatch',
                          detect_anomalies(root,[wrapped])[wrapped['attempt_id']])

    def test_current_moli_failure_shapes_are_anomalous_without_flagging_valid_files(self):
        """Compact regression copied from the fourteen 2026-09-11 Moli failures."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows=[]
            def add(case, text='', *, ok=True, error=None, status=None, route='ordinary_markdown',
                    blocked=False, complete=None, forbidden=None, document_response=None):
                path=f'{case}.txt'; (root/path).write_text(text)
                metrics={'task_kind':'structure' if route=='challenge_json_trace' else 'body',
                         'content_pass':False, 'blocked_page':blocked,
                         'blocked_page_hits':['verification'] if blocked else [],
                         'stable_task_pass':False}
                if complete is not None:
                    metrics['stable_complete_record_count']=complete
                if forbidden:
                    metrics.update(stable_structure_pass=True,
                                   forbidden_hits=forbidden,
                                   stable_task_errors=['forbidden_content'])
                rows.append({'attempt_id':f'moli-webfetch__{case}__r001',
                             'provider':'moli-webfetch','case_id':case,'ok':ok,
                             'status_code':status,'error_type':error,'elapsed_ms':4000,
                             'artifact_path':path,'metrics':metrics,'provider_metadata':{
                                 'route':route,'exit_code':0 if error in (None,'empty_output','invalid_output') else 1,
                                 'document_response':document_response}})
            add('lexmount-doc',ok=False,error='tool_error')
            add('web-retail-returns',ok=False,error='empty_output')
            add('lm-feishu-help',ok=False,error='tool_error')
            for case in ('stable-wcag21-recommendation','live-024-cascading-style-sheets-level-2-r',
                         'stable-xml10-recommendation'):
                add(case,'Performing security verification',blocked=True)
            add('live-036-build-on-the-openai-api-platform','Error: Forbidden 403')
            add('document-05',ok=False,error='invalid_output',route='raw_document',
                document_response='serialized_text_not_raw_document')
            add('image-unsplash-detail','anubis.techaro.lol botstopper',route='resource_page_runtime')
            add('challenge-02','JD navigation shell',route='challenge_json_trace',complete=0)
            add('challenge-04','Zhihu login shell',route='challenge_json_trace',complete=0)
            add('challenge-06','verification center',route='challenge_json_trace',blocked=True,complete=0)
            add('challenge-09','',ok=False,error='empty_output',status=202,
                route='challenge_json_trace',complete=0)
            add('dynamic-react-cart','all products plus Add to cart',forbidden=['Add to cart'])
            found=detect_anomalies(root,rows)
            self.assertEqual(len(found),14)
            self.assertIn('exit-zero-empty-output',found['moli-webfetch__web-retail-returns__r001'])
            self.assertIn('tool-or-configuration-error',found['moli-webfetch__lm-feishu-help__r001'])
            self.assertIn('blocked-or-forbidden-page',found['moli-webfetch__stable-wcag21-recommendation__r001'])
            self.assertIn('blocked-or-forbidden-page',found['moli-webfetch__live-036-build-on-the-openai-api-platform__r001'])
            self.assertIn('raw-document-response-mismatch',found['moli-webfetch__document-05__r001'])
            self.assertIn('blocked-or-forbidden-page',found['moli-webfetch__image-unsplash-detail__r001'])
            self.assertIn('route-output-contract-mismatch',found['moli-webfetch__challenge-02__r001'])
            self.assertIn('route-output-contract-mismatch',found['moli-webfetch__challenge-04__r001'])
            self.assertIn('suspicious-http-status',found['moli-webfetch__challenge-09__r001'])

            valid_files=[]
            for case in ('image-10','video-05','video-08'):
                path=f'{case}.txt'; (root/path).write_text('')
                valid_files.append({'attempt_id':f'moli-webfetch__{case}__r001',
                    'provider':'moli-webfetch','case_id':case,'ok':True,'artifact_path':path,
                    'metrics':{'task_kind':'file','content_pass':False},'provider_metadata':{
                        'route':'raw_file','exit_code':0,'file_evaluation':{'files':[{
                            'artifact_path':f'{case}.bin','bytes':1000,'pass':True}]}}})
            self.assertEqual(detect_anomalies(root,valid_files),{})

    def test_equivalent_frozen_sources_do_not_trigger_duplicate_short_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows=[]
            for case in ('static-simple','redirect'):
                (root/f'{case}.txt').write_text('Example Domain shared answer')
                rows.append({'attempt_id':f'p__{case}','provider':'p','case_id':case,'ok':True,
                             'artifact_path':f'{case}.txt','metrics':{'content_pass':True}})
            dataset={'cases':[{'id':case,'source_verification':{'reference_sha256':'a'*64}}
                              for case in ('static-simple','redirect')]}
            self.assertEqual(detect_anomalies(root,rows,dataset),{})

    def test_complete_declared_cohort_with_one_pass_triggers_every_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            cases = []
            for index in range(10):
                case_id = f'document-{index:02d}'
                artifact = f'{case_id}.txt'
                (root / artifact).write_text(f'answer {index}')
                cases.append({'id': case_id, 'track': 'document'})
                rows.append({
                    'attempt_id': f'provider__{case_id}__r001',
                    'provider': 'provider',
                    'case_id': case_id,
                    'ok': index == 0,
                    'artifact_path': artifact,
                    'metrics': {'content_pass': index == 0},
                })
            dataset = {
                'cases': cases,
                'coverage_plan': {'document': ['provider']},
            }
            found = detect_anomalies(root, rows, dataset)
            self.assertEqual(set(found), {row['attempt_id'] for row in rows})
            self.assertTrue(all(
                'provider-track-at-most-one-pass' in triggers
                for triggers in found.values()
            ))

    def test_low_pass_cohort_trigger_rejects_partial_or_duplicate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [{'id': f'case-{index}', 'track': 'video'} for index in range(4)]
            dataset = {'cases': cases, 'coverage_plan': {'video': ['provider']}}

            def row(index, passed=False):
                artifact = f'answer-{index}.txt'
                (root / artifact).write_text(f'answer {index}')
                return {
                    'attempt_id': f'provider__case-{index}__r001',
                    'provider': 'provider', 'case_id': f'case-{index}',
                    'artifact_path': artifact,
                    'metrics': {'content_pass': passed},
                }

            partial = [row(0, True), row(1), row(2)]
            self.assertFalse(any(
                'provider-track-at-most-one-pass' in triggers
                for triggers in detect_anomalies(root, partial, dataset).values()
            ))
            duplicate = [row(0, True), row(1), row(2), row(2)]
            self.assertFalse(any(
                'provider-track-at-most-one-pass' in triggers
                for triggers in detect_anomalies(root, duplicate, dataset).values()
            ))

    def test_required_review_blocks_unresolved_and_uncorrected_defect(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'empty.txt').write_text('')
            dataset={'comparison':{'require_anomaly_review':True}}
            records=[{'attempt_id':'p__a','provider':'p','case_id':'a','ok':True,
                      'artifact_path':'empty.txt','metrics':{}}]
            review={'review_protocol':{'anomaly_review':{
                        'trigger_version':TRIGGER_VERSION, 'performed_after_blind_verdict':True,
                        'triggered_attempts':1, 'seven_layers':list(LAYERS)}},
                    'attempts':[{'attempt_id':'p__a','reviewer_anomalies':[],
                                 'anomaly_review':self.audit('unresolved')} ]}
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                require_anomaly_review(root,dataset,records,review)
            review['attempts'][0]['anomaly_review']=self.audit('corrected', defect=True)
            review['attempts'][0]['anomaly_review']['correction_applied']=False
            with self.assertRaisesRegex(ValueError, 'must be corrected'):
                require_anomaly_review(root,dataset,records,review)
            review['attempts'][0]['anomaly_review']['correction_applied']=True
            require_anomaly_review(root,dataset,records,review)
            review['attempts'][0]['anomaly_review']['layers']['raw']['status']='insufficient'
            with self.assertRaisesRegex(ValueError, 'insufficient'):
                require_anomaly_review(root,dataset,records,review)
            review['attempts'][0]['anomaly_review']['layers']['raw']['status']='defect'
            review['attempts'][0]['anomaly_review']['correction_applied']=False
            with self.assertRaisesRegex(ValueError, 'must be corrected'):
                require_anomaly_review(root,dataset,records,review)

    def test_transport_timeout_raw_gap_requires_bound_call_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'empty.txt').write_text('')
            (root / 'call-logs').mkdir()
            empty_sha = hashlib.sha256(b'').hexdigest()
            log = {
                'response': {'status_code': None},
                'error': {'type': 'timeout', 'message': 'read timed out'},
                'output': {'sha256': empty_sha, 'bytes': 0},
            }
            data = json.dumps(log).encode()
            (root / 'call-logs/physical.json').write_bytes(data)
            row = {
                'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a',
                'ok': False, 'status_code': None, 'error_type': 'timeout',
                'error_message': 'read timed out', 'artifact_path': 'empty.txt',
                'elapsed_ms': 40000, 'metrics': {'content_sha256': empty_sha},
                'provider_metadata': {'call_log': {
                    'path': 'call-logs/physical.json',
                    'sha256': hashlib.sha256(data).hexdigest(),
                }},
            }
            dataset = {'comparison': {'require_anomaly_review': True}}
            reviewer_anomalies = {'p__a': ['transport raw gap']}
            triggers = detect_anomalies(
                root, [row], dataset, reviewer_anomalies)
            self.assertIn('p__a', triggers)
            audit = self.audit('no-defect')
            audit['triggers'] = triggers['p__a']
            audit['layers']['raw'] = {
                'status': 'insufficient',
                'evidence': 'Transport timed out before an adapter response was returned.',
            }
            review = {
                'review_protocol': {'anomaly_review': {
                    'trigger_version': TRIGGER_VERSION,
                    'performed_after_blind_verdict': True,
                    'triggered_attempts': 1,
                    'seven_layers': list(LAYERS),
                }},
                'attempts': [{'attempt_id': 'p__a',
                              'reviewer_anomalies': reviewer_anomalies['p__a'],
                              'anomaly_review': audit}],
            }
            require_anomaly_review(root, dataset, [row], review)
            row['status_code'] = 200
            with self.assertRaisesRegex(ValueError, 'insufficient'):
                require_anomaly_review(root, dataset, [row], review)
            row['status_code'] = None
            row['provider_metadata']['call_log']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'insufficient'):
                require_anomaly_review(root, dataset, [row], review)
            row['provider_metadata']['call_log']['sha256'] = hashlib.sha256(data).hexdigest()
            audit['layers']['request']['status'] = 'insufficient'
            with self.assertRaisesRegex(ValueError, 'insufficient'):
                require_anomaly_review(root, dataset, [row], review)

    @staticmethod
    def audit(status, defect=False):
        return {'triggers':['request-ok-empty-answer'], 'status':status,
                'evaluation_defect':defect, 'correction_applied':not defect,
                'layers':{layer:{'status':'verified','evidence':f'{layer} evidence'} for layer in LAYERS},
                'same_class_scope':{'attempt_ids':['p__a'],'rationale':'same route and symptom'},
                'root_cause':'verified upstream or evaluation cause',
                'corrective_action':'documented correction or no-defect disposition',
                'rerun_scope':{'required':False,'attempt_ids':[],
                               'reason':'saved evidence remains authoritative'}}
