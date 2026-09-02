from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.evidence import add_provider_run


class EvidenceIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = {"cases": [{"id": "a"}, {"id": "b"}]}
        self.canonical = self.make_run("canonical", "existing")
        self.incoming = self.make_run("incoming", "new")
        self.before = {
            p.relative_to(self.canonical): p.read_bytes()
            for p in self.canonical.rglob("*")
            if p.is_file()
        }

    def make_run(self, folder, provider):
        root = self.root / folder
        root.mkdir()
        rows = []
        for i, case in enumerate(self.dataset["cases"]):
            content = f"{provider}:{case['id']}".encode()
            path = f"artifacts/{provider}/{case['id']}.txt"
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            rows.append(
                {
                    "provider": provider,
                    "case_id": case["id"],
                    "attempt_id": provider + "__" + case["id"],
                    "artifact_path": path,
                    "started_at": f"2026-09-02T12:00:0{i}+00:00",
                    "metrics": {"content_sha256": hashlib.sha256(content).hexdigest()},
                    "provider_metadata": {"binary_sha256": "verified"},
                }
            )
        batch = {
            "providers": [{"name": provider}],
            "started_at": "2026-09-02T12:00:00+00:00",
            "completed_at": "2026-09-02T12:00:05+00:00",
            "attempt_count": len(rows),
        }
        (root / "dataset.json").write_text(json.dumps(self.dataset))
        (root / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (root / "execution.json").write_text(json.dumps({"batches": [batch]}))
        return root

    def assert_unchanged(self):
        self.assertEqual(
            self.before,
            {
                p.relative_to(self.canonical): p.read_bytes()
                for p in self.canonical.rglob("*")
                if p.is_file()
            },
        )

    def test_complete_run_added_old_bytes_preserved(self):
        self.assertEqual(
            add_provider_run(self.canonical, self.incoming, "new", {"sha256": "verified"}), 2
        )
        for path, value in self.before.items():
            if path.name not in ("results.jsonl", "execution.json"):
                self.assertEqual((self.canonical / path).read_bytes(), value)
        self.assertTrue(
            (self.canonical / "results.jsonl")
            .read_bytes()
            .startswith(self.before[Path("results.jsonl")])
        )
        batch = json.loads((self.canonical / "execution.json").read_text())["batches"][-1]
        self.assertEqual(batch["attempt_ids"], ["new__a", "new__b"])
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.incoming, "new")

    def test_partial_run_rejected_without_changes(self):
        p = self.incoming / "results.jsonl"
        p.write_text(p.read_text().splitlines()[0] + "\n")
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.incoming, "new")
        self.assert_unchanged()

    def test_hash_or_dataset_changes_rejected(self):
        artifact = self.incoming / "artifacts/new/a.txt"
        artifact.write_text("modified")
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.incoming, "new")
        self.assert_unchanged()
        d = deepcopy(self.dataset)
        d["cases"][0]["name"] = "changed reference"
        (self.incoming / "dataset.json").write_text(json.dumps(d))
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.incoming, "new")
        self.assert_unchanged()

    def test_ambiguous_batches_and_wrong_binary_rejected(self):
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.incoming, "new", {"sha256": "wrong"})
        path = self.incoming / "execution.json"
        data = json.loads(path.read_text())
        data["batches"] *= 2
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.incoming, "new")
        self.assert_unchanged()
