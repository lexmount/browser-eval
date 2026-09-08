import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.project_headless_provider import project


class HeadlessProviderProjectionTests(unittest.TestCase):
    def test_projects_only_a_complete_provider_without_changing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, references, output = root / "run", root / "references", root / "projected"
            source.mkdir(); references.mkdir()
            (references / "sources").mkdir(); (references / "list-references").mkdir()
            cases = [
                {"id": identifier, "track": "web", "source_verification": {"reference_sha256": identifier}}
                for identifier in ("a", "b")
            ]
            dataset = {"id": "d", "cases": cases, "coverage_plan": {"web": ["p", "q"]}}
            (source / "dataset.json").write_text(json.dumps(dataset))
            records = []
            for provider in ("p", "q"):
                for identifier in ("a", "b"):
                    relative = f"artifacts/{provider}/{identifier}/run-001.txt"
                    data = f"{provider}-{identifier}".encode()
                    path = source / relative; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    records.append({"attempt_id": f"{provider}__{identifier}__r001",
                        "provider": provider, "case_id": identifier, "run": 1,
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "completed_at": "2026-01-01T00:00:01+00:00", "elapsed_ms": 1000,
                        "artifact_path": relative, "metrics": {"content_sha256": hashlib.sha256(data).hexdigest()}})
            (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
            manifest = {"started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:02+00:00",
                "dataset": {"id": "d", "case_count": 2},
                "providers": [{"name": "p"}, {"name": "q"}],
                "reference_checks": {"status": "awaiting_postcheck",
                    "cases": {identifier: {"reference_sha256": identifier} for identifier in ("a", "b")}}}
            (source / "manifest.json").write_text(json.dumps(manifest))

            project(source, output, "p", references)

            projected = json.loads((output / "dataset.json").read_text())
            self.assertEqual(projected["coverage_plan"], {"web": ["p"]})
            rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
            self.assertEqual({row["provider"] for row in rows}, {"p"})
            self.assertEqual(len(rows), 2)
            self.assertEqual((output / "artifacts/p/a/run-001.txt").read_bytes(), b"p-a")
            saved = json.loads((output / "manifest.json").read_text())
            self.assertEqual(saved["projection"]["selection"],
                             "all frozen cases for one provider; no score-based selection")

            subset = root / "subset"
            project(source, subset, "p", references, {"b"})
            subset_dataset = json.loads((subset / "dataset.json").read_text())
            self.assertEqual([case["id"] for case in subset_dataset["cases"]], ["b"])
            subset_rows = [json.loads(line) for line in
                           (subset / "results.jsonl").read_text().splitlines()]
            self.assertEqual([(row["provider"], row["case_id"]) for row in subset_rows],
                             [("p", "b")])
            subset_manifest = json.loads((subset / "manifest.json").read_text())
            self.assertEqual(set(subset_manifest["reference_checks"]["cases"]), {"b"})
            self.assertIn("explicit predeclared", subset_manifest["projection"]["selection"])

    def test_rejects_incomplete_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "run"; source.mkdir()
            dataset = {"id": "d", "cases": [{"id": "a"}, {"id": "b"}],
                       "coverage_plan": {"web": ["p"]}}
            (source / "dataset.json").write_text(json.dumps(dataset))
            (source / "results.jsonl").write_text(json.dumps({"provider": "p", "case_id": "a"}) + "\n")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                project(source, root / "out", "p")


if __name__ == "__main__":
    unittest.main()
