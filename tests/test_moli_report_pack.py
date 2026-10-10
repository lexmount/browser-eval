"""The hosted evidence package must preserve every logical file byte for byte."""

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.lexbench import pack as MODULE


class MoliReportPackTests(unittest.TestCase):
    def create_report(self, root):
        root.mkdir()
        for name, prefix in [("index.html", ""), ("en.html", ""), ("en/index.html", "../")]:
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(f'<html><script defer src="{prefix}mermaid.min.js"></script></html>')
        for name in ("app.js", "style.css", "mermaid.min.js", "mermaid-LICENSE.txt"):
            (root / name).write_text(name)
        (root / "data").mkdir()
        contents = {
            "data/model.json": '{"message":"中文证据"}'.encode(),
            "data/error.log": b"exact error\n" * 10,
            "data/frame.bin": bytes(range(256)),
        }
        for name, data in contents.items():
            (root / name).write_bytes(data)
        return contents

    def test_round_trip_binary_unicode_and_pack_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = self.create_report(root / "source")
            summary = MODULE.package(root / "source", root / "package", pack_limit=80)
            self.assertEqual(summary["bundled_files"], 3)
            self.assertGreater(summary["packs"], 1)
            catalog = json.loads((root / "package/bundle-manifest.json").read_text())["files"]
            self.assertEqual(set(catalog), set(expected))
            for name, entry in catalog.items():
                payload = gzip.decompress((root / "package" / entry["pack"]).read_bytes())
                data = payload[entry["offset"] : entry["offset"] + entry["size"]]
                self.assertEqual(data, expected[name])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
            for name in ("index.html", "en.html", "en/index.html"):
                self.assertIn("bundle-loader.js", (root / "package" / name).read_text())
            with self.assertRaisesRegex(ValueError, "already exists"):
                MODULE.package(root / "source", root / "package")

    def test_rejects_symlinked_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_report(root / "source")
            (root / "outside").write_text("not public")
            (root / "source/data/link").symlink_to(root / "outside")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                MODULE.package(root / "source", root / "package")
            self.assertFalse((root / "package").exists())

    def test_chinese_notice_is_physical_and_works_without_runtime(self):
        from harness.report_languages import write_language_contract

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            self.create_report(source)
            (source / "en/index.html").unlink()
            write_language_contract(source, include_english=False)
            notice = (source / "en/index.html").read_text()
            (source / "en.html").write_text(
                notice.replace('href="../index.html"', 'href="index.html"')
            )
            MODULE.package(source, root / "package")
            for name in ("en.html", "en/index.html"):
                self.assertEqual((root / "package" / name).read_text(), (source / name).read_text())
                self.assertNotIn("bundle-loader.js", (root / "package" / name).read_text())
            self.assertTrue((root / "package/report-languages.json").is_file())

    def test_rejects_symlinked_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_report(root / "source")
            (root / "linked-source").symlink_to(root / "source", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                MODULE.package(root / "linked-source", root / "package")
            self.assertFalse((root / "package").exists())

    def test_invalid_html_does_not_leave_a_partial_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_report(root / "source")
            (root / "source/en/index.html").write_text("<html>missing runtime</html>")
            with self.assertRaisesRegex(ValueError, "runtime marker"):
                MODULE.package(root / "source", root / "package")
            self.assertFalse((root / "package").exists())

    def test_rejects_output_inside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            self.create_report(source)
            with self.assertRaisesRegex(ValueError, "inside"):
                MODULE.package(source, source / "package")
            self.assertFalse((source / "package").exists())

    def test_pack_write_failure_does_not_leave_a_partial_package(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_report(root / "source")
            with patch.object(MODULE, "write_packs", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    MODULE.package(root / "source", root / "package")
            self.assertFalse((root / "package").exists())
            self.assertEqual({p.name for p in root.iterdir()}, {"source"})

    def test_destination_created_during_packaging_is_preserved(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_report(root / "source")
            original = MODULE.write_packs

            def concurrent_writer(*args, **kwargs):
                (root / "package").mkdir()
                (root / "package/owned").write_text("another process")
                return original(*args, **kwargs)

            with patch.object(MODULE, "write_packs", side_effect=concurrent_writer):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    MODULE.package(root / "source", root / "package")
            self.assertEqual((root / "package/owned").read_text(), "another process")
            self.assertEqual(list((root / "package").iterdir()), [root / "package/owned"])

    def test_rejects_dangling_destination_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_report(root / "source")
            (root / "package").symlink_to(root / "absent", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "already exists"):
                MODULE.package(root / "source", root / "package")
            self.assertTrue((root / "package").is_symlink())
            self.assertFalse((root / "absent").exists())

    def test_command_help_outside_checkout(self):
        import subprocess
        import sys

        script = Path(__file__).resolve().parents[1] / "scripts/moli_report/pack.py"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertIn("--source", result.stdout)
        self.assertIn("--output", result.stdout)
