import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
                "attempt_count": 4,
                "attempt_ids": [row["attempt_id"] for row in records],
                "unexecuted": [],
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

    def test_projects_certified_execution_batches_and_all_bound_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "run", root / "projected"
            source.mkdir()
            cases = [{"id": identifier, "track": "web"} for identifier in ("a", "b")]
            dataset = {"id": "d", "cases": cases, "coverage_plan": {"web": ["p", "q"]}}
            (source / "dataset.json").write_text(json.dumps(dataset))
            records = []
            for provider in ("p", "q"):
                for index, identifier in enumerate(("a", "b")):
                    relative = f"artifacts/{provider}/{identifier}/run-001.txt"
                    data = f"{provider}-{identifier}".encode()
                    path = source / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    metadata = {}
                    if provider == "p" and identifier == "a":
                        retry_relative = "artifacts/p/a/retry-001.txt"
                        retry_data = b"retry-p-a"
                        retry_path = source / retry_relative
                        retry_path.write_bytes(retry_data)
                        metadata["retry"] = {"attempts": [{
                            "artifact_path": retry_relative,
                            "artifact_paths": [retry_relative],
                            "artifact_sha256": {
                                retry_relative: hashlib.sha256(retry_data).hexdigest()
                            },
                        }]}
                    if provider == "p" and identifier == "b":
                        private_relative = "private_artifacts/p/b/raw.json"
                        private_data = b'{"private":true}'
                        private_path = source / private_relative
                        private_path.parent.mkdir(parents=True, exist_ok=True)
                        private_path.write_bytes(private_data)
                        metadata.update({
                            "raw_response_path": private_relative,
                            "raw_response_sha256": hashlib.sha256(private_data).hexdigest(),
                            "raw_response_bytes": len(private_data),
                        })
                    records.append({
                        "attempt_id": f"{provider}__{identifier}__r001",
                        "provider": provider, "case_id": identifier, "run": 1,
                        "started_at": f"2026-01-01T00:00:0{index}+00:00",
                        "completed_at": f"2026-01-01T00:00:0{index + 1}+00:00",
                        "elapsed_ms": 1000, "artifact_path": relative,
                        "metrics": {"content_sha256": hashlib.sha256(data).hexdigest()},
                        "provider_metadata": metadata,
                    })
            (source / "results.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in records)
            )
            observation = b'{"observations":[]}'
            observation_hash = hashlib.sha256(observation).hexdigest()
            observation_relative = f"reference-checks/observations-{observation_hash}.json"
            observation_path = source / observation_relative
            observation_path.parent.mkdir()
            observation_path.write_bytes(observation)
            batches = []
            for index, identifier in enumerate(("a", "b")):
                batch = {
                    "started_at": f"2026-01-01T00:00:0{index}+00:00",
                    "completed_at": f"2026-01-01T00:00:0{index + 1}+00:00",
                    "dataset": {"id": "d", "case_count": 1},
                    "providers": [{"name": "p", "type": "p"}, {"name": "q", "type": "q"}],
                    "attempt_count": 2,
                    "attempt_ids": [f"p__{identifier}__r001", f"q__{identifier}__r001"],
                    "unexecuted": [],
                }
                if identifier == "a":
                    batch["reference_checks"] = {
                        "status": "verified", "cases": {"a": {"reference_sha256": "a"}},
                        "postcheck": {
                            "status": "verified", "cases": {"a": {"status": "verified"}},
                            "evidence_path": observation_relative,
                            "evidence_sha256": observation_hash,
                        },
                    }
                batches.append(batch)
            (source / "execution.json").write_text(json.dumps({"batches": batches}))

            with patch("scripts.project_headless_provider.require_verified_reference_checks") as verify:
                project(source, output, "p")

            self.assertEqual(verify.call_count, 2)
            self.assertFalse((output / "manifest.json").exists())
            projected = json.loads((output / "execution.json").read_text())["batches"]
            self.assertEqual([batch["attempt_ids"] for batch in projected], [
                ["p__a__r001"], ["p__b__r001"],
            ])
            self.assertEqual([batch["attempt_count"] for batch in projected], [1, 1])
            self.assertEqual(projected[0]["reference_checks"]["postcheck"]["cases"],
                             {"a": {"status": "verified"}})
            self.assertTrue((output / observation_relative).is_file())
            self.assertEqual((output / "artifacts/p/a/retry-001.txt").read_bytes(), b"retry-p-a")
            self.assertEqual((output / "private_artifacts/p/b/raw.json").read_bytes(),
                             b'{"private":true}')
            self.assertTrue((output / "source-execution.json").is_file())

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
