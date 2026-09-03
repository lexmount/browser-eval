import os
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.report_interfaces import SPECS
from harness.web_fetch.report_methods import PROFILES


@unittest.skipUnless(os.name == "posix", "executable fixture uses POSIX shebang")
class MoliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.binary = Path(self.directory.name) / "moli fixture"
        self.case = EvalCase("example", "Example", "https://example.com/?a=1&b=2", "static", [])

    def provider(self, script, **config):
        self.binary.write_text("#!" + sys.executable + "\n" + script)
        self.binary.chmod(0o700)
        return build_provider(
            "moli-webfetch", dict(type="moli_webfetch", binary=str(self.binary), **config)
        )

    def test_cli_contract_and_diagnostics(self):
        provider = self.provider(
            'import sys\nassert sys.argv[1:]==["fetch","--dump","markdown","--wait-until","done","--timeout","2000","--block-private-networks","https://example.com/?a=1&b=2"]\nprint("# Example Domain")\nprint("diagnostic only",file=sys.stderr)\n'
        )
        self.assertEqual(provider.validate(), [])
        result = provider.fetch(self.case, 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "# Example Domain\n")
        self.assertIn("diagnostic only", result.metadata["stderr"])
        self.assertIsNone(result.status_code)
        self.assertIsNone(result.final_url)
        self.assertEqual(len(result.metadata["binary_sha256"]), 64)

    def test_failure_empty_binary_and_oversized_output(self):
        for script, config, expected in [
            ('import sys\nprint("partial")\nsys.exit(7)', {}, "tool_error"),
            ('print(" ")', {}, "empty_output"),
            ('import sys\nsys.stdout.buffer.write(b"\\xff")', {}, "invalid_output"),
            ('print("abcdef")', {"max_bytes": 3}, "output_too_large"),
            ('import sys\nprint("abcdef",file=sys.stderr)', {"max_bytes": 3}, "output_too_large"),
        ]:
            with self.subTest(expected=expected):
                result = self.provider(script, **config).fetch(self.case, 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_type, expected)

    def test_timeout_is_failure(self):
        result = self.provider("import time\ntime.sleep(10)").fetch(self.case, 0.05)
        self.assertEqual(result.error_type, "timeout")

    def test_url_and_config_validation_before_process(self):
        provider = self.provider('raise AssertionError("should not run")')
        with patch("harness.web_fetch.providers.moli.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                provider.fetch(EvalCase("x", "x", "file:///etc/passwd", "x", []), 1)
            process.assert_not_called()
        self.assertIn("wait_until", self.provider("", wait_until="invalid").validate())
        self.assertTrue(
            build_provider(
                "moli-webfetch", {"type": "moli_webfetch", "binary": "/nonexistent/moli"}
            ).validate()
        )

    def test_registration_documented(self):
        self.assertIn("moli-webfetch", SPECS)
        self.assertIn("moli-webfetch", PROFILES)

    def test_binary_resolved_once_per_fetch(self):
        provider = self.provider('print("ok")')
        with patch(
            "harness.web_fetch.providers.moli.shutil.which", side_effect=[str(self.binary), None]
        ) as which:
            result = provider.fetch(self.case, 2)
            self.assertTrue(result.ok, repr(result))
            self.assertEqual(which.call_count, 1)
            self.assertEqual(provider.fetch(self.case, 2).error_type, "configuration")

    def test_binary_replacement_updates_audit_hash(self):
        provider = self.provider('print("first")')
        provider.validate()
        first = provider.fetch(self.case, 2)
        self.provider('print("second")')  # Replace the executable at the same path.
        provider.validate()  # Revalidate the changed runtime before measuring.
        second = provider.fetch(self.case, 2)
        self.assertEqual(second.content, "second\n")
        self.assertNotEqual(first.metadata["binary_sha256"], second.metadata["binary_sha256"])
        self.assertEqual(
            second.metadata["binary_sha256"], hashlib.sha256(self.binary.read_bytes()).hexdigest()
        )

    def test_disappearance_and_permission_errors_become_records(self):
        provider = self.provider('print("ok")')
        with patch(
            "harness.web_fetch.providers.moli.open", side_effect=FileNotFoundError("removed")
        ):
            self.assertIn('binary (unreadable)', provider.validate())
        with patch(
            "harness.web_fetch.providers.moli.subprocess.Popen",
            side_effect=PermissionError("not executable"),
        ):
            self.assertEqual(provider.fetch(self.case, 2).error_type, "configuration")
