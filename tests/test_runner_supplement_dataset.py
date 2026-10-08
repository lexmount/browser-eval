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
from scripts import run_local_dynamic_builtins as local

OTHER9 = ("lexmount-global", "lexmount-cn", "firecrawl", "tavily", "exa",
          "cloudflare-markdown", "langchain-webbase", "crawl4ai", "moli-webfetch")


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
                                                for name in (*OTHER9, *local.NAMES)}})
        self.paths = local.RuntimePaths(*(self.root / "external" / name
                                          for name in ("cow", "claw", "deepseek", "node")))
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(benchmark, "ROOT", self.root))
        stack.enter_context(patch.object(local, "ROOT", self.root))
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
        names = ([folder.parent.name] if "workers" in folder.parts else list(local.NAMES))
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
                        "--track", "dynamic", "--providers", ",".join(OTHER9), *args])

    def local_main(self, *args):
        argv = ["runner", "--output", str(self.output), "--cowagent-repo", str(self.paths.cowagent),
                "--openclaw-repo", str(self.paths.openclaw), "--deepseek-repo", str(self.paths.deepseek),
                "--node", str(self.paths.node), *args]
        with patch("sys.argv", argv):
            return local.main()

    def test_other9_measure_only_seven_new_cases_and_copy_supplement_references(self):
        before = self.dataset_path.read_bytes()
        self.benchmark_main()
        self.assertEqual(self.measure.call_count, 9)
        for call in self.measure.call_args_list:
            self.assertEqual(call.kwargs["dataset_path"], self.dataset_path)
            self.assertEqual({case.id for case in call.kwargs["cases"]}, self.ids)
        records = [json.loads(line) for line in (self.output / "results.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 63)
        self.assertEqual({(row["case_id"], row["provider"]) for row in records},
                         {(case, name) for case in self.ids for name in OTHER9})
        for case in self.ids:
            self.assertEqual((self.output / "sources" / case / "reference.txt").read_text(), self.content)
            self.assertEqual(json.loads((self.output / "list-references" / (case + ".json")).read_text()),
                             {"origin": "supplement"})
        self.assertEqual(self.dataset_path.read_bytes(), before)
        self.assertFalse((self.root / "evaluation").exists())

    def test_validate_only_preflights_without_writes_measurement_or_report(self):
        self.benchmark_main("--validate-only", "--env-file", "/dev/null")
        self.env_loader.assert_called_once_with(Path("/dev/null"))
        self.assertEqual(self.provider.validate.call_count, 9)
        self.measure.assert_not_called()
        self.report.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_track_filter_excludes_unrelated_cases_from_both_runners(self):
        unrelated = {key: value for key, value in self.payload["cases"][0].items()
                     if key != "retrieval_hints"}
        unrelated.update(id="unrelated-web", track="web")
        self.payload["cases"].append(unrelated)
        self.write(self.dataset_path, self.payload)
        self.benchmark_main()
        self.assertEqual(json.loads((self.output / "dataset.json").read_text())["coverage_plan"],
                         {"dynamic": sorted(OTHER9)})
        self.assertEqual({case["id"] for case in local.selected_dataset(self.payload)["cases"]}, self.ids)
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
                with self.assertRaises(ValueError):
                    local.selected_dataset(payload)
        self.measure.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_validate_only_still_reports_missing_credentials(self):
        self.provider.validate.return_value = ["api_key"]
        with self.assertRaises(SystemExit):
            self.benchmark_main("--validate-only")
        self.assertEqual(self.provider.validate.call_count, 9)
        self.measure.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_benchmark_defaults_remain_canonical(self):
        self.payload["coverage_plan"] = {"dynamic": list(local.NAMES)}
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
        with patch.object(local.subprocess, "run") as child, self.assertRaises(FileExistsError):
            self.local_main("--dataset", str(self.dataset_path))
        child.assert_not_called()
        self.env_loader.assert_not_called()
        self.measure.assert_not_called()
        self.assertEqual(sentinel.read_text(), "preserve")

    def test_local_parent_forwards_absolute_dataset_and_external_runtimes_in_clean_env(self):
        with patch.object(local.subprocess, "run", return_value=Mock(returncode=0)) as child, \
                patch.dict(os.environ, {"PRIVATE_API_KEY": "do-not-inherit"}), \
                patch("os.getcwd", return_value=str(self.root)):
            self.assertEqual(self.local_main("--dataset", "supplement/frozen.json"), 0)
        command = child.call_args.args[0]
        self.assertEqual(command[command.index("--dataset") + 1], str(self.dataset_path))
        for flag, path in (("--cowagent-repo", self.paths.cowagent), ("--openclaw-repo", self.paths.openclaw),
                           ("--deepseek-repo", self.paths.deepseek), ("--node", self.paths.node)):
            self.assertEqual(command[command.index(flag) + 1], str(path))
        env = child.call_args.kwargs["env"]
        self.assertNotIn("PRIVATE_API_KEY", env)
        self.assertNotIn("HOME", env)
        self.assertEqual(env["NETRC"], str(self.output / "runtime/no-netrc"))
        self.env_loader.assert_not_called()

    def test_local_default_dataset_and_child_dispatch(self):
        with patch.object(local, "run_child", return_value=0) as child:
            self.local_main("--isolated-child", "--runtime-tmp", str(self.root / "tmp"))
        self.assertEqual(child.call_args.args[-1], self.root / "evaluation/dataset.json")
        self.assertFalse(self.output.exists())

    def test_local_output_must_stay_in_persistent_runs(self):
        self.output = self.root / "outside-run"
        with patch.object(local.subprocess, "run") as child, self.assertRaises(ValueError):
            self.local_main("--dataset", str(self.dataset_path))
        child.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_local3_child_uses_only_supplement_and_validates_copied_references(self):
        self.output.mkdir(parents=True)
        temporary = self.root / "tmp"
        worker = "node_modules/.pnpm/.browser-harness-dsh-worker.mts"
        for path in (self.paths.deepseek / worker, self.root / "scripts/oss/dsh_webfetch_worker.mts"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("mock runtime")
        real_is_file = Path.is_file
        runtime_roots = (self.root / "external", self.root / "scripts", self.root / "harness")
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, local.clean_environment(
                self.output, self.paths, temporary), clear=True))
            stack.enter_context(patch.object(Path, "is_file", lambda path:
                real_is_file(path) or any(path.is_relative_to(root) for root in runtime_roots)))
            stack.enter_context(patch.object(local, "digest", return_value=hashlib.sha256(
                self.content.encode()).hexdigest()))
            stack.enter_context(patch.object(local, "version", return_value="mock-version"))
            stack.enter_context(patch.object(local.subprocess, "check_output", return_value="mock-runtime"))
            stack.enter_context(patch.object(local.subprocess, "run", return_value=Mock(
                returncode=0, stdout="", stderr="")))
            stack.enter_context(patch("harness.web_fetch.providers.build_provider", return_value=self.provider))
            measure = stack.enter_context(patch("harness.web_fetch.runner.run_evaluation",
                                                 side_effect=self.fake_measure))
            stack.enter_context(patch("harness.web_fetch.metrics.summarize", return_value={}))
            self.assertEqual(local.run_child(self.output, self.paths, "dynamic", temporary,
                                             self.dataset_path), 0)
        self.assertEqual(len(measure.call_args.kwargs["cases"]), 7)
        self.assertEqual(len(measure.call_args.kwargs["providers"]), 3)
        self.assertEqual(measure.call_args.kwargs["dataset_path"], self.output / "dataset.json")
        self.assertEqual(json.loads((self.output / "dataset.json").read_text())["cases"], self.payload["cases"])
        audits = json.loads((self.output / "reference-validation.json").read_text())
        self.assertEqual(set(audits), self.ids)
        self.assertTrue(all(audit["valid"] for audit in audits.values()))
        identity = json.loads((self.output / "runtime-identity.json").read_text())
        self.assertEqual(identity["source_dataset_sha256"], hashlib.sha256(self.dataset_path.read_bytes()).hexdigest())
        for name in local.NAMES:
            bundle = self.output / "providers" / name
            self.assertEqual(len((bundle / "results.jsonl").read_text().splitlines()), 7)
            self.assertEqual({path.name for path in (bundle / "sources").iterdir()}, self.ids)
        self.assertFalse((self.root / "evaluation").exists())
        self.env_loader.assert_not_called()

    def test_invalid_local_reference_fails_before_runtime_or_measurement(self):
        self.output.mkdir(parents=True)
        (self.dataset_path.parent / "sources/new-0/rendered.txt").write_text("incomplete")
        temporary = self.root / "tmp"
        with patch.dict(os.environ, local.clean_environment(self.output, self.paths, temporary), clear=True), \
                patch("harness.web_fetch.runner.run_evaluation") as measure, \
                patch.object(local.subprocess, "run") as runtime, \
                self.assertRaisesRegex(ValueError, "Invalid reference new-0"):
            local.run_child(self.output, self.paths, "dynamic", temporary, self.dataset_path)
        runtime.assert_not_called()
        measure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
