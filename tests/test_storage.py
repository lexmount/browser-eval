import os
import json
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

    def test_explicit_current_dataset_scopes_canonical_source_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            canonical = root / "canonical"
            evidence = root / "evidence"
            for base in (canonical, evidence):
                (base / "sources/case-b").mkdir(parents=True)
                (base / "list-references").mkdir()
            (canonical / "sources/case-a").mkdir()
            (canonical / "sources/case-a/reference.txt").write_text("unrelated")
            (canonical / "sources/case-b/reference.txt").write_text("selected")
            (evidence / "sources/case-b/reference.txt").write_text("selected")
            dataset = {
                "schema_version": 1,
                "tool": "web_fetch",
                "dynamic_scoring": {},
                "id": "popular",
                "name": "Popular",
                "coverage_plan": {},
                "cases": [{"id": "case-b", "url": "https://example.test",
                           "ground_truth_evidence": [{"source": "sources/case-b/reference.txt"}]}],
            }
            selected = canonical / "popular-dataset.json"
            selected.write_text(json.dumps(dataset))
            (evidence / "dataset.json").write_text(json.dumps(dataset))
            require_canonical_inputs(evidence, canonical, canonical_dataset=selected)
            (evidence / "sources/case-a").mkdir()
            (evidence / "sources/case-a/reference.txt").write_text("unrelated")
            with self.assertRaisesRegex(ValueError, "sources"):
                require_canonical_inputs(evidence, canonical, canonical_dataset=selected)

    def test_selected_dataset_scopes_list_references_by_filename_stem(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "evaluation"
            evidence = root / "evidence"
            (canonical / "list-references").mkdir(parents=True)
            (evidence / "list-references").mkdir(parents=True)
            selected = canonical / "selected.json"
            payload = {"schema_version": 1, "tool": "web_fetch", "cases": [{
                "id": "case-a", "source_benchmark": {
                    "mode": "dynamic_list_structure",
                    "verification_scope": "first_three_name_detail_pairs",
                },
            }]}
            selected.write_text(json.dumps(payload))
            (evidence / "dataset.json").write_text(json.dumps(payload))
            for base in (canonical, evidence):
                (base / "list-references" / "case-a.json").write_text("{}")
            (canonical / "list-references" / "case-b.json").write_text("{}")
            require_canonical_inputs(evidence, canonical, canonical_dataset=selected)

    def test_selected_dataset_includes_declared_grouped_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical, evidence = root / "evaluation", root / "evidence"
            relative = Path("sources/frozen-contract/case-a/stable-page.txt")
            for base in (canonical, evidence):
                (base / relative).parent.mkdir(parents=True)
                (base / relative).write_text("Stable\n")
            payload = {
                "schema_version": 1,
                "tool": "web_fetch",
                "dynamic_scoring": {},
                "id": "popular",
                "cases": [{
                    "id": "case-a",
                    "source_benchmark": {
                        "stable_annotation": {"anchor_evidence": relative.as_posix()}
                    },
                }],
            }
            selected = canonical / "popular-dataset.json"
            selected.write_text(json.dumps(payload))
            (evidence / "dataset.json").write_text(json.dumps(payload))
            require_canonical_inputs(evidence, canonical, canonical_dataset=selected)

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
                'case_count':1, 'providers':['p'], 'timeout_seconds':45,
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
                     'case_count':2,'providers':['p','q'],'timeout_seconds':45,
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


if __name__ == "__main__":
    unittest.main()
