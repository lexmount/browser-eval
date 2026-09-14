import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.anomaly_review import LAYERS, detect_anomalies
from harness.web_fetch.result_review import require_blind_result_review, require_result_review
from scripts.review_result_anomalies import (
    anomaly_groups, cached_group_resolutions, merge_group_resolutions,
    validate_group_batch)


class ReviewResultAnomaliesTests(unittest.TestCase):
    def test_prior_blind_review_can_be_upgraded_without_changing_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'a.txt').write_text('')
            dataset={'comparison':{'require_independent_result_review':True,
                                   'require_anomaly_review':True},
                     'cases':[{'id':'a','track':'web','task_kind':'body'}]}
            records=[{'attempt_id':'p__a','provider':'p','case_id':'a','ok':True,
                      'artifact_path':'a.txt','metrics':{'content_pass':True}}]
            prior={'schema_version':1,'bindings':{},'review_protocol':{
                'scope':'all','machine_judgments_withheld':True},'attempts':[{
                    'attempt_id':'p__a','machine_verdict':'pass','agent_verdict':'pass',
                    'agent_evidence':'beginning x; middle y; end z','final_verdict':'pass'}]}
            (root/'dataset.json').write_text(json.dumps(dataset));(root/'results.jsonl').write_text(json.dumps(records[0])+'\n')
            import hashlib
            prior['bindings']={'dataset_sha256':hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest(),
                               'results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()}
            require_blind_result_review(root,dataset,records,prior)
            with self.assertRaises(ValueError): require_result_review(root,dataset,records,prior)
            triggers=detect_anomalies(root,records,dataset);groups=anomaly_groups(dataset,records,triggers)
            layer={name:{'status':'verified','evidence':name+' checked'} for name in LAYERS}
            resolutions={'groups':[{'group_id':groups[0]['group_id'],'attempt_ids':['p__a'],
                'layers':layer,'same_class_rationale':'same empty-answer route','root_cause':'provider returned no answer',
                'corrective_action':'classified as provider failure','status':'no-defect',
                'evaluation_defect':False,'correction_applied':False,'rerun_required':False,
                'rerun_attempt_ids':[],'rerun_reason':'saved call is authoritative'}]}
            merged=merge_group_resolutions(prior,groups,resolutions,triggers)
            self.assertEqual(merged['attempts'][0]['agent_verdict'],'pass')
            self.assertEqual(merged['attempts'][0]['agent_evidence'],prior['attempts'][0]['agent_evidence'])
            require_result_review(root,dataset,records,merged)

    def test_same_class_group_expands_to_every_exact_attempt(self):
        dataset={'cases':[{'id':'a','track':'web'},{'id':'b','track':'web'}]}
        records=[{'attempt_id':f'p__{case}','provider':'p','case_id':case,
                  'error_type':'tool_error','status_code':None,
                  'provider_metadata':{'route':'ordinary_markdown'}} for case in ('a','b')]
        triggers={row['attempt_id']:['tool-or-configuration-error'] for row in records}
        groups=anomaly_groups(dataset,records,triggers)
        self.assertEqual(len(groups),1)
        prior={'attempts':[{'attempt_id':row['attempt_id'],'agent_verdict':'fail'} for row in records],
               'review_protocol':{}}
        layer={name:{'status':'verified','evidence':'shared '+name} for name in LAYERS}
        row={'group_id':groups[0]['group_id'],'attempt_ids':groups[0]['attempt_ids'],
             'layers':layer,'same_class_rationale':'same provider route and error','root_cause':'same tool error',
             'corrective_action':'same bounded retry policy','status':'no-defect','evaluation_defect':False,
             'correction_applied':False,'rerun_required':False,'rerun_attempt_ids':[],
             'rerun_reason':'no correction'}
        merged=merge_group_resolutions(prior,groups,{'groups':[row]},triggers)
        self.assertEqual({x['attempt_id'] for x in merged['attempts'] if x.get('anomaly_review')},
                         {'p__a','p__b'})
        with self.assertRaisesRegex(ValueError,'attempt scope'):
            validate_group_batch(groups,{'groups':[{**row,'attempt_ids':['p__a']}]})

    def test_cache_survives_batch_reordering_but_rejects_changed_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            current=[{'group_id':'anomaly-group-009','attempt_ids':['p__a']},
                     {'group_id':'anomaly-group-010','attempt_ids':['p__b']}]
            layer={name:{'status':'verified','evidence':'checked'} for name in LAYERS}
            row={'group_id':'anomaly-group-001','attempt_ids':['p__a'],'layers':layer,
                 'same_class_rationale':'same route','root_cause':'none',
                 'corrective_action':'none','status':'no-defect',
                 'evaluation_defect':False,'correction_applied':False,
                 'rerun_required':False,'rerun_attempt_ids':[],'rerun_reason':'none'}
            (root/'anomaly-batch-001.json').write_text(json.dumps({'groups':[row]}))
            stale={**row,'group_id':'anomaly-group-002','attempt_ids':['p__b','p__c']}
            (root/'anomaly-batch-002.json').write_text(json.dumps({'groups':[stale]}))
            reused,pending=cached_group_resolutions(root,current)
            self.assertEqual([(x['group_id'],x['attempt_ids']) for x in reused],
                             [('anomaly-group-009',['p__a'])])
            self.assertEqual(pending,[current[1]])
