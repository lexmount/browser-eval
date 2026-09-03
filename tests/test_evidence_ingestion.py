from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

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

    def assert_captured_records_used(self, incoming, provider, *, track=None):
        path = (incoming/'results.jsonl').resolve()
        captured = path.read_bytes().replace(b'\n', b'\r\n')
        path.write_bytes(captured)
        original = (self.canonical/'results.jsonl').read_bytes()
        copy2, read_bytes = shutil.copy2, Path.read_bytes
        reads = []
        changed = False

        def record_read(file):
            if file == path:
                reads.append(file)
            return read_bytes(file)

        def mutate_after_copy(src, dst, *args, **kwargs):
            nonlocal changed
            result = copy2(src, dst, *args, **kwargs)
            if not changed:
                rows = [json.loads(line) for line in captured.splitlines()]
                rows[0]['provider'] = 'unvalidated-provider'
                rows[0]['metrics']['content_pass'] = 'unvalidated-score'
                path.write_bytes(b'\n'.join(json.dumps(row).encode() for row in rows)+b'\n')
                changed = True
            return result

        with patch.object(Path, 'read_bytes', record_read), \
                patch('harness.web_fetch.evidence.shutil.copy2', mutate_after_copy):
            self.assertEqual(add_provider_run(self.canonical, incoming, provider, track=track), 2)
        self.assertTrue(changed)
        self.assertEqual(reads, [path])
        self.assertEqual((self.canonical/'results.jsonl').read_bytes(), original+captured)
        for relative, raw in self.before.items():
            if relative.parts[0] == 'artifacts':
                self.assertEqual((self.canonical/relative).read_bytes(), raw)

    def test_incoming_results_mutation_uses_validated_bytes(self):
        self.assert_captured_records_used(self.incoming, 'new')

    def test_normalized_artifact_collision_rejected_before_staging(self):
        path = self.incoming/'results.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[1]['artifact_path'] = rows[0]['artifact_path'].replace('/', '//')
        rows[1]['metrics']['content_sha256'] = rows[0]['metrics']['content_sha256']
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        with patch('harness.web_fetch.evidence.tempfile.mkdtemp', wraps=tempfile.mkdtemp) as staging:
            with self.assertRaisesRegex(ValueError, 'Duplicate artifact paths'):
                add_provider_run(self.canonical, self.incoming, 'new')
            staging.assert_not_called()
        self.assert_unchanged()

    def test_normalized_existing_artifact_collision_rejected_before_staging(self):
        path = self.incoming/'results.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['artifact_path'] = 'artifacts//existing/./a.txt'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        with patch('harness.web_fetch.evidence.tempfile.mkdtemp', wraps=tempfile.mkdtemp) as staging:
            with self.assertRaisesRegex(ValueError, 'Duplicate artifact paths'):
                add_provider_run(self.canonical, self.incoming, 'new')
            staging.assert_not_called()
        self.assert_unchanged()


class TrackSupplementTests(EvidenceIngestionTests):
    def prepare_track(self):
        # Existing provider is measured on web only; dynamic reference already
        # belongs to the canonical dataset, but has no attempts for this method.
        full = {'tool': 'web_fetch', 'coverage_plan': {'web': ['existing'], 'dynamic': []},
                'cases': [{'id': 'a', 'track': 'web'}, {'id': 'b', 'track': 'web'},
                          {'id': 'c', 'track': 'dynamic', 'required_facts': [{'id': 'f', 'alternatives': ['real fact']}]},
                          {'id': 'd', 'track': 'dynamic'}]}
        (self.canonical/'dataset.json').write_text(json.dumps(full))
        self.dataset = {'tool': 'web_fetch', 'cases': full['cases'][2:]}
        self.supplement = self.make_run('track', 'existing')
        self.before = {p.relative_to(self.canonical): p.read_bytes()
                       for p in self.canonical.rglob('*') if p.is_file()}

    def test_complete_track_preserves_existing_measurements(self):
        self.prepare_track()
        self.assertEqual(add_provider_run(self.canonical, self.supplement, 'existing', track='dynamic'), 2)
        self.assertTrue((self.canonical/'results.jsonl').read_bytes().startswith(self.before[Path('results.jsonl')]))
        data = json.loads((self.canonical/'dataset.json').read_text())
        self.assertEqual(data['coverage_plan']['dynamic'], ['existing'])
        self.assertEqual(data['cases'], json.loads(self.before[Path('dataset.json')])['cases'])
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.supplement, 'existing', track='dynamic')

    def test_track_results_mutation_uses_validated_bytes(self):
        self.prepare_track()
        self.assert_captured_records_used(self.supplement, 'existing', track='dynamic')

    def test_changed_track_reference_rejected_without_mutation(self):
        self.prepare_track()
        data = json.loads((self.supplement/'dataset.json').read_text())
        data['cases'][0]['required_facts'][0]['alternatives'] = ['different fact']
        (self.supplement/'dataset.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.supplement, 'existing', track='dynamic')
        self.assert_unchanged()

    def test_partial_or_wrong_track_rejected(self):
        self.prepare_track()
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.supplement, 'existing', track='unknown')
        path = self.supplement/'results.jsonl'
        path.write_text(path.read_text().splitlines()[0]+'\n')
        with self.assertRaises(ValueError):
            add_provider_run(self.canonical, self.supplement, 'existing', track='dynamic')
        self.assert_unchanged()
