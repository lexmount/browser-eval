from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.fixture import FixtureProvider
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.evidence import project_provider_run


class InterruptedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "interrupted"
        cases = [EvalCase(id=name, name=name, url="https://example.test/" + name,
                         category="web", expected_any=["anchor"])
                 for name in ("pass", "fail", "pending")]
        dataset = {"tool": "web_fetch", "cases": [asdict(case) for case in cases],
                   "coverage_plan": {"web": ["fixture"]}}
        path = self.root / "input.json"
        path.write_text(json.dumps(dataset))
        config = self.root / "providers.json"
        config.write_text(json.dumps({"providers": {"fixture": {"type": "fixture"}}}))
        run_evaluation(output_dir=self.source, dataset_meta=dataset, dataset_path=path,
                       cases=cases, providers=[FixtureProvider("fixture", {"type": "fixture", "fail_cases": ["fail"]})],
                       provider_config_path=config, runs=1, timeout=1, delay=0, max_failure_retries=0)
        self.rows = [json.loads(line) for line in (self.source / "results.jsonl").read_text().split("\n") if line]
        self.rows = self.rows[:2]
        (self.source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.rows))
        manifest = json.loads((self.source / "manifest.json").read_text())
        for key in ("completed_at", "attempt_ids", "attempt_count"):
            manifest.pop(key)
        (self.source / "manifest.json").write_text(json.dumps(manifest))
        self.original = {str(p.relative_to(self.source)): p.read_bytes()
                         for p in self.source.rglob("*") if p.is_file()}

    def test_recovers_every_saved_pass_and_failure_without_calls_or_source_mutation(self):
        output = self.root / "retained"
        project_provider_run(self.source, output, "fixture", retain_completed=True)
        rows = [json.loads(line) for line in (output / "results.jsonl").read_text().split("\n") if line]
        self.assertEqual(rows, self.rows)
        self.assertEqual([row["ok"] for row in rows], [True, False])
        self.assertEqual({str(p.relative_to(self.source)): p.read_bytes()
                          for p in self.source.rglob("*") if p.is_file()}, self.original)
        batch = json.loads((output / "execution.json").read_text())["batches"][0]
        self.assertEqual(batch["attempt_ids"], [row["attempt_id"] for row in rows])
        self.assertEqual(batch["recovery"]["original_batch_completed"], False)
        self.assertEqual(batch["recovery"]["pending_logical_results"], 1)
        self.assertEqual(batch["completed_at"], rows[-1]["completed_at"])

    def test_rejects_changed_input_configuration_log_or_output(self):
        for kind in ("input", "config", "log", "output", "identity"):
            with self.subTest(kind=kind):
                manifest = json.loads((self.source / "manifest.json").read_text())
                if kind == "input": target = Path(manifest["dataset"]["source"])
                elif kind == "config": target = Path(manifest["provider_config_source"])
                elif kind == "output": target = self.source / self.rows[0]["artifact_path"]
                elif kind == "log": target = self.source / self.rows[0]["provider_metadata"]["call_log"]["path"]
                else: target = self.source / "results.jsonl"
                before = target.read_bytes()
                if kind == "identity":
                    changed = json.loads(json.dumps(self.rows))
                    changed[0]["attempt_id"] = "fixture__other__r001"
                    target.write_text("".join(json.dumps(row) + "\n" for row in changed))
                else: target.write_bytes(before + b" ")
                try:
                    with self.assertRaises(ValueError):
                        project_provider_run(self.source, self.root / kind, "fixture", retain_completed=True)
                finally: target.write_bytes(before)

    def test_recovery_cannot_select_only_passing_cases(self):
        with self.assertRaises(ValueError):
            project_provider_run(self.source, self.root / "selected", "fixture", case_ids={"pass"}, retain_completed=True)


class ProviderAssemblyReviewPolicyTests(unittest.TestCase):
    def test_adding_a_provider_preserves_the_declared_review_policy(self):
        from harness.web_fetch.assembly import assemble
        for required in (False, True):
            with self.subTest(required=required), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = EvalCase(id="a", name="A", url="https://example.test/a",
                                category="web", expected_any=["anchor"])
                runs = []
                for provider in ("fixture", "peer"):
                    dataset = {"tool": "web_fetch", "cases": [asdict(case)],
                               "comparison": {"require_independent_result_review": required},
                               "coverage_plan": {"web": [provider]}}
                    path = root / (provider + ".json")
                    path.write_text(json.dumps(dataset))
                    config = root / (provider + "-config.json")
                    config.write_text(json.dumps({"providers": {provider: {"type": "fixture"}}}))
                    output = root / provider
                    run_evaluation(output_dir=output, dataset_meta=dataset, dataset_path=path,
                                   cases=[case], providers=[FixtureProvider(provider, {"type": "fixture"})],
                                   provider_config_path=config, runs=1, timeout=1, delay=0, max_failure_retries=0)
                    (output / "sources").mkdir()
                    (output / "list-references").mkdir()
                    runs.append(output)
                target = root / "assembled"
                assemble(runs[0], root / "unused", [], target, provider_additions=[runs[1]])
                from harness.web_fetch.artifacts import verify_record_public_call_logs
                from harness.record_io import parse_jsonl
                for row in parse_jsonl((target / 'results.jsonl').read_bytes()):
                    verify_record_public_call_logs(target, row)
                saved = json.loads((target / "dataset.json").read_text())
                self.assertEqual(saved["comparison"]["require_independent_result_review"], required)
                self.assertEqual(saved["coverage_plan"], {"web": ["fixture", "peer"]})


class CaseAssemblyExecutionBatchTests(unittest.TestCase):
    def test_case_addition_retains_every_certified_batch_and_call_log(self):
        from harness.web_fetch.assembly import assemble
        from harness.record_io import parse_jsonl
        from harness.web_fetch.artifacts import verify_record_public_call_logs
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);runs=[]
            for cid in ('first','second'):
                case=EvalCase(id=cid,name=cid,url='https://example.test/'+cid,category='web',expected_any=['anchor'])
                dataset={'tool':'web_fetch','cases':[asdict(case)],
                         'comparison':{'require_independent_result_review':False},
                         'coverage_plan':{'web':['fixture','peer']}}
                path=root/(cid+'.json');path.write_text(json.dumps(dataset))
                config=root/(cid+'-config.json');config.write_text('{}')
                output=root/cid
                run_evaluation(output_dir=output,dataset_meta=dataset,dataset_path=path,cases=[case],
                               providers=[FixtureProvider(n,{'type':'fixture'}) for n in ['fixture','peer']],
                               provider_config_path=config,runs=1,timeout=1,delay=0)
                manifest=json.loads((output/'manifest.json').read_text())
                (output/'execution.json').write_text(json.dumps({'batches':[manifest]}))
                (output/'manifest.json').unlink()
                (output/'sources').mkdir();(output/'list-references').mkdir();runs.append(output)
            target=root/'assembled'
            assemble(runs[0],root/'unused',[],target,additions=[runs[1]])
            rows=parse_jsonl((target/'results.jsonl').read_bytes())
            self.assertEqual({(r['case_id'],r['provider']) for r in rows},
                             {(cid,p) for cid in ('first','second') for p in ('fixture','peer')})
            self.assertEqual(len(json.loads((target/'execution.json').read_text())['batches']),2)
            for row in rows:verify_record_public_call_logs(target,row)
