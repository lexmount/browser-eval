import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.moli_report import run_resource_matrix as matrix
from scripts.moli_report.run_resource_matrix import schedule, verify_calls


class ResourceMatrixTests(unittest.TestCase):
    def setUp(self):
        self.protocol = {
            "task_ids": ["first", "second"],
            "attempts": 3,
            "methods": {name: {"sha256": name * 64} for name in ("a", "b", "c")},
        }
        self.calls = [
            {
                "method": m,
                "task_id": t,
                "attempt": a,
                "started_ns": i * 10,
                "ended_ns": i * 10 + 5,
                "binary_sha256": m * 64,
            }
            for i, (m, t, a) in enumerate(schedule(["first", "second"], 3, ["a", "b", "c"]))
        ]

    def test_every_method_gets_every_position_and_every_task_attempt(self):
        for task in self.protocol["task_ids"]:
            groups = [
                [c["method"] for c in self.calls if c["task_id"] == task and c["attempt"] == a]
                for a in range(1, 4)
            ]
            self.assertEqual(groups, [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]])
        verify_calls(self.protocol, self.calls)

    def test_partial_duplicate_and_reordered_results_are_rejected(self):
        variants = [self.calls[:-1], self.calls + self.calls[-1:], list(reversed(self.calls))]
        for rows in variants:
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                verify_calls(self.protocol, rows)

    def test_overlapping_execution_and_wrong_binary_are_rejected(self):
        for field, value in [("started_ns", 0), ("binary_sha256", "other")]:
            rows = copy.deepcopy(self.calls)
            rows[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_calls(self.protocol, rows)

    def test_duplicate_task_or_method_cannot_expand_the_population(self):
        for tasks, methods in [(["x", "x"], ["a", "b"]), (["x"], ["a", "a"])]:
            with self.assertRaises(ValueError):
                list(schedule(tasks, 3, methods))

    def test_workers_run_the_complete_serial_matrix_and_bind_saved_rows(self):
        # A small stand-in driver exposes the same lifecycle, without browsers
        # or provider calls. The real coordinator and child processes execute.
        source = """
import json
from pathlib import Path
from types import SimpleNamespace
ENGINE_DEFS = {"moli": {}}
def find_free_port(): return 0
class BrowserManager:
    def launch(self, *args, **kwargs): return None
def run_driver_attempt(run_dir, result_path, run_id, task, engine, attempt, *args):
    row = {"task_id": task.task_id, "attempt": attempt, "status": "pass"}
    with result_path.open("a") as file: file.write(json.dumps(row) + "\\n")
    return row
def main(argv):
    task_ids = [argv[i+1] for i, item in enumerate(argv) if item == "--task"]
    k = int(argv[argv.index("--k")+1])
    run_id = argv[argv.index("--run-id")+1]
    directory = Path("runs") / run_id
    directory.mkdir(parents=True)
    manager = BrowserManager()
    manager.launch("moli")
    for task in task_ids:
        for attempt in range(1,k+1):
            manager.launch("moli")
            run_driver_attempt(directory, directory / "results.jsonl", run_id,
                               SimpleNamespace(task_id=task), "moli", attempt)
    (directory / "run_manifest.json").write_text(json.dumps({
        "completion_status": "completed", "completed_result_rows":len(task_ids)*k}))
    (directory / "host_summary.json").write_text('{"polluted":false}')
    return 0
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "runner").mkdir()
            (root / "runner/__init__.py").write_text("")
            (root / "runner/run.py").write_text(source)
            binary = root / "binary"
            binary.write_bytes(b"fixed test binary")
            protocol = {
                **self.protocol,
                "runner_root": str(root),
                "id": "test",
                "selected_round": 1,
                "seed": "fixed",
                "orchestrator_sha256": matrix.sha(Path(matrix.__file__)),
                "input_files": {"runner/run.py": matrix.sha(root / "runner/run.py")},
                "methods": {
                    name: {
                        "binary": str(binary),
                        "sha256": matrix.sha(binary),
                        "version": "test",
                        "layout": "off",
                        "run_id": name,
                    }
                    for name in ("a", "b", "c")
                },
            }
            path = root / "protocol.json"
            path.write_text(json.dumps(protocol))
            matrix.execute(path, root / "output")
            calls = [
                json.loads(line)
                for line in (root / "output/physical-calls.jsonl").read_text().splitlines()
            ]
            verify_calls(protocol, calls)
            receipt = json.loads((root / "output/receipt.json").read_text())
            self.assertEqual(receipt["physical_calls"], 18)
            self.assertEqual(set(receipt["results"]), {"a", "b", "c"})
            protocol["input_files"]["runner/run.py"] = "wrong"
            path.write_text(json.dumps(protocol))
            with self.assertRaisesRegex(ValueError, "frozen input changed"):
                matrix.execute(path, root / "rejected")


if __name__ == "__main__":
    unittest.main()
