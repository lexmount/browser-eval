"""Frozen supplements stay separate; all measurement/runtime calls are mocked."""
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from harness.web_fetch import benchmark_runner as benchmark

METHODS = ("fixture-alpha", "fixture-beta", "fixture-gamma")


class SupplementDatasetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.dataset_path = self.root / "supplement/frozen.json"
        self.output = self.root / "runs/new"
        self.content = "Frozen supplement content"
        self.payload = {"id": "independent-supplement", "tool": "web_fetch", "cases": [
            {"id": f"new-{index}", "name": f"New {index}", "url": "https://example.test/",
             "category": "dynamic_content", "track": "dynamic", "expected_any": [],
             "retrieval_hints": {
                 "version": 1, "wait_until": "domcontentloaded",
                 "completion": {"selector": "main", "minimum_count": 1,
                                "timeout_ms": 1000, "delay_ms": 0}},
             "ground_truth_main_content": self.content,
             "required_facts": [{"id": "content", "alternatives": [self.content]}],
             "source_benchmark": {"render_evidence": {
                 "scope": "Complete visible fixture", "rendered_truncated": False,
                 "completion": {"condition": "Content visible", "timeout_ms": 1000,
                                "satisfied": True}}}}
            for index in range(7)]}
        self.write(self.dataset_path, self.payload)
        self.ids = {case["id"] for case in self.payload["cases"]}
        for case in self.payload["cases"]:
            source = self.dataset_path.parent / "sources" / case["id"]
            source.mkdir(parents=True)
            for filename, content in (("reference.txt", self.content),
                                      ("rendered.txt", self.content),
                                      ("initial.html.txt", "<main>Loading</main>")):
                (source / filename).write_text(content)
            self.write(self.dataset_path.parent / "list-references" / (case["id"] + ".json"),
                       {"origin": "supplement"})
        self.write(self.root / "configs/providers.resources.json", {
            "schema_version": 1, "providers": {name: {"type": "fixture"}
                                                for name in METHODS}})
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(benchmark, "ROOT", self.root))
        stack.enter_context(patch.dict(os.environ, {"BROWSER_EVAL_DATA_HOME": str(self.root)}))
        self.env_loader = stack.enter_context(patch.object(benchmark, "load_runtime_env"))
        self.report = stack.enter_context(patch.object(benchmark, "build_report"))
        self.measure = stack.enter_context(patch.object(benchmark, "run_evaluation",
                                                       side_effect=self.fake_measure))
        self.provider = Mock(validate=Mock(return_value=[]))
        stack.enter_context(patch.object(benchmark, "build_provider", return_value=self.provider))
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(redirect_stderr(io.StringIO()))

    def write(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def fake_measure(self, **kwargs):
        folder = kwargs["output_dir"]
        folder.mkdir(parents=True, exist_ok=True)
        names = [folder.parent.name]
        records = []
        for name in names:
            for case in kwargs["cases"]:
                artifact = Path("artifacts") / name / case.id / "run-001.txt"
                (folder / artifact).parent.mkdir(parents=True, exist_ok=True)
                (folder / artifact).write_text(self.content)
                records.append({"case_id": case.id, "provider": name,
                                "attempt_id": name + "__" + case.id,
                                "artifact_path": artifact.as_posix(),
                                "metrics": {"content_sha256": hashlib.sha256(
                                    self.content.encode()).hexdigest()}})
        self.write(folder / "manifest.json", {
            "dataset": {"source": str(kwargs["dataset_path"])},
            "providers": [{"name": name} for name in names]})
        return records, {}

    def benchmark_main(self, *args):
        benchmark.main(["--dataset", str(self.dataset_path), "--output", str(self.output),
                        "--track", "dynamic", "--providers", ",".join(METHODS), *args])

    def test_selected_methods_measure_only_supplement_and_copy_references(self):
        before = self.dataset_path.read_bytes()
        self.benchmark_main()
        self.assertEqual(self.measure.call_count, len(METHODS))
        for call in self.measure.call_args_list:
            self.assertEqual(call.kwargs["dataset_path"], self.dataset_path)
            self.assertEqual({case.id for case in call.kwargs["cases"]}, self.ids)
        records = [json.loads(line) for line in (self.output / "results.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), len(self.ids) * len(METHODS))
        self.assertEqual({(row["case_id"], row["provider"]) for row in records},
                         {(case, name) for case in self.ids for name in METHODS})
        for case in self.ids:
            self.assertEqual((self.output / "sources" / case / "reference.txt").read_text(), self.content)
            self.assertEqual(json.loads((self.output / "list-references" / (case + ".json")).read_text()),
                             {"origin": "supplement"})
        self.assertEqual(self.dataset_path.read_bytes(), before)
        self.assertFalse((self.root / "evaluation").exists())

    def test_validate_only_preflights_without_writes_measurement_or_report(self):
        self.benchmark_main("--validate-only", "--env-file", "/dev/null")
        self.env_loader.assert_called_once_with(Path("/dev/null"))
        self.assertEqual(self.provider.validate.call_count, len(METHODS))
        self.measure.assert_not_called()
        self.report.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_explicit_config_supports_different_method_counts_for_dynamic_tasks(self):
        names = (*METHODS, 'fixture-delta')
        config = self.root / 'custom/providers.json'
        self.write(config, {'schema_version': 1,
                            'providers': {name: {'type': 'fixture'} for name in names}})
        (self.root / 'configs/providers.resources.json').unlink()
        original = self.dataset_path.read_bytes()
        for count in (1, 2, 4):
            with self.subTest(method_count=count):
                selected = names[:count]
                output = self.root / 'runs' / str(count)
                benchmark.main(['--dataset', str(self.dataset_path),
                                '--provider-config', str(config),
                                '--providers', ','.join(selected), '--track', 'dynamic',
                                '--output', str(output), '--measure-only'])
                rows = [json.loads(line) for line in
                        (output / 'results.jsonl').read_text().splitlines()]
                self.assertEqual({(row['provider'], row['case_id']) for row in rows},
                                 {(name, case) for name in selected for case in self.ids})
                self.assertEqual(len(rows), count * len(self.ids))
                self.assertEqual(json.loads((output / 'dataset.json').read_text())['coverage_plan'],
                                 {'dynamic': sorted(selected)})
        self.assertEqual(self.dataset_path.read_bytes(), original)
        self.report.assert_not_called()

    def test_track_filter_excludes_unrelated_cases(self):
        unrelated = {key: value for key, value in self.payload["cases"][0].items()
                     if key != "retrieval_hints"}
        unrelated.update(id="unrelated-web", track="web")
        self.payload["cases"].append(unrelated)
        self.write(self.dataset_path, self.payload)
        self.benchmark_main()
        self.assertEqual(json.loads((self.output / "dataset.json").read_text())["coverage_plan"],
                         {"dynamic": sorted(METHODS)})
        for call in self.measure.call_args_list:
            self.assertEqual({case.id for case in call.kwargs["cases"]}, self.ids)
        self.assertFalse((self.output / "sources/unrelated-web").exists())

    def test_invalid_supplement_does_not_fall_back_to_canonical(self):
        self.write(self.root / "evaluation/dataset.json", self.payload)
        for payload in ({**self.payload, "cases": []},
                        {**self.payload, "cases": [self.payload["cases"][0]] * 2}):
            with self.subTest(payload=payload):
                self.write(self.dataset_path, payload)
                with self.assertRaises(ValueError):
                    self.benchmark_main("--validate-only")
        self.measure.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_validate_only_still_reports_missing_credentials(self):
        self.provider.validate.return_value = ["api_key"]
        with self.assertRaises(SystemExit):
            self.benchmark_main("--validate-only")
        self.assertEqual(self.provider.validate.call_count, len(METHODS))
        self.measure.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_benchmark_defaults_remain_canonical(self):
        self.payload["coverage_plan"] = {"dynamic": list(METHODS)}
        self.write(self.root / "evaluation/dataset.json", self.payload)
        benchmark.main(["--validate-only"])
        self.assertEqual(self.provider.validate.call_count, 3)
        self.env_loader.assert_called_once_with(None)
        self.measure.assert_not_called()

    def test_existing_output_rejected_before_env_loading(self):
        self.output.mkdir(parents=True)
        sentinel = self.output / "measured.txt"
        sentinel.write_text("preserve")
        with self.assertRaises(SystemExit):
            self.benchmark_main()
        self.env_loader.assert_not_called()
        self.measure.assert_not_called()
        self.assertEqual(sentinel.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
