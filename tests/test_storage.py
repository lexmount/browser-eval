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
