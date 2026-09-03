from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_local_dynamic_builtins import NAMES, ROOT, RuntimePaths, clean_environment, package_providers, publishable_identity, resolved_config, selected_dataset


class LocalDynamicBuiltinTests(unittest.TestCase):
    def paths(self):
        return RuntimePaths(Path("/private/tmp/cow"), Path("/private/tmp/claw"),
                            Path("/private/tmp/dsh"), Path("/private/tmp/node"))

    def test_clean_environment_ignores_inherited_credentials_and_netrc(self):
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "forbidden", "HTTP_PROXY": "forbidden"}):
            environment = clean_environment(Path("/private/tmp/run"), self.paths())
        self.assertNotIn("FIRECRAWL_API_KEY", environment)
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertEqual(environment["NETRC"], "/private/tmp/run/runtime/no-netrc")
        self.assertTrue(environment["OPENCLAW_CONFIG_PATH"].startswith("/private/tmp/run/"))

    def test_selection_preserves_current_facts_and_does_not_mutate_input(self):
        payload = {"id": "canonical", "cases": [
            {"id": identifier, "track": "dynamic", "required_facts": [{"id": "corrected"}]}
            for identifier in ("new-case-b", "new-case-a")] + [{"id": "static", "track": "web"}]}
        before = deepcopy(payload)
        subset = selected_dataset(payload)
        self.assertEqual(payload, before)
        self.assertEqual([case["id"] for case in subset["cases"]], ["new-case-b", "new-case-a"])
        self.assertEqual(subset["cases"][0]["required_facts"], [{"id": "corrected"}])
        self.assertEqual(subset["coverage_plan"]["dynamic"], list(NAMES))
        with self.assertRaises(ValueError):
            selected_dataset({"id": "bad", "cases": []})

    def test_provider_settings_preserved_except_explicit_paths(self):
        payload = {"schema_version": 1, "providers": {name: {"type": name, "test_setting": 45}
                   for name in NAMES}}
        before = deepcopy(payload)
        config = resolved_config(payload, Path("/private/tmp/run"), self.paths())
        self.assertEqual(payload, before)
        for name in NAMES:
            self.assertEqual(config["providers"][name]["test_setting"], 45)
        self.assertEqual(config["providers"][NAMES[0]]["workspace"],
                         "/private/tmp/run/runtime/cowagent-workspace")

    def test_packages_single_provider_batches_without_changing_cases_or_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = {"tool": "web_fetch", "cases": [
                {"id": identifier} for identifier in ("case-c", "case-a", "case-d", "case-b")]}
            for filename in ("dataset.json", "providers.json", "runtime-identity.json", "reference-validation.json"):
                (root / filename).write_text(json.dumps(dataset if filename == "dataset.json" else {}))
            (root / "sources").mkdir()
            records = []
            for name in NAMES:
                artifact = root / "artifacts" / name
                artifact.mkdir(parents=True)
                (artifact / "sample.txt").write_text("unchanged output")
                records.extend({"provider": name, "case_id": case["id"],
                                "attempt_id": name + case["id"]} for case in dataset["cases"])
            before = deepcopy(records)
            manifest = {"providers": [{"name": name} for name in NAMES], "dataset": {"source": "parent"}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            with patch("harness.web_fetch.metrics.summarize", return_value={}):
                package_providers(root, records, manifest)
            self.assertEqual(records, before)
            for name in NAMES:
                child = root / "providers" / name
                self.assertEqual(json.loads((child / "dataset.json").read_text()), dataset)
                self.assertEqual([json.loads(line) for line in (child / "results.jsonl").read_text().splitlines()],
                                 [record for record in records if record["provider"] == name])
                batch = json.loads((child / "execution.json").read_text())["batches"]
                self.assertEqual(len(batch), 1)
                self.assertEqual(batch[0]["providers"], [{"name": name}])
                self.assertEqual(batch[0]["attempt_count"], 4)

    def test_private_paths_removed_from_publishable_identity(self):
        paths = self.paths()
        identity = {"python_executable": "/private/runtime/python", "environment": {"NETRC": "/private/netrc"},
                    "file_sha256": {str(paths.node): "nodehash", "/private/runtime/python": "pyhash",
                                    str(paths.cowagent / "agent/tool.py"): "codehash",
                                    str(ROOT / "harness/web_fetch/runner.py"): "runnerhash"},
                    "repositories": {str(paths.cowagent): {"head": "commit", "tracked_status": ""}}}
        public = publishable_identity(identity, paths)
        self.assertNotIn("/private/", json.dumps(public))
        self.assertEqual(public["file_sha256"]["cowagent/agent/tool.py"], "codehash")
        self.assertEqual(public["repositories"]["cowagent"]["head"], "commit")

    def test_explicit_short_tmp_is_independent_of_long_output_path(self):
        env = clean_environment(Path("/private/tmp/" + "long" * 40), self.paths(), Path("/private/tmp/wfd-unit"))
        self.assertEqual(env["TMPDIR"], "/private/tmp/wfd-unit")
        self.assertLess(len(env["TMPDIR"] + "/tsx-501/12345.pipe"), 100)


if __name__ == "__main__":
    unittest.main()
