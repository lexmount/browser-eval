import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import merge_runs


class MergeProviderRunsTests(unittest.TestCase):
    def make_run(self, root: Path, provider: str) -> Path:
        run = root / provider
        (run / "artifacts" / provider).mkdir(parents=True)
        (run / "sources" / "case").mkdir(parents=True)
        (run / "sources" / "case" / "stable-page.txt").write_text("Stable\n")
        dataset = {
            "schema_version": 1, "id": "popular-sites", "tool": "web_fetch",
            "dynamic_scoring": "stable", "coverage_plan": {"challenge": [provider]},
            "comparison": {"kind": "popular_sites"},
            "cases": [{"id": "case", "track": "challenge", "url": "https://example.com"}],
        }
        (run / "dataset.json").write_text(json.dumps(dataset))
        artifact = run / "artifacts" / provider / "case.txt"
        artifact.write_text(provider)
        record = {
            "attempt_id": f"{provider}__case__r001", "provider": provider,
            "case_id": "case", "run": 1, "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:01+00:00", "elapsed_ms": 1000,
            "ok": True, "artifact_path": f"artifacts/{provider}/case.txt",
            "metrics": {"content_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()},
        }
        (run / "results.jsonl").write_text(json.dumps(record) + "\n")
        (run / "manifest.json").write_text(json.dumps({
            "schema_version": 1, "dataset": {"id": "popular-sites", "source": "/private/path"},
            "provider_config_source": "/private/config", "environment": {"hostname": "private"},
            "providers": [{"name": provider, "type": "fixture"}], "unexecuted": [],
        }))
        return run

    def test_merges_disjoint_complete_provider_cohorts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.make_run(root, "one")
            second = self.make_run(root, "two")
            output = root / "merged"
            with patch.object(merge_runs, "summarize", return_value={"providers": {}}), \
                    patch("sys.argv", ["merge_runs.py", str(output), str(first), str(second)]):
                merge_runs.main()
            dataset = json.loads((output / "dataset.json").read_text())
            self.assertEqual(dataset["coverage_plan"], {"challenge": ["one", "two"]})
            self.assertEqual(len((output / "results.jsonl").read_text().splitlines()), 2)
            execution = json.loads((output / "execution.json").read_text())
            self.assertEqual(len(execution["batches"]), 2)
            self.assertNotIn("hostname", execution["batches"][0]["environment"])
            self.assertEqual(execution["batches"][0]["dataset"]["source"],
                             "evaluation/popular-sites-dataset.json")

    def test_canonical_dataset_supplies_complete_reference_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root, "one")
            canonical = root / "evaluation" / "popular-sites-dataset.json"
            canonical.parent.mkdir()
            canonical.write_bytes((run / "dataset.json").read_bytes())
            source = canonical.parent / "sources" / "popular-sites" / "case" / "evidence.json"
            source.parent.mkdir(parents=True)
            source.write_text("{}")
            output = root / "merged"
            with patch.object(merge_runs, "summarize", return_value={"providers": {}}), \
                    patch("sys.argv", ["merge_runs.py", str(output), str(run),
                                       "--canonical-dataset", str(canonical)]):
                merge_runs.main()
            self.assertEqual(
                (output / "sources" / "popular-sites" / "case" / "evidence.json").read_text(),
                "{}",
            )

    def test_canonical_dataset_may_add_reference_binding_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self.make_run(root, "one")
            canonical = root / "canonical.json"
            payload = json.loads((run / "dataset.json").read_text())
            payload["comparison"]["reference_binding_mode"] = "hash_bound"
            payload["comparison"]["unverified_reference_cases"] = []
            canonical.write_text(json.dumps(payload))
            output = root / "merged"
            with patch.object(merge_runs, "summarize", return_value={"providers": {}}), \
                    patch("sys.argv", ["merge_runs.py", str(output), str(run),
                                       "--canonical-dataset", str(canonical)]):
                merge_runs.main()
            self.assertEqual(
                json.loads((output / "dataset.json").read_text())["comparison"][
                    "reference_binding_mode"
                ],
                "hash_bound",
            )

    def test_rejects_duplicate_provider_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.make_run(root / "a", "one")
            second = self.make_run(root / "b", "one")
            with patch("sys.argv", ["merge_runs.py", str(root / "merged"), str(first), str(second)]), \
                    self.assertRaises(SystemExit):
                merge_runs.main()
