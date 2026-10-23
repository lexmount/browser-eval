import os
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.storage import (
    current_evidence_dir,
    data_home,
    require_canonical_inputs,
    require_current_report_profile,
    require_evidence,
    require_selection_history,
    research_evidence_dir,
)


class StorageTests(unittest.TestCase):
    def test_legacy_profile_validator_rejects_wcxb_with_track_specific_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / 'profile.json'
            profile.write_text(json.dumps({'comparison_kind': 'wcxb_frozen_html_to_markdown'}))
            with self.assertRaisesRegex(ValueError, 'WCXB evidence uses the certified cohort validator'):
                require_current_report_profile(root, profile)

    def test_explicit_data_home_and_evidence_override(self):
        with patch.dict(os.environ, {"BROWSER_EVAL_DATA_HOME": "/tmp/browser-data"}, clear=False):
            self.assertEqual(data_home(), Path("/tmp/browser-data").resolve())
        with patch.dict(os.environ, {"WEB_FETCH_EVIDENCE_DIR": "/tmp/browser-evidence"}, clear=False):
            evidence = Path("/tmp/browser-evidence").resolve()
            self.assertEqual(current_evidence_dir(), evidence)
            self.assertEqual(research_evidence_dir(), evidence.parent / "research")

    def test_explicit_research_override(self):
        with patch.dict(
            os.environ,
            {"WEB_FETCH_RESEARCH_EVIDENCE_DIR": "/tmp/browser-research"},
            clear=False,
        ):
            self.assertEqual(research_evidence_dir(), Path("/tmp/browser-research").resolve())

    def test_required_bundle_rejects_partial_data(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(FileNotFoundError,'results.jsonl'):
                require_evidence(Path(folder))

    def test_canonical_task_inputs_match_while_run_metadata_may_differ(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            canonical = root / "canonical"
            evidence = root / "evidence"
            for base in (canonical, evidence):
                (base / "sources/case").mkdir(parents=True)
                (base / "list-references").mkdir()
                (base / "dataset.json").write_text(
                    '{"schema_version":1,"tool":"web_fetch","dynamic_scoring":{},'
                    '"id":"canonical","name":"Canonical","coverage_plan":{},'
                    '"cases":[{"id":"case","url":"https://example.test"}]}'
                )
                (base / "sources/case/reference.txt").write_bytes(b"reference")
                (base / "list-references/case.json").write_bytes(b"list")
            require_canonical_inputs(evidence, canonical)
            (evidence / "dataset.json").write_text(
                '{"schema_version":1,"tool":"web_fetch","dynamic_scoring":{},'
                '"id":"cohort","name":"Cohort","coverage_plan":{"web":["p"]},'
                '"comparison":{"kind":"headless_markdown"},'
                '"cases":[{"id":"case","url":"https://example.test"}]}'
            )
            require_canonical_inputs(evidence, canonical)
            (evidence / "dataset.json").write_text(
                '{"schema_version":1,"tool":"web_fetch","dynamic_scoring":{},'
                '"id":"cohort","name":"Cohort","coverage_plan":{},'
                '"cases":[{"id":"case","url":"https://changed.test"}]}'
            )
            with self.assertRaisesRegex(ValueError, "cases"):
                require_canonical_inputs(evidence, canonical)
            (evidence / "dataset.json").write_text((canonical / "dataset.json").read_text())
            (evidence / "sources/case/reference.txt").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "sources"):
                require_canonical_inputs(evidence, canonical)

    def test_rerun_selection_requires_complete_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder)
            (evidence / "file-rerun-selection.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "historical baseline"):
                require_selection_history(evidence)
            baseline = evidence / "history/file-baseline"
            (baseline / "artifacts").mkdir(parents=True)
            (baseline / "results.jsonl").write_text("")
            (baseline / "execution.json").write_text("{}")
            require_selection_history(evidence)

    def test_current_report_profile_rejects_another_complete_cohort(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = root / 'evidence'
            evidence.mkdir()
            profile = {
                'dataset_id':'current', 'comparison_kind':'headless_markdown',
                'case_count':1, 'case_ids_sha256': hashlib.sha256(b'["a"]').hexdigest(),
                'providers':['p'], 'timeout_seconds':45,
                'runs_per_case':1, 'concurrency':1,
                'reference_binding_mode':'equivalent_reference_bytes_with_independent_batch_captures',
                'publication_state':'verified', 'unverified_reference_cases':[],
                'require_independent_result_review':True, 'conversion':'native',
            }
            (root/'profile.json').write_text(json.dumps(profile))
            dataset = {
                'id':'current', 'cases':[{'id':'a'}], 'coverage_plan':{'web':['p']},
                'comparison':{'kind':'headless_markdown','timeout_seconds':45,
                              'runs_per_case':1,'concurrency':1,'conversion':'native',
                              'reference_binding_mode':'equivalent_reference_bytes_with_independent_batch_captures',
                              'publication_state':'verified','unverified_reference_cases':[],
                              'require_independent_result_review':True},
            }
            (evidence/'dataset.json').write_text(json.dumps(dataset))
            record={'attempt_id':'p__a__r001','provider':'p','case_id':'a'}
            (evidence/'results.jsonl').write_text(json.dumps(record)+'\n')
            require_current_report_profile(evidence, root/'profile.json')
            dataset['comparison']['conversion']='obsolete converter'
            (evidence/'dataset.json').write_text(json.dumps(dataset))
            with self.assertRaisesRegex(ValueError, 'conversion'):
                require_current_report_profile(evidence, root/'profile.json')

    def test_current_report_profile_requires_exact_matrix_and_unique_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);evidence=root/'evidence';evidence.mkdir()
            profile={'dataset_id':'current','comparison_kind':'headless_markdown',
                     'case_count':2,
                     'case_ids_sha256': hashlib.sha256(b'["a","b"]').hexdigest(),
                     'providers':['p','q'],'timeout_seconds':45,
                     'runs_per_case':1,'concurrency':2,'conversion':'native',
                     'reference_binding_mode':'strict','publication_state':'verified',
                     'unverified_reference_cases':[],
                     'require_independent_result_review':True}
            (root/'profile.json').write_text(json.dumps(profile))
            dataset={'id':'current','cases':[{'id':'a'},{'id':'b'}],
                     'coverage_plan':{'web':['p','q']},'comparison':{
                         'kind':'headless_markdown','timeout_seconds':45,'runs_per_case':1,
                         'concurrency':2,'conversion':'native','reference_binding_mode':'strict',
                         'publication_state':'verified','unverified_reference_cases':[],
                         'require_independent_result_review':True}}
            (evidence/'dataset.json').write_text(json.dumps(dataset))
            rows=[{'attempt_id':f'id-{index}','provider':provider,'case_id':case}
                  for index,(provider,case) in enumerate([('p','a'),('p','a'),('q','a'),('q','b')])]
            (evidence/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaisesRegex(ValueError,'provider_case_matrix'):
                require_current_report_profile(evidence,root/'profile.json')
            rows=[{'attempt_id':'same','provider':provider,'case_id':case}
                  for provider in ('p','q') for case in ('a','b')]
            (evidence/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.assertRaisesRegex(ValueError,'attempt_ids'):
                require_current_report_profile(evidence,root/'profile.json')

    def test_current_report_profile_requires_bound_isolated_resource_cohort(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = root / 'evidence'
            evidence.mkdir()
            resource_profile = {
                'required': True, 'comparison_kind': 'headless_client_resource_profile',
                'case_count': 1, 'providers': ['p'], 'concurrency': 1,
                'sample_interval_ms': 50, 'backend': 'docker_cgroup_v2',
                'cpus_per_call': 2.0, 'memory_per_call': '2g',
                'network_isolation': 'dedicated bridge', 'controller_excluded': True,
                'required_metrics': ['cpu_ms', 'memory_peak_bytes', 'network_rx_bytes'],
            }
            profile = {
                'dataset_id': 'current', 'comparison_kind': 'headless_markdown',
                'case_count': 1, 'providers': ['p'], 'timeout_seconds': 45,
                'runs_per_case': 1, 'concurrency': 1, 'conversion': 'native',
                'case_ids_sha256': hashlib.sha256(b'["a"]').hexdigest(),
                'reference_binding_mode': 'strict', 'publication_state': 'verified',
                'unverified_reference_cases': [],
                'require_independent_result_review': True,
                'resource_profile': resource_profile,
            }
            (root / 'profile.json').write_text(json.dumps(profile))
            dataset = {
                'id': 'current', 'cases': [{'id': 'a'}],
                'coverage_plan': {'web': ['p']},
                'comparison': {
                    'kind': 'headless_markdown', 'timeout_seconds': 45,
                    'runs_per_case': 1, 'concurrency': 1, 'conversion': 'native',
                    'reference_binding_mode': 'strict', 'publication_state': 'verified',
                    'unverified_reference_cases': [],
                    'require_independent_result_review': True,
                },
            }
            (evidence / 'dataset.json').write_text(json.dumps(dataset))
            (evidence / 'results.jsonl').write_text(
                json.dumps({'attempt_id': 'p__a__r001', 'provider': 'p', 'case_id': 'a'}) + '\n'
            )
            with self.assertRaisesRegex(ValueError, 'resource_profile_missing'):
                require_current_report_profile(evidence, root / 'profile.json')

            resource = evidence / 'resource-cohort'
            resource.mkdir()
            environment = {
                'backend': 'docker_cgroup_v2', 'cgroup_version': '2',
                'cpus': 2.0, 'memory': '2g',
                'network_isolation': 'dedicated bridge', 'controller_excluded': True,
            }
            (resource / 'dataset.json').write_text(json.dumps({
                'cases': [{'id': 'a'}],
                'comparison': {
                    'kind': 'headless_client_resource_profile', 'concurrency': 1,
                    'resource_sample_interval_ms': 50,
                    'resource_backend': 'docker_cgroup_v2',
                    'resource_cpus': 2.0, 'resource_memory': '2g',
                },
            }))
            resource_row = {
                'attempt_id': 'resource__p__a', 'provider': 'p', 'case_id': 'a',
                'provider_metadata': {
                    'client_resource': {
                        'cpu_ms': 1, 'memory_peak_bytes': 2, 'network_rx_bytes': 3,
                    },
                    'resource_environment': environment,
                },
            }
            (resource / 'results.jsonl').write_text(json.dumps(resource_row) + '\n')
            (resource / 'resource-comparison.json').write_text(json.dumps({
                'providers': ['p'], 'common_pass_cases': ['a'], 'environment': environment,
            }))
            require_current_report_profile(evidence, root / 'profile.json')
            resource_row['provider_metadata']['resource_environment']['backend'] = 'host_process'
            (resource / 'results.jsonl').write_text(json.dumps(resource_row) + '\n')
            with self.assertRaisesRegex(ValueError, 'resource_environment'):
                require_current_report_profile(evidence, root / 'profile.json')


if __name__ == "__main__":
    unittest.main()

class FrozenProfileInputsTests(unittest.TestCase):
    def test_current_profile_binds_complete_assembled_evidence(self):
        from harness.web_fetch.storage import frozen_input_bindings, require_current_report_profile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = {
                'id': 'bound', 'cases': [{'id': 'a', 'track': 'web'}],
                'coverage_plan': {'web': ['p']},
                'comparison': {
                    'kind': 'headless_markdown', 'timeout_seconds': 45,
                    'runs_per_case': 1, 'concurrency': 1, 'conversion': 'native',
                    'reference_binding_mode': 'frozen_inputs',
                    'publication_state': 'verified', 'unverified_reference_cases': [],
                    'require_independent_result_review': False,
                },
            }
            (root / 'dataset.json').write_text(json.dumps(dataset))
            (root / 'results.jsonl').write_text(json.dumps({
                'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a',
            }) + '\n')
            (root / 'execution.json').write_text('{"batches": []}\n')
            (root / 'scoring-revisions.json').write_text('[]\n')
            files = {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in ('dataset.json', 'results.jsonl', 'execution.json',
                             'scoring-revisions.json')
            }
            profile = {
                'dataset_id': 'bound', 'comparison_kind': 'headless_markdown',
                'case_count': 1,
                'case_ids_sha256': hashlib.sha256(b'["a"]').hexdigest(),
                'providers': ['p'],
                **{k: v for k, v in dataset['comparison'].items() if k != 'kind'},
                'frozen_inputs': frozen_input_bindings(root),
                'case_inventory': {'web': ['a']},
                'evidence_binding': {
                    'schema_version': 1, 'kind': 'assembled_webfetch_evidence',
                    'files': files,
                },
            }
            path = root / 'profile.json'
            path.write_text(json.dumps(profile))
            require_current_report_profile(root, path)
            (root / 'results.jsonl').write_text(json.dumps({
                'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a', 'changed': True,
            }) + '\n')
            with self.assertRaisesRegex(ValueError, 'results.jsonl'):
                require_current_report_profile(root, path)
            (root / 'results.jsonl').write_text(json.dumps({
                'attempt_id': 'p__a', 'provider': 'p', 'case_id': 'a',
            }) + '\n')
            profile['evidence_binding']['files'].pop('scoring-revisions.json')
            path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError, 'incomplete evidence binding'):
                require_current_report_profile(root, path)

    def test_report_answers_must_match_independently_bound_pre_call_input(self):
        from harness.web_fetch.storage import frozen_input_bindings, require_frozen_case_source
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'pre-call.json';profile_path=root/'profile.json'
            original={'cases':[{'id':'a','url':'https://example.com','ground_truth_main_content':'Fixed answer',
                                'source_verification':{'reference_sha256':hashlib.sha256(b'Earlier capture').hexdigest()}}]}
            source.write_text(json.dumps(original))
            current={**original,'comparison':{'report_interpretation':{'path':'report-assets/article.json'}}}
            (root/'dataset.json').write_text(json.dumps(current))
            profile={'source_native_dataset_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                     'frozen_inputs':frozen_input_bindings(root)}
            profile_path.write_text(json.dumps(profile))
            require_frozen_case_source(root,profile_path,source)
            source.write_text(json.dumps({**original,'cases':[{'id':'a','ground_truth_main_content':'Changed answer'}]}))
            with self.assertRaisesRegex(ValueError,'source binding'):
                require_frozen_case_source(root,profile_path,source)
            source.write_text(json.dumps(original))
            current['cases']=[{**original['cases'][0],'ground_truth_main_content':'Changed answer'}]
            (root/'dataset.json').write_text(json.dumps(current))
            with self.assertRaisesRegex(ValueError,'frozen input bindings'):
                require_frozen_case_source(root,profile_path,source)
            # Rebinding a generated report still cannot bless an answer change.
            profile['frozen_inputs']=frozen_input_bindings(root)
            profile_path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,'answers differ from pre-call'):
                require_frozen_case_source(root,profile_path,source)
            current['cases']=original['cases'];(root/'dataset.json').write_text(json.dumps(current))
            profile['frozen_inputs']=frozen_input_bindings(root);profile.pop('source_native_dataset_sha256')
            profile_path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,'both source and report input bindings'):
                require_frozen_case_source(root,profile_path,source)

    def test_registered_native_browser_cannot_omit_interface_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            dataset={'id':'native','cases':[{'id':'a','url':'https://example.test/paper.pdf','track':'document'}],
                     'coverage_plan':{'document':['moli']},'comparison':{'kind':'headless_markdown',
                     'timeout_seconds':45,'runs_per_case':1,'concurrency':1,'conversion':'native',
                     'reference_binding_mode':'frozen_inputs','publication_state':'verified',
                     'retry_selection':'transient_transport_or_http_failure_only; final_physical_attempt_is_authoritative',
                     'unverified_reference_cases':[],'require_independent_result_review':False}}
            (root/'dataset.json').write_text(json.dumps(dataset))
            row={'attempt_id':'moli__a','provider':'moli','case_id':'a',
                 'provider_metadata':{'route':'ordinary_markdown','output':'markdown'}}
            (root/'results.jsonl').write_text(json.dumps(row)+'\n')
            profile={'dataset_id':'native','comparison_kind':'headless_markdown','case_count':1,
                     'case_ids_sha256':hashlib.sha256(b'["a"]').hexdigest(),'providers':['moli'],
                     **{k:v for k,v in dataset['comparison'].items() if k!='kind'}}
            path=root/'profile.json';path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,'requires native_task_route_contract'):
                require_current_report_profile(root,path)
            profile['native_task_route_contract']=4
            path.write_text(json.dumps(profile));require_current_report_profile(root,path)
            row['provider_metadata']['document_extractor']='pypdf'
            (root/'results.jsonl').write_text(json.dumps(row)+'\n')
            with self.assertRaisesRegex(ValueError,'Native task route contract violated'):
                require_current_report_profile(root,path)

    def test_composed_dataset_binds_all_current_inputs_without_raw_qualification_files(self):
        from harness.web_fetch.storage import frozen_input_bindings, require_current_report_profile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            dataset={'id':'composed','cases':[{'id':'a','track':'web'}],
                     'coverage_plan':{'web':['p']},
                     'comparison':{'kind':'headless_markdown','timeout_seconds':45,'runs_per_case':1,
                         'concurrency':1,'conversion':'native','reference_binding_mode':'frozen_inputs',
                         'retry_selection':'transient_transport_or_http_failure_only; final_physical_attempt_is_authoritative',
                         'publication_state':'verified','unverified_reference_cases':[],
                         'require_independent_result_review':False}}
            (root/'dataset.json').write_text(json.dumps(dataset))
            (root/'results.jsonl').write_text(json.dumps({'attempt_id':'p__a','provider':'p','case_id':'a','content':'First\u0085middle\u2028last\u2029paragraph'}, ensure_ascii=False)+'\n')
            profile={'dataset_id':'composed','comparison_kind':'headless_markdown','case_count':1,
                     'case_ids_sha256':hashlib.sha256(b'["a"]').hexdigest(),'providers':['p'],
                     **{k:v for k,v in dataset['comparison'].items() if k!='kind'},
                     'frozen_inputs':frozen_input_bindings(root),'case_inventory':{'web':['a']}}
            path=root/'profile.json';path.write_text(json.dumps(profile))
            require_current_report_profile(root,path)
            profile['case_inventory']={'web':['wrong-case']}
            path.write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,'case_inventory'):
                require_current_report_profile(root,path)
            profile['case_inventory']={'web':['a']}
            path.write_text(json.dumps(profile))
            (root/'sources').mkdir();(root/'sources/reference.txt').write_text('Changed current input')
            with self.assertRaisesRegex(ValueError,'frozen input bindings'):
                require_current_report_profile(root,path)
            (root/'sources/reference.txt').unlink()
            dataset['cases'][0]['url']='https://different.example/'
            (root/'dataset.json').write_text(json.dumps(dataset))
            with self.assertRaisesRegex(ValueError,'frozen input bindings'):
                require_current_report_profile(root,path)

    def test_input_binding_rejects_symlinked_assets(self):
        from harness.web_fetch.storage import frozen_input_bindings
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'dataset.json').write_text('{}')
            (root/'outside.txt').write_text('body');(root/'sources').mkdir()
            (root/'sources/link.txt').symlink_to(root/'outside.txt')
            with self.assertRaisesRegex(ValueError,'symlinks'):
                frozen_input_bindings(root)
