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
    def test_cancellation_between_popen_and_handle_registration_is_latched(self):
        import subprocess
        provider=self.provider('import time\ntime.sleep(20)')
        original=subprocess.Popen
        children=[]
        def launch(*args,**kwargs):
            process=original(*args,**kwargs)
            children.append(process)
            provider.cancel()  # Real child exists but fetch has not received its handle.
            return process
        provider.begin_call()
        with patch('harness.web_fetch.providers.moli.subprocess.Popen',side_effect=launch):
            result=provider.fetch(self.case,1)
        self.assertFalse(result.ok)
        self.assertIsNotNone(children[0].poll())

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

    def test_file_task_preserves_binary_stdout(self):
        from dataclasses import replace
        provider = self.provider(
            'import sys\n'
            'assert "--dump" not in sys.argv\n'
            'assert "--wait-until" not in sys.argv\n'
            'sys.stdout.buffer.write(b"\\x89PNG\\r\\n\\x1a\\n" + b"x"*40)'
        )
        result = provider.fetch(replace(self.case, task_kind="file"), 2)
        self.assertTrue(result.ok)
        self.assertEqual(result.files[0]["data"], b"\x89PNG\r\n\x1a\n" + b"x"*40)
        self.assertEqual(result.content, "")
        self.assertIsNone(result.metadata["dump"])
        self.assertIsNone(result.metadata["wait_until"])

    def test_direct_pdf_body_uses_raw_fetch_and_extracts_inside_call(self):
        from dataclasses import replace
        import io
        from pypdf import PdfWriter
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(output)
        encoded = repr(output.getvalue())
        provider = self.provider(
            'import sys\n'
            'assert "--dump" not in sys.argv\n'
            'assert "--wait-until" not in sys.argv\n'
            f'sys.stdout.buffer.write({encoded})'
        )
        document = replace(self.case, url="https://example.com/fixed.pdf", track="document")
        result = provider.fetch(document, 2)
        self.assertFalse(result.ok)  # A blank but valid PDF has no answer text.
        self.assertEqual(result.error_type, "empty_output")
        self.assertEqual(result.metadata["document_extractor"], "pypdf")
        self.assertEqual(result.metadata["dump"], None)
        self.assertIn("_raw_stdout_b64", result.metadata)

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
