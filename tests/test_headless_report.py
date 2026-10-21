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
    _headless_article_input,
    _render_article_resources,
)
from harness.web_fetch.report_i18n import localize_html
from harness.record_io import canonical_json_sha256
from harness.web_fetch.report_resources import GROUPS, render_resource_comparison
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256
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
        records = [{'provider_type': 'moli_webfetch'}, {'provider_type': 'headless_markdown'}]
        self.assertTrue(is_headless_cohort(records))
        for unrelated in ([], [{'provider_type': 'moli_webfetch'}],
                          records + [{'provider_type': 'firecrawl'}]):
            self.assertFalse(is_headless_cohort(unrelated))


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
        self.assertIn('Moli 1.1.15 · independent resource cohort, Lightpanda, Obscura, Chromium', english)
        self.assertNotIn('资源效率并列领先', english)

    def test_resource_table_excludes_unobservable_remote_provider(self):
        resource_providers = ['moli', 'chromium', 'lightpanda', 'obscura']
        selected = ['moli-fixed', 'chromium', 'lightpanda', 'obscura', 'kitesurf']
        rows = []
        for index, provider in enumerate(resource_providers, 1):
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
        summary = {'providers': resource_providers, 'rows': rows, 'common_pass_cases': ['a'] * 8,
                   'planned_cases': ['a'] * 20}
        page, interpretation = _render_article_resources(summary, selected)
        self.assertNotIn('<th scope="col">Kitesurf</th>', page)
        self.assertIn('data-resource-population="all_declared_calls"', page)
        self.assertIn('全部 20 个普通网页，包含失败调用', page)
        self.assertIn('共同通过 <strong>8/20</strong>', page)
        self.assertIn('不用于选择资源表的样本', page)
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
            return canonical_json_sha256(_headless_article_input(cs, [row], ['moli']))
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
        from harness.web_fetch.report_explainer import _headless_article_input
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
                             'started_at':'2026-10-09T00:00:00Z','provider_metadata':(
                                 {'dns_mode':'public_doh','system_dns_fallback':False}
                                 if name=='moli' else {'dns_mode':'system'}),
                             'error_type':None if passed else 'http_status',
                             'error_message':None if passed else 'Origin returned HTTP 403',
                             'metrics':{'content_pass':passed}})
        content={'schema':'webfetch_browser_interpretation/1',
                 'evidence_sha256':canonical_json_sha256(_headless_article_input(cases,rows,['moli','lightpanda'])),
                 'title':'浏览器结果解读','intro':'相同任务和规则下的测量。',
                 'scoring':'正文是验收目标，最终页面地址只用于诊断。','conclusions':'结合正文与交付结果选择方法。',
                 'providers':{name:{'explanation':'这一节解释实际返回的内容。',
                                    'specimen_case_id':'risk-case'} for name in ['moli','lightpanda']}}
        p=root/'report-assets/content.json';p.parent.mkdir();p.write_text(json.dumps(content,ensure_ascii=False))
        binding={'path':'report-assets/content.json','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        return cases,rows,binding

    def bind_failure_review(self, root, cases, rows, binding):
        result_path = root/'results.jsonl'
        if not result_path.exists():
            result_path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        failed = next(row for row in rows if not row['ok'])
        artifact = root/failed['artifact_path']
        review = {'schema':'webfetch_failure_review/1',
                  'results_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),
                  'failures':[{'attempt_id':failed['attempt_id'],'provider':failed['provider'],
                               'case_id':failed['case_id'],'category':'source_access_denied',
                               'category_label':'源站拒绝访问',
                               'reason':'源站返回 HTTP 403，输出明确写明 Access denied。',
                               'remediation_status':'保留失败；评测端不绕过访问限制。',
                               'evidence_paths':[{'path':failed['artifact_path'],
                                  'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}]}
        path=root/'report-assets/failures.json';path.write_text(json.dumps(review,ensure_ascii=False))
        content_path=root/binding['path'];content=json.loads(content_path.read_text())
        content.update(require_failure_review=True, failure_review={
            'path':'report-assets/failures.json','sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        content['background']='网页正文与文件内容按各自交付要求验收。'
        import base64
        image=root/'report-assets/delivery.png'
        image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
        content['figures']=[{'path':'report-assets/delivery.png',
                            'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                            'alt':'同一输入分别取得正文或文件', 'caption':'不同任务检查不同的交付内容。'}]

        content['case_examples']=[{'provider':'lightpanda','case_id':'web-case',
                                  'explanation':'这个实例返回全部固定正文，满足相同验收要求。'}]
        content_path.write_text(json.dumps(content,ensure_ascii=False))
        binding['sha256']=hashlib.sha256(content_path.read_bytes()).hexdigest()
        return review

    def test_reference_reading_components_reject_missing_or_false_visuals(self):
        from bs4 import BeautifulSoup
        from harness.web_fetch.report_explainer import render_explainer,check_current_browser_article
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            self.bind_failure_review(root,cases,rows,binding)
            path=root/binding['path'];content=json.loads(path.read_text())
            content.update(focus_provider='lightpanda',comparison_provider='moli',version_baseline='moli',
                comparisons=[{'id':'complete-scope','title':'全部任务','tracks':['web','risk_control'],'left':'lightpanda','right':'moli'}],
                results_explanation='两种配置在相同任务上的完整结果。')
            for row in rows:
                row['metrics'].update(word_recall=0.8,word_precision=0.7,word_f1=0.75)
            content['evidence_sha256']=canonical_json_sha256(_headless_article_input(cases,rows,['moli','lightpanda']))
            (root/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            review_path=root/content['failure_review']['path'];review=json.loads(review_path.read_text())
            review['results_sha256']=hashlib.sha256((root/'results.jsonl').read_bytes()).hexdigest();review_path.write_text(json.dumps(review))
            content['failure_review']['sha256']=hashlib.sha256(review_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(content,ensure_ascii=False));binding['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            body=render_explainer(root,cases,rows,content_binding=binding);page=root/'guide/index.html';page.write_text(body)
            check_current_browser_article(root,cases,rows,binding)
            parsed=BeautifulSoup(body,'html.parser')
            self.assertEqual(len(parsed.select('[data-reading-speed]')),6)
            self.assertEqual([int(node['data-count']) for node in parsed.select('[data-version-relation]')],[1,1,0,0])
            self.assertEqual(len(parsed.select('[data-browser-metric^="quality_"]')),6)
            for label,selector,attribute,value in [
                ('opening','.m-hero','class','missing'),
                ('bar length','[data-reading-speed] .m-speed-track i','style','width:1%'),
                ('highlight','[data-reading-speed] .m-speed-track i','class','best'),
                ('case selection','.case-choice','id','missing-case'),
                ('case comparison','.case-compare','class','missing-compare'),
                ('verdict color','[data-example-check]','class','bad'),
                ('failure frequency','[data-reading-cause]','data-count','99'),
                ('version scope','[data-version-relation]','data-count','99'),
            ]:
                with self.subTest(label=label):
                    changed=BeautifulSoup(body,'html.parser');node=changed.select_one(selector);node[attribute]=value;page.write_text(str(changed))
                    with self.assertRaises(ValueError):check_current_browser_article(root,cases,rows,binding)
            page.write_text(body)

    def test_empty_response_has_zero_delivered_characters(self):
        from harness.web_fetch.report_explainer import browser_example_evidence
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,_=self.example(root);row=rows[0]
            (root/row['artifact_path']).write_bytes(b'')
            evidence=browser_example_evidence(root,cases[row['case_id']],row)
            self.assertEqual(evidence['actual_chars'],0)
            self.assertEqual(evidence['actual'],'本次没有返回内容。')

    def test_reference_excerpt_is_verbatim_and_never_accepts_an_invented_focus(self):
        from harness.web_fetch.report_explainer import browser_example_evidence
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,_=self.example(root);row=rows[0];case=cases[row['case_id']]
            original=case['ground_truth_main_content']
            evidence=browser_example_evidence(root,case,row,reference_focus='complete body')
            self.assertEqual(evidence['reference'],'complete body')
            self.assertEqual(case['ground_truth_main_content'],original)
            with self.assertRaises(ValueError):browser_example_evidence(root,case,row,reference_focus='invented abstract')

    def test_compact_interfaces_group_same_output_without_guessing_missing_records(self):
        from harness.web_fetch.report_interfaces import browser_task_interface_matrix
        cases={cid:dict(id=cid,url='https://example.com/'+cid,track='image',task_kind=kind)
               for cid,kind in [('address','resource'),('metadata','body'),('unknown','body')]}
        rows=[dict(provider='moli',case_id=cid,provider_metadata=(
            dict(extractor='moli_native_markdown',dump='markdown',route=cid)
            if cid!='unknown' else {})) for cid in cases]
        matrix=browser_task_interface_matrix(cases,['moli'],rows,compact=True)
        parts=matrix['rows'][0]['cells']['moli']
        self.assertEqual([(p['count'],p['description']) for p in parts],[(2,'原生 Markdown'),(1,'接口未记录')])

    def test_example_focus_is_verbatim_and_does_not_modify_saved_answer(self):
        from harness.web_fetch.report_explainer import browser_example_evidence
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root);row=rows[0]
            answer='Navigation\n'*900+'# Product body\nVisible article text'
            (root/row['artifact_path']).write_text(answer)
            evidence=browser_example_evidence(root,cases[row['case_id']],row,focus='# Product body')
            self.assertTrue(evidence['actual'].startswith('# Product body'))
            self.assertEqual((root/row['artifact_path']).read_text(),answer)
            with self.assertRaisesRegex(ValueError,'focus is absent'):
                browser_example_evidence(root,cases[row['case_id']],row,focus='Invented body')

    def test_failure_review_is_complete_current_and_independent_of_program_errors(self):
        from harness.web_fetch.report_explainer import render_explainer, check_current_browser_article
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            self.bind_failure_review(root,cases,rows,binding)
            body=render_explainer(root,cases,rows,content_binding=binding)
            (root/'guide/index.html').write_text(body)
            model=check_current_browser_article(root,cases,rows,binding)
            self.assertEqual(model['stats']['moli']['failure_counts'],{'http_status':1})
            self.assertEqual(model['stats']['moli']['reviewed_failure_counts'],{'source_access_denied':1})
            self.assertNotIn('data-reviewed-reason=',body)
            self.assertIn('../attempts/moli__risk-case.html',body)
            self.assertIn('程序返回分类',body)
            self.assertIn('03 测试方法与实例',body)
            self.assertIn('data-browser-figure="report-assets/delivery.png"',body)
            self.assertIn('05 失败任务与正文缺失',body)
            # Only the authored specimen embeds complete output in the independent edition.
            offline=(root/'guide/article.html').read_text()
            self.assertIn('offline-result-lightpanda__web-case',offline)
            self.assertIn('src="data:image/png;base64,',offline)
            from bs4 import BeautifulSoup
            document=BeautifulSoup(offline,'html.parser')
            for anchor in document.select('a[href^="#"]'):
                self.assertIsNotNone(document.find(id=anchor['href'][1:]))
            page=root/'guide/index.html'
            for changed in [body.replace('data-reviewed-failure="moli"','data-removed="moli"',1),
                            body.replace('id="reading-method"','id="missing-method"',1),
                            body.replace('data-browser-figure="report-assets/delivery.png"','data-removed-figure="x"',1),
                            body.replace('data-example-expected=','data-hidden-expected=',1),
                            body.replace('data-example-actual=','data-hidden-actual=',1),
                            body.replace('data-example-check=','data-hidden-check=',1),
                            body.replace('data-example-verdict=','data-hidden-verdict=',1),
                            body.replace('data-reading-contents=','data-hidden-contents=',1),
                            body.replace('P50 是中位数','P50 值',1)]:
                page.write_text(changed)
                with self.assertRaises(ValueError):
                    check_current_browser_article(root,cases,rows,binding)

    def test_interpretation_examples_use_saved_checks_and_do_not_decode_binary_files(self):
        from harness.web_fetch.report_explainer import browser_example_evidence
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            row=deepcopy(rows[0]);case=deepcopy(cases[row['case_id']])
            case['min_content_chars']=10
            case['body_requirements']={'minimum_coverage':0.9}
            row['metrics'].update(length_pass=True,content_chars=22,
                fact_results=[{'id':'body','alternatives':['Expected complete body'],'matched':['Expected complete body'],'pass':True}],
                body_region_results=[{'id':'opening','coverage':0.5,'pass':False}],body_coverage_pass=False,
                stable_anchor_results=[{'id':'heading','alternatives':['Expected'],'matched':['Expected'],'pass':True}],
                stable_field_results=[{'field':'name','count':2,'minimum':3,'pass':False}],
                stable_unique_record_count=2,stable_minimum_records=3,stable_structure_pass=False)
            evidence=browser_example_evidence(root,case,row)
            checks={item['key']:item for item in evidence['checks']}
            self.assertEqual(checks['length']['actual'],'22 字符')
            self.assertEqual(checks['fact_results_1']['verdict'],'通过')
            self.assertEqual(checks['body_region_results_1']['verdict'],'未通过')
            self.assertIn('50.0%',checks['body_region_results_1']['actual'])
            self.assertEqual(checks['stable_field_results_1']['expected'],'3')
            self.assertEqual(checks['records']['actual'],'2')
            # Direct file inspection uses retained file metadata even if no text artifact can be read.
            case['task_kind']='file';row['artifact_path']='must-not-read.bin'
            row['metrics']['raw_file_pass']=True
            row['provider_metadata']['file_evaluation']={'files':[{'mime':'video/mp4','bytes':12,'sha256':'abc','pass':True}]}
            evidence=browser_example_evidence(root,case,row)
            self.assertIn('video/mp4',evidence['actual'])
            self.assertIn('abc',evidence['actual'])
            self.assertTrue(evidence['passed'])
            case['task_kind']='body';row['artifact_path']=rows[0]['artifact_path']
            (root/row['artifact_path']).write_bytes(b'')
            self.assertEqual(browser_example_evidence(root,case,row)['actual'],'本次没有返回内容。')
            (root/row['artifact_path']).write_bytes(b'%PDF-1.7 binary data')
            self.assertIn('二进制响应',browser_example_evidence(root,case,row)['actual'])

    def test_failure_review_rejects_incomplete_stale_and_misattributed_diagnoses(self):
        from harness.web_fetch.report_explainer import current_browser_article_model
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            original=self.bind_failure_review(root,cases,rows,binding)
            content_path=root/binding['path'];content=json.loads(content_path.read_text())
            review_path=root/content['failure_review']['path']
            mutations=[lambda value:value.update(failures=[]),
                       lambda value:value['failures'].append(deepcopy(value['failures'][0])),
                       lambda value:value.update(results_sha256='0'*64),
                       lambda value:value['failures'][0].update(attempt_id=rows[0]['attempt_id'],case_id=rows[0]['case_id']),
                       lambda value:value['failures'][0].update(provider='lightpanda'),
                       lambda value:value['failures'][0].update(category=''),
                       lambda value:value['failures'][0].update(reason=''),
                       lambda value:value['failures'][0].update(evidence_paths=[]),
                       lambda value:value['failures'][0]['evidence_paths'][0].update(sha256='0'*64)]
            for mutation in mutations:
                value=deepcopy(original);mutation(value)
                review_path.write_text(json.dumps(value,ensure_ascii=False))
                content['failure_review']['sha256']=hashlib.sha256(review_path.read_bytes()).hexdigest()
                content_path.write_text(json.dumps(content,ensure_ascii=False))
                binding['sha256']=hashlib.sha256(content_path.read_bytes()).hexdigest()
                with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(ValueError):
                    current_browser_article_model(root,cases,rows,binding)
            content.pop('failure_review');content_path.write_text(json.dumps(content))
            binding['sha256']=hashlib.sha256(content_path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'requires a failure review'):
                current_browser_article_model(root,cases,rows,binding)

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
            self.assertIn('风控网页正文',body)
            self.assertIn('Origin returned HTTP 403',body)
            self.assertIn('独立结果审查：0/4',body)
            self.assertIn('data-network-method="moli" data-network-mode="public_doh"', body)
            self.assertIn('public_doh 2 次', body)
            self.assertIn('统一解析策略不保证', body)
            self.assertNotIn('150 道',body)
            self.assertNotIn('1.1.5',body)

    def test_interpretation_bounds_long_program_errors_without_changing_evidence(self):
        from bs4 import BeautifulSoup
        from harness.web_fetch.report_explainer import render_explainer, check_current_browser_article
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            row=next(row for row in rows if not row['ok'])
            row['error_message']='BEGIN FULL DEBUG LOG\n'+('debug line\n'*12000)+'FINAL ERROR: connection reset'
            original=row['error_message']
            content_path=root/binding['path'];content=json.loads(content_path.read_text())
            content['evidence_sha256']=canonical_json_sha256(_headless_article_input(cases,rows,['moli','lightpanda']))
            content_path.write_text(json.dumps(content,ensure_ascii=False))
            binding['sha256']=hashlib.sha256(content_path.read_bytes()).hexdigest()
            body=render_explainer(root,cases,rows,content_binding=binding)
            page=root/'guide/index.html';page.write_text(body)
            model=check_current_browser_article(root,cases,rows,binding)
            excerpt=BeautifulSoup(body,'html.parser').select_one('[data-browser-error="moli"]').get_text()
            self.assertLess(len(excerpt),4100)
            self.assertTrue(excerpt.endswith('FINAL ERROR: connection reset'))
            self.assertNotIn('BEGIN FULL DEBUG LOG',excerpt)
            self.assertIn('完整错误和日志见结果详情',excerpt)
            self.assertEqual(row['error_message'],original)
            self.assertEqual(model['stats']['moli']['specimen']['error_message'],original)
            page.write_text(body.replace('FINAL ERROR: connection reset','different failure',1))
            with self.assertRaisesRegex(ValueError,'error excerpt'):
                check_current_browser_article(root,cases,rows,binding)

    def test_real_provider_revision_gate_checks_bound_article_with_measured_labels(self):
        from harness.web_fetch.methods import with_measured_method_labels
        from harness.web_fetch.report_explainer import render_explainer
        from harness.web_fetch.report_check import check_revision
        class FollowingReleaseStage(Exception): pass
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            self.assertTrue(all(row['provider_type']!='fixture' for row in rows))
            for row in rows:
                if row['provider']=='moli':row['provider_metadata']['binary_sha256']='example-binary'
            (root/'implementation-provenance.json').write_text(json.dumps({'implementations':[
                {'provider':'moli','binary_sha256':'example-binary','version':'9.2.3'}]}))
            content_path=root/binding['path'];content=json.loads(content_path.read_text())
            content['evidence_sha256']=canonical_json_sha256(_headless_article_input(cases,rows,['moli','lightpanda']))
            content_path.write_text(json.dumps(content));binding['sha256']=hashlib.sha256(content_path.read_bytes()).hexdigest()
            (root/'dataset.json').write_text(json.dumps({'cases':list(cases.values()),'comparison':{'report_interpretation':binding}}))
            (root/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            (root/'index.html').write_text('<a href="providers/moli.html">Moli 9.2.3</a>')
            body=with_measured_method_labels(render_explainer)(root,cases,rows,content_binding=binding)
            self.assertIn('Moli 9.2.3',body)
            page=root/'guide/index.html';page.write_text(body)
            # The real-provider public revision boundary must finish the bound
            # semantic check before proceeding to unrelated release stages.
            with patch('harness.web_fetch.report_check.check_language_contract',side_effect=FollowingReleaseStage) as following:
                with self.assertRaises(FollowingReleaseStage):
                    check_revision(root,authority_root=Path(__file__).resolve().parents[1])
                following.assert_called_once()
            page.write_text(body.replace('data-browser-interface="moli"','data-missing-interface="moli"',1))
            with patch('harness.web_fetch.report_check.check_language_contract') as following:
                with self.assertRaisesRegex(ValueError,'interface matrix'):
                    check_revision(root,authority_root=Path(__file__).resolve().parents[1])
                following.assert_not_called()

    def test_network_disclosure_uses_recorded_modes_and_separates_service_network(self):
        from harness.web_fetch.report_explainer import _browser_network_rows
        records = [
            {'provider': 'moli', 'provider_metadata': {
                'dns_mode': 'public_doh', 'system_dns_fallback': False}},
            {'provider': 'moli', 'provider_metadata': {
                'dns_mode': 'public_doh', 'system_dns_fallback': False}},
            {'provider': 'chromium', 'provider_metadata': {
                'dns_mode': 'public_doh', 'secure_dns_mode': 'strict',
                'system_dns_fallback': False}},
            {'provider': 'lightpanda', 'provider_metadata': {}},
            {'provider': 'kitesurf', 'provider_metadata': {'dns_mode': 'public_doh'}},
        ]
        rows = {row['provider']: row for row in _browser_network_rows(
            records, ['moli', 'chromium', 'lightpanda', 'kitesurf'])}
        self.assertEqual(rows['moli']['mode'], 'public_doh')
        self.assertIn('不回退系统 DNS', rows['moli']['text'])
        self.assertIn('不保证', rows['moli']['text'])
        self.assertEqual(rows['chromium']['mode'], 'public_doh')
        self.assertIn('严格安全 DNS 配置', rows['chromium']['text'])
        self.assertEqual(rows['lightpanda']['mode'], 'unknown')
        self.assertEqual(rows['kitesurf']['mode'], 'service_network')
        self.assertIn('托管网络', rows['kitesurf']['text'])

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
                     body.replace('data-network-method="moli"','data-removed-network="moli"',1),
                     body.replace('data-network-mode="public_doh"','data-network-mode="system"',1),
                     body.replace('public_doh 2 次','public_doh 9 次',1),
                     body.replace('其中 2 次明确记录不回退系统 DNS','其中 1 次明确记录不回退系统 DNS',1),
                     body.replace('data-case-count="1"','data-case-count="9"',1),
                     body.replace('1 题：接口未记录','1 题：共同转换',1),
                     body.replace('data-browser-wait=','data-removed-wait=',1)]
            for changed in changes:
                with self.subTest(change=changes.index(changed)):
                    page.write_text(changed)
                    with self.assertRaises(ValueError):check_current_browser_article(root,cases,rows,binding)
            page.write_text(body)
            (root/'attempts/moli__risk-case.html').unlink()
            with self.assertRaisesRegex(ValueError,'target is broken'):
                check_current_browser_article(root,cases,rows,binding)

    def test_new_content_cannot_hide_incomplete_matrix_or_changed_measurements(self):
        from harness.web_fetch.report_explainer import render_explainer, _headless_article_input
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cases,rows,binding=self.example(root)
            changed=deepcopy(rows);changed[0]['elapsed_ms']=100
            with self.assertRaisesRegex(ValueError,'evidence changed'):
                render_explainer(root,cases,changed,content_binding=binding)
            p=root/binding['path'];content=json.loads(p.read_text())
            content['evidence_sha256']=canonical_json_sha256(_headless_article_input(cases,rows[:-1],['moli','lightpanda']))
            p.write_text(json.dumps(content));binding['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'complete declared matrix'):
                render_explainer(root,cases,rows[:-1],content_binding=binding)

    def test_public_builder_materializes_bound_prose_and_preserves_scores(self):
        from harness.web_fetch.config import load_dataset
        from harness.web_fetch.providers.fixture import FixtureProvider
        from harness.web_fetch.runner import run_evaluation
        from harness.web_fetch.report_build import build_report
        from harness.web_fetch.report_explainer import _headless_article_input, check_current_browser_article
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
                     'evidence_sha256':canonical_json_sha256(_headless_article_input(case_map,rows,['moli','lightpanda'])),
                     'title':'新一轮浏览器结果','intro':'统一规则的合成测试。',
                     'scoring':'正文必须满足题目要求。','conclusions':'完整比较所有调用。',
                     'providers':{name:{'specimen_case_id':'risk-case','explanation':'展示实际调用及程序验收结果。'} for name in ['moli','lightpanda']}}
            content_path=source/'report-assets/content.json';content_path.parent.mkdir();content_path.write_text(json.dumps(content,ensure_ascii=False))
            binding={'path':'report-assets/content.json','sha256':hashlib.sha256(content_path.read_bytes()).hexdigest()}
            self.bind_failure_review(source,case_map,rows,binding)
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
            self.assertEqual((report/'report-assets/failures.json').read_bytes(),(source/'report-assets/failures.json').read_bytes())
            self.assertEqual((report/'report-assets/delivery.png').read_bytes(),(source/'report-assets/delivery.png').read_bytes())
            failed=next(row for row in rows if not row['ok'])
            self.assertIn('失败原因专题核验',(report/'attempts'/f"{failed['attempt_id']}.html").read_text())
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
            with tempfile.TemporaryDirectory(prefix='report-profile-test-') as profile_dir:
                profile_path=Path(profile_dir)/'current-report.json'
                measured['comparison']['reproduction_profile']='instances/current-report.json'
                measured['comparison']['report_output']='reports/synthetic-browser'
                (source/'dataset.json').write_text(json.dumps(measured))
                profile['frozen_inputs']=frozen_input_bindings(source)
                profile_path.write_text(json.dumps(profile))
                cli_report=root/'cli-report'
                build=subprocess.run([sys.executable,'scripts/build_report.py','--profile',str(profile_path),
                                      '--evidence',str(source),'--output',str(cli_report),'--language','zh'],
                                     cwd=repository,capture_output=True,text=True)
                self.assertEqual(build.returncode,0,build.stderr)
                self.assertIn('新一轮浏览器结果',(cli_report/'index.html').read_text())
                self.assertIn('data-english-not-generated',(cli_report/'en/index.html').read_text())
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
