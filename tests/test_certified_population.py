"""Exercise explicit population selection through the certified entry point."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.record_io import canonical_json_sha256
from harness.web_fetch import benchmark_runner, certification, reference_freeze
from harness.web_fetch.evidence import finalize_aggregate


class PopulationCohortTests(unittest.TestCase):
    def inputs(self, root):
        case = {
            "id": "selected",
            "name": "Stable page",
            "url": "https://example.test/page",
            "category": "web",
            "track": "web",
            "expected_any": ["Required label"],
            "acceptance_contract": {
                "version": 1,
                "schema": "body_text",
                "minimum_records": 0,
                "stable_text_anchors": [{"id": "a", "alternatives": ["Required label"]}],
            },
        }
        excluded = dict(case, id="excluded")
        revision = {"id": "example", "sha256": "a" * 64, "parent_tasks_sha256": "b" * 64}
        data = {"tool": "web_fetch", "definition_revision": revision, "cases": [case, excluded]}
        spec = {
            "schema_version": 1,
            "kind": "task_population_selection",
            "id": "example",
            "revision": "example",
            "revision_sha256": "a" * 64,
            "task_count": 1,
            "suite_counts": {"articles": 1},
            "frozen_task_bindings": {"articles": {"sha256": "b" * 64}},
            "cases": [{"suite": "articles", "id": "selected"}],
            "excluded": [{"suite": "articles", "id": "excluded"}],
        }
        dataset, population, config = (
            root / "dataset.json",
            root / "population.json",
            root / "providers.json",
        )
        spec["frozen_task_bindings"]["articles"]["exported_cases_sha256"] = canonical_json_sha256(data["cases"])
        dataset.write_text(json.dumps(data), encoding='utf-8')
        population.write_text(json.dumps(spec), encoding='utf-8')
        config.write_text(json.dumps({"providers": {"fixture": {"type": "fixture"}}}), encoding="utf-8")
        args = [
            "--dataset",
            str(dataset),
            "--population",
            str(population),
            "--population-suite",
            "articles",
            "--output",
            str(root / "run"),
            "--work-root",
            str(root / "work"),
            "--providers",
            "fixture",
            "--provider-config",
            str(config),
        ]
        return data, spec, args

    def test_certified_run_selects_and_records_exact_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, spec, args = self.inputs(root)

            def execute(stage, arguments):
                if stage == reference_freeze.main:
                    reference_freeze.main(arguments)
                elif stage == benchmark_runner.main:
                    with patch.object(benchmark_runner, "load_runtime_env"):
                        benchmark_runner.main(arguments)
                elif stage == certification.evidence.finalize_main:
                    finalize_aggregate(root / "run", root / "work/post-captures/observations.json")
                else:
                    self.fail("Unexpected external call: " + str(stage))

            with patch.object(certification, "run", side_effect=execute):
                certification.main(args)
            execution = json.loads((root / "run/execution.json").read_text(encoding='utf-8'))
            saved = json.loads((root / "run/dataset.json").read_text(encoding='utf-8'))
            binding = execution["aggregate"]["task_population"]
            self.assertEqual(binding, saved["comparison"]["task_population"])
            self.assertEqual(binding["selected_case_ids"], ["selected"])
            self.assertEqual(binding["definition_revision"], data["definition_revision"])
            raw = (root / "population.json").read_bytes()
            self.assertEqual((root / "run/task-population.json").read_bytes(), raw)
            self.assertEqual(binding["sha256"], hashlib.sha256(raw).hexdigest())
            rows = [
                json.loads(line) for line in (root / "run/results.jsonl").read_text(encoding='utf-8').splitlines()
            ]
            self.assertEqual([r["case_id"] for r in rows], ["selected"])

    def test_mismatch_is_rejected_before_calls(self):
        mutations = [
            ("missing content binding", lambda d, s: s["frozen_task_bindings"]["articles"].pop("exported_cases_sha256")),
            ("changed URL", lambda d, s: d["cases"][0].update(url="https://changed.example/task")),
            ("changed rule", lambda d, s: d["cases"][0]["acceptance_contract"]["stable_text_anchors"][0].update(alternatives=["Different requirement"])),
            ("wrong revision", lambda d, s: s.update(revision_sha256="c" * 64)),
            (
                "wrong suite source",
                lambda d, s: d["definition_revision"].update(parent_tasks_sha256="d" * 64),
            ),
            ("wrong count", lambda d, s: s.update(task_count=2)),
            ("duplicate", lambda d, s: s["cases"].append(copy.deepcopy(s["cases"][0]))),
            ("missing selected", lambda d, s: d["cases"].pop(0)),
            ("unexpected task", lambda d, s: d["cases"].append(dict(d["cases"][0], id="extra"))),
            (
                "selection overlaps exclusion",
                lambda d, s: s["excluded"].append(copy.deepcopy(s["cases"][0])),
            ),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data, spec, args = self.inputs(root)
                mutate(data, spec)
                (root / "dataset.json").write_text(json.dumps(data), encoding='utf-8')
                (root / "population.json").write_text(json.dumps(spec), encoding='utf-8')
                with patch.object(certification, "run") as calls, self.assertRaises(SystemExit):
                    certification.main(args)
                calls.assert_not_called()
                self.assertFalse((root / "run").exists())
                self.assertFalse((root / "work").exists())

    def test_invalid_population_objects_get_cli_error_before_calls(self):
        for target, value in [('population', []), ('population', None),
                              ('revision', []), ('binding', None)]:
            with self.subTest(target=target, value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data, spec, args = self.inputs(root)
                if target == 'population':
                    spec = value
                elif target == 'revision':
                    data['definition_revision'] = value
                else:
                    spec['frozen_task_bindings']['articles'] = value
                (root / 'dataset.json').write_text(json.dumps(data), encoding='utf-8')
                (root / 'population.json').write_text(json.dumps(spec), encoding='utf-8')
                with patch.object(certification, 'run') as calls, patch('sys.stderr') as stderr:
                    with self.assertRaises(SystemExit) as error:
                        certification.main(args)
                self.assertEqual(error.exception.code, 2)
                self.assertIn('Invalid population selection', ''.join(
                    item.args[0] for item in stderr.write.call_args_list))
                calls.assert_not_called()
