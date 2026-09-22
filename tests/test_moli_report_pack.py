"""The hosted evidence package must preserve every logical file byte for byte."""

import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "moli_report_pack", ROOT / "scripts/moli_report/pack.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
