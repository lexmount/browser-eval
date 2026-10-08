import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from harness.web_fetch.assembly import (
    _copy_speed_bundle,
    _project_provider_subset,
    _prune_shadowed_batch_attempts,
    _selected_record,
    assemble,
    bind_assembled_scoring_revision,
    bind_missing_assembled_scoring_gap,
    copy_bound_review,
    copy_projected_review,
    copy_scoring_revision_history,
    read_verified_batches,
)
from harness.web_fetch.evidence import certify_case
from harness.web_fetch.reference_lifecycle import (
    preflight_reference_checks,
    require_verified_reference_checks,
    sha256,
    valid_scoring_revision,
)
from harness.web_fetch.report_explainer import (
    _article_speed_population,
    _digest,
    _headless_article_input,
    _render_article_resources,
    headless_evidence,
    render_explainer,
)
from harness.web_fetch.report_i18n import localize_html
from harness.web_fetch.report_resources import GROUPS, render_resource_comparison
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
from harness.web_fetch.speed_cohort import build_summary as build_speed_summary


class BrowserCohortRegistryTests(unittest.TestCase):
    def test_generic_method_name_uses_observed_version_without_an_old_release_label(self):
        from harness.web_fetch.methods import method_page_model
        from harness.web_fetch.report_methods import display_name
        for version in ('1.1.15','1.2.0'):
            records=[{'provider_type':'moli_webfetch','provider_metadata':{'reported_version':'moli '+version}}]
            model=method_page_model('moli',display_name('moli'),records)
            self.assertIn('Moli '+version,model['title'])
            self.assertNotIn('1.1.5',model['title'])


    def test_task_specific_moli_adapter_keeps_browser_report_routes(self):
        from harness.web_fetch.report import is_headless_cohort
        from harness.web_fetch.report_explainer import _is_headless_comparison, supports_authored_explainer
        records = [{'provider_type': 'moli_webfetch'}, {'provider_type': 'headless_markdown'}]
        self.assertTrue(is_headless_cohort(records))
        self.assertTrue(_is_headless_comparison(records))
        self.assertFalse(supports_authored_explainer({'risk': {'track': 'risk_control'}}))
        for unrelated in ([], [{'provider_type': 'moli_webfetch'}],
                          records + [{'provider_type': 'firecrawl'}]):
            self.assertFalse(is_headless_cohort(unrelated))
            self.assertFalse(_is_headless_comparison(unrelated))


class HeadlessReportTests(unittest.TestCase):
    def test_resource_cards_name_each_actual_metric_winner(self):
        providers = ['moli', 'lightpanda', 'obscura', 'chromium']
        keys = ('cpu_ms', 'cpu_average_utilization_pct', 'cpu_sampled_utilization_p95_pct',
                'memory_mean_bytes', 'memory_p95_bytes', 'memory_peak_bytes',
                'memory_mib_seconds', 'network_rx_bytes', 'network_tx_bytes',
                'network_rx_bytes_per_second', 'network_tx_bytes_per_second')
        rows = [{'provider': provider, 'metrics': {
            key: {'mean': value, 'p50': value, 'p95': value} for key in keys}}
            for provider, value in zip(providers, (1, 10, 10, 10))]
        rows[1]['metrics']['cpu_ms']['mean'] = 0.5
        rows[2]['metrics']['memory_peak_bytes']['mean'] = 0.5
        rows[3]['metrics']['network_rx_bytes']['mean'] = 0.5
        rows[1]['metrics']['network_tx_bytes']['mean'] = 0.5
        summary = {'rows': rows, 'common_pass_cases': ['a'], 'planned_cases': ['a']}
        page, result = render_resource_comparison(summary, providers)
        self.assertEqual((result['winner'], result['winner_count']), ('moli', 8))
        self.assertIn('<span>CPU 领先者</span><strong>Lightpanda</strong>', page)
        self.assertIn('<span>内存领先者</span><strong>Obscura</strong>', page)
        self.assertIn('<span>网络流量领先者</span><strong>接收 Chromium / 发送 Lightpanda</strong>', page)
        english = localize_html(page, PurePosixPath('guide/index.html'))
        self.assertIn('Received Chromium / sent Lightpanda', english)
        self.assertNotIn('接收 Chromium', english)

    def test_resource_co_leaders_are_named_in_both_languages(self):
        providers = ['moli', 'lightpanda', 'obscura', 'chromium']
        keys = {key for _, metrics in GROUPS for _, key, _, _, _ in metrics}
        summary = {'rows': [
            {'provider': provider, 'metrics': {
                key: {'mean': 1, 'p50': 1, 'p95': 1} for key in keys}}
            for provider in providers],
            'common_pass_cases': ['a'], 'planned_cases': ['a'],
        }
        page, result = render_resource_comparison(summary, providers)
        self.assertTrue(result['winner_tied'])
        english = localize_html(page, PurePosixPath('guide/index.html'))
        self.assertIn('Resource-efficiency co-leaders in this run', english)
        self.assertIn('Moli, Lightpanda, Obscura, Chromium', english)
        self.assertNotIn('资源效率并列领先', english)

    def test_resource_table_excludes_unobservable_remote_provider(self):
        selected = ['moli', 'chromium', 'lightpanda', 'obscura', 'kitesurf']
        rows = []
        for index, provider in enumerate(selected[:-1], 1):
            metrics = {
                'cpu_ms': {'mean': index * 10, 'p50': index * 9, 'p95': index * 12},
                'cpu_average_utilization_pct': {'mean': index * 2},
                'cpu_sampled_utilization_p95_pct': {'mean': index * 3},
                'memory_mean_bytes': {'mean': index * 524288},
                'memory_p95_bytes': {'mean': index * 786432},
                'memory_peak_bytes': {'mean': index * 1048576, 'p95': index * 2097152},
                'memory_mib_seconds': {'mean': index * 2},
                'network_rx_bytes': {'mean': index * 1048576, 'p95': index * 2097152},
                'network_tx_bytes': {'mean': index * 1024, 'p95': index * 2048},
                'network_rx_bytes_per_second': {'mean': index * 4096},
                'network_tx_bytes_per_second': {'mean': index * 2048},
            }
            rows.append({'provider': provider, 'metrics': metrics})
        summary = {'rows': rows, 'common_pass_cases': ['a'] * 8,
                   'planned_cases': ['a'] * 20}
        page, interpretation = _render_article_resources(summary, selected)
        self.assertNotIn('<th scope="col">Kitesurf</th>', page)
        self.assertIn('<strong>8/20</strong> 个普通网页', page)
        self.assertIn('本轮资源效率领先', page)
        self.assertIn('data-resource-winner="moli"', page)
        self.assertEqual(page.count('data-resource-metric-winner="moli"'), 12)
        self.assertIn('CPU 与计算', page)
        self.assertIn('内存', page)
        self.assertIn('网络', page)
        self.assertIn('资源效率领先者', interpretation)
        self.assertIn('不等于综合任务通过率领先', interpretation)
    def test_provider_replacement_requires_prior_selected_record(self):
        selected=[({'case_id':'a','provider':'keep'},Path('/evidence'))]
        with self.assertRaisesRegex(ValueError,'no prior selected record: a/missing'):
            _selected_record(selected,('a','missing'))

    def test_two_provider_projection_keeps_complete_matrix_and_prunes_metadata(self):
        providers=['keep','lexmount-global','lexmount-cn']
        dataset={'comparison':{},'cases':[{'id':'a','track':'web'},{'id':'b','track':'web'}],
                 'coverage_plan':{'web':providers}}
        records=[{'attempt_id':f'{provider}__{case}__r001','provider':provider,
                  'case_id':case,'started_at':'2026-09-01T00:00:01+00:00',
                  'completed_at':'2026-09-01T00:00:02+00:00','elapsed_ms':1000}
                 for provider in providers for case in ('a','b')]
        batches=[{'started_at':'2026-09-01T00:00:00+00:00',
                  'completed_at':'2026-09-01T00:00:03+00:00',
                  'attempt_ids':[row['attempt_id'] for row in records],
                  'attempt_count':len(records),'providers':[{'name':name} for name in providers]}]
        audit=[{'provider':'lexmount-cn','excluded_attempts':[]},
               {'case_id':'a','excluded_attempts':[{'attempt_id':'lexmount-global__a__r001'},
                                                    {'attempt_id':'keep__a__r001'}]}]
        projected,rows,execution,selection=_project_provider_subset(
            dataset,records,batches,audit,['lexmount-global','lexmount-cn'])
        self.assertEqual(projected['coverage_plan'],{'web':['keep']})
        self.assertEqual({(row['case_id'],row['provider']) for row in rows},
                         {('a','keep'),('b','keep')})
        self.assertEqual(execution[0]['providers'],[{'name':'keep'}])
        self.assertEqual(execution[0]['attempt_count'],2)
        self.assertEqual(selection,[{'case_id':'a','excluded_attempts':[
            {'attempt_id':'keep__a__r001'}]}])

    def test_provider_projection_fails_closed_for_unknown_or_incomplete_scope(self):
        dataset={'comparison':{},'cases':[{'id':'a','track':'web'},{'id':'b','track':'web'}],
                 'coverage_plan':{'web':['keep','lexmount-global','lexmount-cn']}}
        records=[{'attempt_id':f'{provider}__{case}__r001','provider':provider,'case_id':case}
                 for provider in dataset['coverage_plan']['web'] for case in ('a','b')]
        with self.assertRaisesRegex(ValueError,'absent from the declared matrix'):
            _project_provider_subset(dataset,records,[],[],['missing'])
        with self.assertRaisesRegex(ValueError,'complete case/provider matrix'):
            _project_provider_subset(dataset,[row for row in records
                if not (row['provider']=='keep' and row['case_id']=='b')],[],[],
                                     ['lexmount-global','lexmount-cn'])

    def test_two_provider_projection_filters_and_rebinds_full_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';output=root/'output';source.mkdir();output.mkdir()
            providers=['keep','lexmount-global','lexmount-cn']
            dataset={'comparison':{'require_independent_result_review':True},
                     'cases':[{'id':'a','track':'web','task_kind':'body'}],
                     'coverage_plan':{'web':providers}}
            records=[{'attempt_id':f'{provider}__a__r001','provider':provider,
                      'case_id':'a','ok':True,'metrics':{'content_pass':True}}
                     for provider in providers]
            (source/'dataset.json').write_text(json.dumps(dataset))
            (source/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
            review={'schema_version':1,'bindings':{
                'dataset_sha256':hashlib.sha256((source/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()},
                'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':row['attempt_id'],'machine_verdict':'pass',
                             'agent_verdict':'pass','final_verdict':'pass'} for row in records]}
            (source/'result-review.json').write_text(json.dumps(review))
            projected=deepcopy(dataset);projected['coverage_plan']['web']=['keep']
            projected['comparison']['excluded_providers']=['lexmount-cn','lexmount-global']
            (output/'dataset.json').write_text(json.dumps(projected))
            (output/'results.jsonl').write_text(json.dumps(records[0])+'\n')
            self.assertTrue(copy_projected_review(
                source,output,['lexmount-global','lexmount-cn']))
            rebound=json.loads((output/'result-review.json').read_text())
            self.assertEqual([row['attempt_id'] for row in rebound['attempts']],['keep__a__r001'])
            self.assertEqual(rebound['reviewed_attempts'],1)
            self.assertEqual(rebound['total_attempts'],1)

    def test_speed_projection_recomputes_provider_statistics_and_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';target=root/'target';source.mkdir()
            providers=['keep','lexmount-global','lexmount-cn']
            dataset={'comparison':{},'cases':[{'id':'a','track':'web'},{'id':'b','track':'web'}],
                     'coverage_plan':{'web':providers}}
            records=[];batches=[]
            for index,provider in enumerate(providers):
                attempts=[]
                for case in ('a','b'):
                    payload=f'{provider}-{case}'.encode();relative=f'artifacts/{provider}/{case}.txt'
                    path=source/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
                    attempt=f'{provider}__{case}__r001';attempts.append(attempt)
                    records.append({'attempt_id':attempt,'provider':provider,'case_id':case,'run':1,
                        'artifact_path':relative,'ok':True,'elapsed_ms':10+index,
                        'started_at':'2026-09-01T00:00:01+00:00',
                        'completed_at':'2026-09-01T00:00:02+00:00',
                        'metrics':{'content_pass':True,'content_sha256':hashlib.sha256(payload).hexdigest()}})
                batches.append({'started_at':'2026-09-01T00:00:00+00:00',
                    'completed_at':'2026-09-01T00:00:10+00:00','attempt_ids':attempts,
                    'attempt_count':2,'providers':[{'name':provider}]})
            aggregate={'started_at':'2026-09-01T00:00:00+00:00',
                'completed_at':'2026-09-01T00:00:10+00:00','providers':providers,
                'case_ids':['a','b'],'batch_count':3}
            (source/'dataset.json').write_text(json.dumps(dataset))
            (source/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
            (source/'execution.json').write_text(json.dumps({'schema_version':1,
                                                              'aggregate':aggregate,'batches':batches}))
            summary=build_speed_summary(dataset,records,batches,aggregate)
            summary['bindings']={'dataset_sha256':hashlib.sha256((source/'dataset.json').read_bytes()).hexdigest(),
                                 'results_sha256':hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()}
            (source/'speed-comparison.json').write_text(json.dumps(summary))
            with patch('harness.web_fetch.assembly.require_verified_reference_checks'):
                _copy_speed_bundle(source,target,dataset,records,batches,
                                   excluded_providers=['lexmount-global','lexmount-cn'])
            projected=json.loads((target/'speed-comparison.json').read_text())
            self.assertEqual(projected['providers'],['keep'])
            self.assertEqual([row['provider'] for row in projected['rows']],['keep'])
            self.assertEqual(json.loads((target/'dataset.json').read_text())['coverage_plan'],
                             {'web':['keep']})
            self.assertEqual(len((target/'results.jsonl').read_text().splitlines()),2)

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
        local = record | {'provider_metadata': record['provider_metadata'] | {
            'local_report_complete_diagnostic_links': True}}
        self.assertEqual(binding(cases, local), expected)
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
                      'input_dataset_sha256':'a'*64,'input_results_sha256':'b'*64,
                      'output_dataset_sha256':'c'*64,'output_results_sha256':'d'*64,
                      'changed_cases':[],'task_decision_changes':[]}]
            (root/'scoring-revisions.json').write_text(json.dumps(history))
            self.assertTrue(bind_assembled_scoring_revision(root))
            self.assertTrue(valid_scoring_revision(root))
            bound=json.loads((root/'scoring-revisions.json').read_text())[-1]
            self.assertEqual(bound['input_results_sha256'],'d'*64)
            self.assertFalse(bind_assembled_scoring_revision(root))
            history=json.loads((root/'scoring-revisions.json').read_text())
            history[0]['changed_cases']=[{'case_id':'tampered','fields':[]}]
            (root/'scoring-revisions.json').write_text(json.dumps(history))
            self.assertFalse(valid_scoring_revision(root))

    def test_late_full_review_can_bind_one_missing_assembly_edge(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            dataset={'comparison':{'require_independent_result_review':True},
                     'cases':[{'id':'body','task_kind':'body'}]}
            record={'attempt_id':'p__body__r001','provider':'p','case_id':'body','ok':True,
                    'metrics':{'content_pass':True}}
            (root/'dataset.json').write_text(json.dumps(dataset))
            (root/'results.jsonl').write_text(json.dumps(record)+'\n')
            current_bindings={
                'dataset_sha256':hashlib.sha256((root/'dataset.json').read_bytes()).hexdigest(),
                'results_sha256':hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest()}
            current_review={'schema_version':1,'bindings':current_bindings,
                'review_protocol':{'machine_judgments_withheld':True,'scope':'all'},
                'attempts':[{'attempt_id':record['attempt_id'],'machine_verdict':'pass',
                             'agent_verdict':'pass','final_verdict':'pass'}]}
            (root/'result-review.json').write_text(json.dumps(current_review))
            gap_target={'dataset_sha256':'e'*64,'results_sha256':'f'*64}
            staged=current_review | {
                'bindings':gap_target,'unresolved':0,'accepted_unreviewed_passes':0}
            staged_path=root/'assembly-review.json';staged_path.write_text(json.dumps(staged))
            history=[
                {'kind':'audited_scoring_rule_revision','provider_calls':0,
                 'input_dataset_sha256':'a'*64,'input_results_sha256':'b'*64,
                 'output_dataset_sha256':'c'*64,'output_results_sha256':'d'*64,
                 'changed_cases':[],'task_decision_changes':[]},
                {'kind':'audited_scoring_rule_revision','provider_calls':0,
                 'input_dataset_sha256':'e'*64,'input_results_sha256':'f'*64,
                 'output_dataset_sha256':current_bindings['dataset_sha256'],
                 'output_results_sha256':current_bindings['results_sha256'],
                 'changed_cases':[],'task_decision_changes':[]},
            ]
            (root/'scoring-revisions.json').write_text(json.dumps(history))
            self.assertTrue(bind_missing_assembled_scoring_gap(root,staged_path))
            self.assertTrue(valid_scoring_revision(root))
            repaired=json.loads((root/'scoring-revisions.json').read_text())
            self.assertEqual(repaired[1]['kind'],'audited_assembled_scoring_revision')
            self.assertEqual(repaired[1]['output_results_sha256'],'f'*64)

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

    def test_scoring_revision_copy_includes_hash_bound_review_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';output=root/'output'
            source.mkdir();output.mkdir()
            payload=b'{"review":"prior"}\n'
            relative='review-history/result-review-old.json'
            archive=source/relative;archive.parent.mkdir();archive.write_bytes(payload)
            receipts=[{'kind':'audited_body_scoring_revision',
                       'prior_result_review_path':relative,
                       'prior_result_review_sha256':hashlib.sha256(payload).hexdigest()}]
            (source/'scoring-revisions.json').write_text(json.dumps(receipts))
            self.assertTrue(copy_scoring_revision_history(source,output))
            self.assertEqual((output/relative).read_bytes(),payload)
            archive.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'hash does not match'):
                copy_scoring_revision_history(source,root/'second')

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
            with patch('harness.web_fetch.assembly.require_verified_reference_checks') as verify:
                self.assertEqual(read_verified_batches(root,{'cases':[]},[]),batches)
                verify.assert_called_once_with(batches,cases={'cases':[]},records=[],source=root,
                    equivalent_capture_bindings=False)

    def test_speed_bundle_copies_only_explicitly_bound_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';target=root/'target';source.mkdir()
            artifact=b'public result';private=b'private response'
            artifact_path='artifacts/p/body/run-001.txt'
            private_path='private_artifacts/p/body/run-001.response.bin'
            for relative,payload in ((artifact_path,artifact),(private_path,private)):
                path=source/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(payload)
            row={'attempt_id':'p__body__r001','provider':'p','case_id':'body',
                 'artifact_path':artifact_path,
                 'metrics':{'content_sha256':hashlib.sha256(artifact).hexdigest()},
                 'provider_metadata':{
                     'raw_response_path':private_path,
                     'raw_response_sha256':hashlib.sha256(private).hexdigest(),
                     'raw_response_bytes':len(private)}}
            dataset={'cases':[{'id':'body'}]}
            observation=b'{}'
            observation_hash=hashlib.sha256(observation).hexdigest()
            observation_path=f'reference-checks/observations-{observation_hash}.json'
            batches=[{'reference_checks':{'postcheck':{
                'evidence_path':observation_path,'evidence_sha256':observation_hash}}}]
            required={
                'dataset.json':json.dumps(dataset).encode(),
                'results.jsonl':(json.dumps(row)+'\n').encode(),
                'execution.json':json.dumps({'batches':batches}).encode(),
                'speed-comparison.json':b'{}',
                'result-review.json':b'{}',
                'scoring-revisions.json':b'[]',
                observation_path:observation,
                'sources/body/source.txt':b'independent source',
                'list-references/body.json':b'{}',
            }
            for relative,payload in required.items():
                path=source/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(payload)
            secret=source/'worker-secrets/session-token.txt'
            secret.parent.mkdir();secret.write_text('must not copy')
            (source/'reference-checks/private-note.txt').write_text('synthetic private note')

            _copy_speed_bundle(source,target,dataset,[row],batches)

            for relative in (*required,artifact_path,private_path):
                self.assertTrue((target/relative).is_file(),relative)
            self.assertFalse((target/'reference-checks/private-note.txt').exists())
            self.assertFalse((target/'worker-secrets').exists())

    def test_speed_bundle_rejects_any_symlink_even_when_unreferenced(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';target=root/'target';source.mkdir()
            outside=root/'secret';outside.write_text('secret')
            (source/'dataset.json').write_text('{"cases": []}')
            (source/'results.jsonl').write_text('')
            (source/'execution.json').write_text('{"batches": []}')
            (source/'speed-comparison.json').write_text('{}')
            (source/'unreferenced-link').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'contains a symlink'):
                _copy_speed_bundle(source,target,{'cases':[]},[],[])
            self.assertFalse(target.exists())

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
            with self.assertRaisesRegex(ValueError, 'requires speed evidence'):
                render_explainer(Path(temp), cases, records)
            self.assertFalse((Path(temp) / 'guide/article.html').exists())

    def prepare(self,root):
        now=datetime.now(timezone.utc)
        case={'id':'body','name':'Body','url':'https://example.test/body','track':'web',
            'task_kind':'body','ground_truth_main_content':'Reference body',
            'source_verification':{'state':'valid','reference_mode':'live_exact','method':'independent fixture',
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
            'metrics':{'reference_sha256':sha256('Reference body'),
                       'scoring_source_sha256':SCORING_SOURCE_SHA256}}
        observation={'case_id':'body','independent':True,'content_text':'Reference body',
            'checked_at':(now-timedelta(seconds=1)).isoformat(),
            'scope_identity':checks['cases']['body']['scope_identity'],
            'completion':{'satisfied':True,'elapsed_ms':100,'timeout_ms':1000}}
        (root/'results.jsonl').write_text(json.dumps(record)+'\n')
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

class CurrentBrowserArticleTests(unittest.TestCase):
    def example(self, root):
        from harness.web_fetch.report_explainer import _digest, _headless_article_input
        cases={cid:{'id':cid,'name':cid,'track':track,'url':'https://example.com/'+cid,
                    'ground_truth_main_content':'Expected complete body'}
               for cid,track in [('web-case','web'),('risk-case','risk_control')]}
        rows=[]
        for name,values in [('moli',[(True,10),(False,30)]),('lightpanda',[(True,20),(True,40)])]:
            for (cid,case),(passed,ms) in zip(cases.items(),values):
                attempt=name+'__'+cid
                artifact='artifacts/'+attempt+'.txt'
                (root/artifact).parent.mkdir(exist_ok=True)
                (root/artifact).write_text('Expected complete body' if passed else 'Access denied')
                (root/'attempts').mkdir(exist_ok=True)
                (root/'attempts'/f'{attempt}.html').write_text('<p>Canonical result and error</p>')
                rows.append({'provider':name,'provider_type':'headless_markdown','case_id':cid,
                             'attempt_id':attempt,'artifact_path':artifact,'ok':passed,'elapsed_ms':ms,
                             'started_at':'2026-10-09T00:00:00Z','provider_metadata':{},
                             'error_type':None if passed else 'http_status',
                             'error_message':None if passed else 'Origin returned HTTP 403',
                             'metrics':{'content_pass':passed}})
        content={'schema':'webfetch_browser_interpretation/1',
                 'evidence_sha256':_digest(_headless_article_input(cases,rows,['moli','lightpanda'])),
                 'title':'浏览器结果解读','intro':'相同任务和规则下的测量。',
                 'scoring':'正文和最终页面地址都属于验收范围。','conclusions':'结合正文与交付结果选择方法。',
                 'providers':{name:{'explanation':'这一节解释实际返回的内容。',
                                    'specimen_case_id':'risk-case'} for name in ['moli','lightpanda']}}
        p=root/'report-assets/content.json';p.parent.mkdir();p.write_text(json.dumps(content,ensure_ascii=False))
        binding={'path':'report-assets/content.json','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        return cases,rows,binding

    def test_new_cohort_has_dynamic_families_populations_and_evidence(self):
        from harness.web_fetch.report_explainer import render_explainer, check_current_browser_article
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            body=render_explainer(root,cases,rows,content_binding=binding)
            (root/'guide/index.html').write_text(body)
            model=check_current_browser_article(root,cases,rows,binding)
            self.assertEqual(model['cases'],2)
            self.assertEqual(model['stats']['moli']['pass_rate'],0.5)
            self.assertEqual(model['stats']['moli']['all']['mean'],20)
            self.assertEqual(model['stats']['moli']['successful']['mean'],10)
            self.assertEqual(model['stats']['lightpanda']['successful']['n'],2)
            self.assertIn('风控网站正文',body)
            self.assertIn('Origin returned HTTP 403',body)
            self.assertIn('独立结果审查：0/4',body)
            self.assertNotIn('150 道',body)
            self.assertNotIn('1.1.5',body)

    def test_release_rejects_wrong_highlights_values_and_missing_evidence(self):
        from harness.web_fetch.report_explainer import render_explainer, check_current_browser_article
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            body=render_explainer(root,cases,rows,content_binding=binding)
            page=root/'guide/index.html';page.write_text(body)
            changes=[body.replace(' data-winner="true"','',1),
                     body.replace('data-value="0.5"','data-value="0.9"',1),
                     body.replace('data-browser-error="moli"','data-removed="moli"',1),
                     body.replace('../attempts/moli__risk-case.html','../attempts/absent.html',1),
                     body.replace('data-count="1"','data-count="9"',1),
                     body.replace('data-browser-interface="moli"','data-removed-interface="moli"',1),
                     body.replace('data-case-count="1"','data-case-count="9"',1),
                     body.replace('CLI fetch --dump markdown → 内置转换 → Markdown','共同转换 → Markdown',1),
                     body.replace('等待：CLI 等待 done','等待：不等待',1)]
            for changed in changes:
                with self.subTest(change=changes.index(changed)):
                    page.write_text(changed)
                    with self.assertRaises(ValueError):check_current_browser_article(root,cases,rows,binding)
            page.write_text(body)
            (root/'attempts/moli__risk-case.html').unlink()
            with self.assertRaisesRegex(ValueError,'target is broken'):
                check_current_browser_article(root,cases,rows,binding)

    def test_new_content_cannot_hide_incomplete_matrix_or_changed_measurements(self):
        from harness.web_fetch.report_explainer import render_explainer, _digest, _headless_article_input
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            changed=deepcopy(rows);changed[0]['elapsed_ms']=100
            with self.assertRaisesRegex(ValueError,'evidence changed'):
                render_explainer(root,cases,changed,content_binding=binding)
            p=root/binding['path'];content=json.loads(p.read_text())
            content['evidence_sha256']=_digest(_headless_article_input(cases,rows[:-1],['moli','lightpanda']))
            p.write_text(json.dumps(content));binding['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'complete declared matrix'):
                render_explainer(root,cases,rows[:-1],content_binding=binding)

    def test_public_builder_materializes_bound_prose_and_preserves_scores(self):
        from harness.web_fetch.config import load_dataset
        from harness.web_fetch.providers.fixture import FixtureProvider
        from harness.web_fetch.runner import run_evaluation
        from harness.web_fetch.report_build import build_report
        from harness.web_fetch.report_explainer import _digest, _headless_article_input, check_current_browser_article
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'evidence';report=root/'report'
            dataset={'schema_version':1,'id':'synthetic-browser-cohort','name':'Synthetic cohort',
                     'tool':'web_fetch','cases':[
                         {'id':cid,'name':cid+'\u2028正文\u0085保留','category':'test','track':track,
                          'url':'https://example.com/'+cid,'expected_any':['Expected body'],
                          'required_facts':[{'id':'body','label':'正文','alternatives':['Expected body']}],
                          'source_verification':{'selector':'main'},
                          'source_benchmark':{'stable_annotation':{
                              'anchor_evidence':'sources/retired-qualification.txt',
                              'anchor_evidence_sha256':'0'*64}}}
                         for cid,track in [('web-case','web'),('risk-case','risk_control')]],
                     'coverage_plan':{'web':['moli','lightpanda'],'risk_control':['moli','lightpanda']},
                     'comparison':{'kind':'synthetic_browser_report','require_independent_result_review':False,
                         'timeout_seconds':5,'runs_per_case':1,'concurrency':1,'conversion':'offline fixture',
                         'reference_binding_mode':'frozen_task_contracts','publication_state':'verified',
                         'unverified_reference_cases':[]}}
            input_path=root/'dataset.json';input_path.write_text(json.dumps(dataset))
            configuration=root/'providers.json';configuration.write_text('{}')
            metadata,cases=load_dataset(input_path)
            rows,_=run_evaluation(output_dir=source,dataset_meta=metadata,dataset_path=input_path,
                                 cases=cases,providers=[FixtureProvider('moli',{'type':'fixture','fail_cases':['risk-case']}),
                                                       FixtureProvider('lightpanda',{'type':'fixture'})],
                                 provider_config_path=configuration,runs=1,timeout=5,delay=0)
            measured=json.loads((source/'dataset.json').read_text());case_map={c['id']:c for c in measured['cases']}
            content={'schema':'webfetch_browser_interpretation/1',
                     'evidence_sha256':_digest(_headless_article_input(case_map,rows,['moli','lightpanda'])),
                     'title':'新一轮浏览器结果','intro':'统一规则的合成测试。',
                     'scoring':'正文必须满足题目要求。','conclusions':'完整比较所有调用。',
                     'providers':{name:{'specimen_case_id':'risk-case','explanation':'展示实际调用及程序验收结果。'} for name in ['moli','lightpanda']}}
            content_path=source/'report-assets/content.json';content_path.parent.mkdir();content_path.write_text(json.dumps(content,ensure_ascii=False))
            binding={'path':'report-assets/content.json','sha256':hashlib.sha256(content_path.read_bytes()).hexdigest()}
            provenance=source/'implementation-provenance.json'
            provenance.write_text(json.dumps({'schema_version':1,'implementations':[
                {'provider':'moli','binary_sha256':'synthetic','version':'synthetic'}]}))
            measured['comparison']['implementation_provenance']={
                'path':'implementation-provenance.json','sha256':hashlib.sha256(provenance.read_bytes()).hexdigest()}
            measured['comparison']['report_interpretation']=binding
            (source/'dataset.json').write_text(json.dumps(measured))
            before=(source/'results.jsonl').read_bytes()
            build_report(source,report)
            self.assertEqual((report/'results.jsonl').read_bytes(),before)
            analysis=(report/'analysis/index.html').read_text()
            self.assertNotIn('已核验判定',analysis,
                             'A machine-only cohort must not claim independently reviewed verdicts')
            self.assertIn('程序判定',analysis)
            self.assertEqual((report/binding['path']).read_bytes(),content_path.read_bytes())
            self.assertEqual((report/'implementation-provenance.json').read_bytes(),provenance.read_bytes())
            check_current_browser_article(report,case_map,rows,binding)
            from bs4 import BeautifulSoup
            offline=BeautifulSoup((report/'guide/article.html').read_text(),'html.parser')
            self.assertFalse(offline.select('script,link[rel="stylesheet"]'))
            for anchor in offline.select('a[href]'):
                self.assertTrue(anchor['href'].startswith(('http://','https://','#')))
                if anchor['href'].startswith('#'):
                    self.assertIsNotNone(offline.find(id=anchor['href'][1:]))
            for row in rows:
                if row['case_id']=='risk-case':
                    detail=offline.find(id='offline-result-'+row['attempt_id'])
                    self.assertIsNotNone(detail)
                    self.assertIn(row['attempt_id'],detail.get_text())
            self.assertIn('新一轮浏览器结果',(report/'index.html').read_text())
            home=(report/'index.html').read_text()
            self.assertLess(home.index('id="overall-ranking"'),home.index('id="overview"'))
            self.assertNotIn('达标率',(report/'datasets/index.html').read_text())
            self.assertNotIn('全部方法执行结果',(report/'datasets/risk-case.html').read_text())
            self.assertIn('../analysis/index.html#inspect?query=risk-case',(report/'datasets/risk-case.html').read_text())
            self.assertIn('查看各方法执行结果',(report/'datasets/risk-case.html').read_text())
            self.assertNotIn('其余六种方法',(report/'accounts/index.html').read_text())
            self.assertTrue('Moli 1.1.1 的普通网页' not in (report/'adoption/index.html').read_text(),'Measured-method introduction retained an old generic version')

            # Exercise the published CLI and release gate with a compact profile,
            # without duplicating complete task/reference content into Git.
            import subprocess, sys
            from harness.web_fetch.storage import frozen_input_bindings
            manifest=json.loads((source/'manifest.json').read_text())
            (source/'execution.json').write_text(json.dumps({'batches':[manifest]}))
            profile={'schema_version':1,'dataset_id':measured['id'],
                     'comparison_kind':measured['comparison']['kind'],'case_count':2,
                     'case_ids_sha256':hashlib.sha256(json.dumps([c['id'] for c in measured['cases']],
                         separators=(',',':'),ensure_ascii=True).encode()).hexdigest(),
                     'providers':['moli','lightpanda'],
                     **{k:v for k,v in measured['comparison'].items() if k!='kind'},
                     'case_inventory':{track:[c['id'] for c in measured['cases'] if c['track']==track]
                                       for track in ['web','risk_control']},
                     'frozen_inputs':frozen_input_bindings(source)}
            repository=Path(__file__).resolve().parents[1]
            with tempfile.TemporaryDirectory(prefix='report-profile-test-',dir=repository/'evaluation') as profile_dir:
                profile_path=Path(profile_dir)/'current-report.json'
                measured['comparison']['reproduction_profile']=profile_path.relative_to(repository).as_posix()
                measured['comparison']['report_output']='reports/synthetic-browser'
                (source/'dataset.json').write_text(json.dumps(measured))
                profile['frozen_inputs']=frozen_input_bindings(source)
                profile_path.write_text(json.dumps(profile))
                cli_report=root/'cli-report'
                build=subprocess.run([sys.executable,'scripts/build_report.py','--profile',str(profile_path),
                                      '--evidence',str(source),'--output',str(cli_report)],
                                     cwd=repository,capture_output=True,text=True)
                self.assertEqual(build.returncode,0,build.stderr)
                gate=subprocess.run([sys.executable,'scripts/check_report.py',str(cli_report),
                                     '--profile',str(profile_path)],cwd=repository,capture_output=True,text=True)
                self.assertEqual(gate.returncode,0,gate.stderr+gate.stdout)
                revision_gate=subprocess.run([sys.executable,'scripts/check_report_revision.py',str(cli_report)],
                                             cwd=repository,capture_output=True,text=True)
                self.assertEqual(revision_gate.returncode,0,revision_gate.stderr+revision_gate.stdout)
                self.assertIn(measured['comparison']['reproduction_profile'],(cli_report/'reproduce/index.html').read_text())
                manifest_path=cli_report/'report-manifest.json';original_manifest=manifest_path.read_bytes()
                for mutation in ('renderer','dataset_sha256'):
                    manifest=json.loads(original_manifest)
                    if mutation=='renderer':manifest['template']['renderer']='copied_renderer.py'
                    else:manifest['dataset_sha256']='0'*64
                    manifest_path.write_text(json.dumps(manifest))
                    rejected=subprocess.run([sys.executable,'scripts/check_report.py',str(cli_report),
                                             '--profile',str(profile_path)],cwd=repository,capture_output=True,text=True)
                    self.assertNotEqual(rejected.returncode,0)
                manifest_path.write_bytes(original_manifest)
            content_path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'differs from binding'):
                build_report(source,root/'tampered-report')
            self.assertFalse((root/'tampered-report').exists())
