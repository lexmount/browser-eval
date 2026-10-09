from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.evidence import finalize_aggregate


class FinalizeBenchmarkRunTests(unittest.TestCase):
    def write(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def test_updates_top_level_batches_and_merges_certificates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = {"attempt_ids": ["p__a__r001"], "reference_checks": {"status": "awaiting_postcheck"}}
            second = {"attempt_ids": ["p__b__r001"]}
            self.write(root / "execution.json", {"batches": [first, second]})
            paths = [root / "workers/p/web/manifest.json", root / "workers/p/resources/manifest.json"]
            self.write(paths[0], first)
            self.write(paths[1], second)
            observations = root / "observations.json"
            observations.write_text("[]\n")

            def finalize(worker: Path, _observations: Path) -> dict:
                manifest = json.loads((worker / "manifest.json").read_text())
                digest = hashlib.sha256(b"evidence\n").hexdigest()
                relative = f"reference-checks/observations-{digest}.json"
                manifest["reference_checks"] = {"status": "verified", "postcheck": {
                    "status": "verified", "evidence_path": relative, "evidence_sha256": digest}}
                self.write(worker / "manifest.json", manifest)
                certificate = worker / relative
                certificate.parent.mkdir()
                certificate.write_text("evidence\n")
                return {"status": "verified"}

            with patch("harness.web_fetch.evidence.finalize_reference_checks", side_effect=finalize):
                finalize_aggregate(root, observations)

            batches = json.loads((root / "execution.json").read_text())["batches"]
            self.assertEqual(batches[0]["reference_checks"]["status"], "verified")
            self.assertNotIn("reference_checks", batches[1])
            digest = hashlib.sha256(b"evidence\n").hexdigest()
            self.assertEqual((root / f"reference-checks/observations-{digest}.json").read_text(),
                             "evidence\n")

    def test_refuses_worker_inventory_mismatch_before_finalizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root / "execution.json", {"batches": [{"attempt_ids": ["p__a__r001"]}]})
            self.write(root / "workers/p/web/manifest.json", {"attempt_ids": ["p__b__r001"]})
            observations = root / "observations.json"
            observations.write_text("[]\n")
            with patch("harness.web_fetch.evidence.finalize_reference_checks") as finalize:
                with self.assertRaisesRegex(ValueError, "differ"):
                    finalize_aggregate(root, observations)
            finalize.assert_not_called()

    def test_resumes_after_worker_postcheck_without_rewriting_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / "workers/p/web"
            observations = root / "observations.json"
            observations.write_text("[]\n", encoding="utf-8")
            (worker / "results.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (worker / "results.jsonl").write_text("{}\n", encoding="utf-8")
            (worker / "dataset.json").write_text("{}\n", encoding="utf-8")
            sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {
                "attempt_ids": ["p__a__r001"],
                "reference_checks": {
                    "status": "verified",
                    "postcheck": {
                        "status": "verified",
                        "evidence_sha256": sha(observations),
                        "evidence_path": f"reference-checks/observations-{sha(observations)}.json",
                        "results_sha256": sha(worker / "results.jsonl"),
                        "dataset_sha256": sha(worker / "dataset.json"),
                    },
                },
            }
            self.write(root / "execution.json", {"batches": [manifest]})
            self.write(worker / "manifest.json", manifest)
            relative = manifest["reference_checks"]["postcheck"]["evidence_path"]
            certificate = worker / relative
            certificate.parent.mkdir()
            certificate.write_bytes(observations.read_bytes())

            with patch("harness.web_fetch.evidence.finalize_reference_checks") as finalize:
                finalize_aggregate(root, observations)
            finalize.assert_not_called()
            batches = json.loads((root / "execution.json").read_text())["batches"]
            self.assertEqual(batches[0]["reference_checks"]["status"], "verified")
            self.assertEqual((root / relative).read_bytes(), observations.read_bytes())

    def test_resume_rejects_stale_retained_postcheck(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / "workers/p/web"
            observations = root / "observations.json"
            observations.write_text("[]\n", encoding="utf-8")
            (worker / "results.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (worker / "results.jsonl").write_text("{}\n", encoding="utf-8")
            (worker / "dataset.json").write_text("{}\n", encoding="utf-8")
            manifest = {
                "attempt_ids": ["p__a__r001"],
                "reference_checks": {
                    "status": "verified",
                    "postcheck": {
                        "status": "verified",
                        "evidence_sha256": "0" * 64,
                        "results_sha256": hashlib.sha256(
                            (worker / "results.jsonl").read_bytes()).hexdigest(),
                        "dataset_sha256": hashlib.sha256(
                            (worker / "dataset.json").read_bytes()).hexdigest(),
                    },
                },
            }
            self.write(root / "execution.json", {"batches": [manifest]})
            self.write(worker / "manifest.json", manifest)

            with patch("harness.web_fetch.evidence.finalize_reference_checks") as finalize:
                with self.assertRaisesRegex(ValueError, "stale evidence_sha256"):
                    finalize_aggregate(root, observations)
            finalize.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def retained_run(root):
    worker = root / 'workers/custom/body'
    worker.mkdir(parents=True)
    observations = root / 'observations.json'
    observations.write_text('[]\n')
    digest = hashlib.sha256(observations.read_bytes()).hexdigest()
    for name in ('dataset.json', 'results.jsonl'):
        (worker / name).write_text('{}\n')
    relative = f'reference-checks/observations-{digest}.json'
    (worker / 'reference-checks').mkdir()
    (worker / relative).write_bytes(observations.read_bytes())
    batch = {'attempt_ids': ['custom__a__r001'], 'reference_checks': {
        'status': 'verified', 'postcheck': {
            'status': 'verified', 'evidence_path': relative, 'evidence_sha256': digest,
            'dataset_sha256': hashlib.sha256((worker / 'dataset.json').read_bytes()).hexdigest(),
            'results_sha256': hashlib.sha256((worker / 'results.jsonl').read_bytes()).hexdigest(),
        }}}
    (worker / 'manifest.json').write_text(json.dumps(batch))
    (root / 'execution.json').write_text(json.dumps({'batches': [batch]}))
    return worker, observations, batch, relative


def test_aggregate_copies_only_certificate_bound_observations(tmp_path):
    worker, observations, _, relative = retained_run(tmp_path)
    (worker / 'reference-checks/private-notes.txt').write_text('private investigation')
    finalize_aggregate(tmp_path, observations)
    assert (tmp_path / relative).read_bytes() == observations.read_bytes()
    assert not (tmp_path / 'reference-checks/private-notes.txt').exists()


def test_aggregate_rejects_duplicate_worker_batches_before_any_finalization(tmp_path):
    import pytest

    worker, observations, batch, _ = retained_run(tmp_path)
    duplicate = worker.parent / 'duplicate'
    duplicate.mkdir()
    (duplicate / 'manifest.json').write_text(json.dumps(batch))
    before = (tmp_path / 'execution.json').read_bytes()
    with patch('harness.web_fetch.evidence.finalize_reference_checks') as finalize:
        with pytest.raises(ValueError):
            finalize_aggregate(tmp_path, observations)
    finalize.assert_not_called()
    assert (tmp_path / 'execution.json').read_bytes() == before


def test_aggregate_rejects_corrupted_retained_observation_without_publishing(tmp_path):
    import pytest

    worker, observations, _, relative = retained_run(tmp_path)
    (worker / relative).write_text('modified')
    before = (tmp_path / 'execution.json').read_bytes()
    with pytest.raises(ValueError):
        finalize_aggregate(tmp_path, observations)
    assert (tmp_path / 'execution.json').read_bytes() == before
    assert not (tmp_path / 'reference-checks').exists()


def test_aggregate_rejects_symlinked_certificates_without_publishing(tmp_path):
    import pytest

    worker, observations, _, relative = retained_run(tmp_path)
    (worker / relative).unlink()
    (worker / relative).symlink_to(observations)
    before = (tmp_path / 'execution.json').read_bytes()
    with pytest.raises(ValueError):
        finalize_aggregate(tmp_path, observations)
    assert (tmp_path / 'execution.json').read_bytes() == before


def test_aggregate_refuses_worker_metadata_changes_with_same_attempt_inventory(tmp_path):
    import pytest

    worker, observations, batch, _ = retained_run(tmp_path)
    batch['timeout'] = 999
    (worker / 'manifest.json').write_text(json.dumps(batch))
    before = (tmp_path / 'execution.json').read_bytes()
    with pytest.raises(ValueError):
        finalize_aggregate(tmp_path, observations)
    assert (tmp_path / 'execution.json').read_bytes() == before


def test_aggregate_cannot_replace_an_existing_aggregate_certificate(tmp_path):
    import pytest

    worker, observations, batch, _ = retained_run(tmp_path)
    batch['reference_checks']['postcheck']['checked_at'] = '2026-10-09T01:00:00+00:00'
    (worker / 'manifest.json').write_text(json.dumps(batch))
    before = (tmp_path / 'execution.json').read_bytes()
    with pytest.raises(ValueError):
        finalize_aggregate(tmp_path, observations)
    assert (tmp_path / 'execution.json').read_bytes() == before
