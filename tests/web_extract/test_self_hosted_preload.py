"""Warm SDK imports must preserve independent requests and bounded process cleanup."""

import hashlib
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import psutil

from harness.web_extract.self_hosted import run_forkserver_worker

SEEN = []


def isolated_request(value):
    SEEN.append(value["name"])
    if value.get("sleep") or value.get("spawn"):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        Path(value["pid_file"]).write_text(str(child.pid), encoding="utf-8")
    if value.get("crash"):
        os._exit(9)
    if value.get("sleep"):
        time.sleep(60)
    return {"name": value["name"], "seen": SEEN, "pid": os.getpid()}


def partial_result_sender(value, result_path, execute):
    Path(result_path).write_text("[", encoding="utf-8")
    time.sleep(60)


@unittest.skipUnless(sys.platform == "linux", "Production forkserver is Linux-only")
class PreloadedWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = run_forkserver_worker({"name": "ready"}, execute=isolated_request)
        assert result.returncode == 0, result.stderr

    def test_changed_preloaded_sources_or_packages_reject_frozen_runtime(self):
        from harness.web_extract import self_hosted

        loaded = SimpleNamespace(
            PRELOAD_SHA256=hashlib.sha256(
                Path(self_hosted.__file__).with_name("worker_preload.py").read_bytes()
            ).hexdigest(),
            WORKER_SHA256=hashlib.sha256(Path(self_hosted.__file__).read_bytes()).hexdigest(),
            ENVIRONMENT_SHA256="a" * 64,
        )
        with (
            patch.dict(os.environ, {
                "EXTRACTION_WORKER_MODE": "forkserver", "EXTRACTION_MODEL": "synthetic",
                "EXTRACTION_MODEL_BASE": "https://model.example",
            }),
            patch.dict(sys.modules, {"harness.web_extract.worker_preload": loaded}),
            patch.object(self_hosted.importlib.metadata, "version", return_value="synthetic"),
            patch.object(self_hosted, "installed_environment_sha256", return_value="a" * 64),
        ):
            try:
                for field in vars(loaded):
                    before = getattr(loaded, field)
                    self_hosted.local_runtime_identity.cache_clear()
                    self_hosted.runtime_identity("crawl4ai")
                    setattr(loaded, field, "b" * 64)
                    self_hosted.local_runtime_identity.cache_clear()
                    with self.assertRaisesRegex(ValueError, "Preloaded extraction runtime changed"):
                        self_hosted.runtime_identity("crawl4ai")
                    setattr(loaded, field, before)
            finally:
                self_hosted.local_runtime_identity.cache_clear()

    def test_partial_result_delivery_keeps_the_request_deadline(self):
        from harness.web_extract import self_hosted

        started = time.monotonic()
        with patch.object(self_hosted, "forked_request", partial_result_sender):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_forkserver_worker({}, timeout=1, execute=isolated_request)
        self.assertLess(time.monotonic() - started, 5)
        result = run_forkserver_worker({"name": "after delivery timeout"}, execute=isolated_request)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_preload_import_cannot_report_ready(self):
        from harness.web_extract import self_hosted

        with TemporaryDirectory() as directory:
            Path(directory, "broken_preload.py").write_text(
                "raise ImportError('synthetic missing SDK dependency')\n", encoding="utf-8"
            )
            code = (
                "import multiprocessing; "
                "from unittest.mock import patch; "
                "from harness.web_extract import self_hosted as s; "
                "c=multiprocessing.get_context('forkserver'); "
                "c.set_forkserver_preload(['broken_preload']); "
                "p=patch.object(s,'worker_context',return_value=c); p.start(); "
                "r=s.run_forkserver_worker({},execute=s.preload_ready); "
                "assert r.returncode != 0 and 'preload did not complete' in r.stderr, r"
            )
            source_root = str(Path(self_hosted.__file__).resolve().parents[2])
            result = subprocess.run(
                [sys.executable, "-c", code], cwd=directory,
                env={**os.environ, "PYTHONPATH": directory + os.pathsep + source_root},
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_completed_requests_share_no_mutable_state(self):
        import json

        outputs = []
        for name in ["first", "second"]:
            value = {"name": name}
            result = run_forkserver_worker(value, execute=isolated_request)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["seen"], [name])
            self.assertEqual(value, {"name": name})
            outputs.append(output)
        self.assertNotEqual(outputs[0]["pid"], outputs[1]["pid"])

    def test_crashed_request_does_not_poison_the_next_request(self):
        with TemporaryDirectory() as directory:
            pid_file = Path(directory) / "browser.pid"
            result = run_forkserver_worker(
                {"name": "crashed", "crash": True, "spawn": True, "pid_file": str(pid_file)},
                execute=isolated_request,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("without a response", result.stderr)
            self.assertTrue(pid_file.exists())
            self.assertFalse(psutil.pid_exists(int(pid_file.read_text(encoding="utf-8"))))
        result = run_forkserver_worker({"name": "next"}, execute=isolated_request)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_timeout_removes_detached_browser_descendants_and_allows_next_request(self):
        with TemporaryDirectory() as directory:
            pid_file = Path(directory) / "browser.pid"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_forkserver_worker(
                    {"name": "timeout", "sleep": True, "pid_file": str(pid_file)},
                    timeout=2,
                    execute=isolated_request,
                )
            self.assertTrue(pid_file.exists())
            pid = int(pid_file.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3
            while psutil.pid_exists(pid) and time.monotonic() < deadline:
                try:
                    if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.NoSuchProcess:
                    break
                time.sleep(0.05)
            if psutil.pid_exists(pid):
                self.assertEqual(psutil.Process(pid).status(), psutil.STATUS_ZOMBIE)
        result = run_forkserver_worker({"name": "after timeout"}, execute=isolated_request)
        self.assertEqual(result.returncode, 0, result.stderr)
