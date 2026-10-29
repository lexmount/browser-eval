"""Exercise the report's public fixed-layout comparison components."""

from harness.distribution import source_path

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


class MoliReportRenderContractTests(unittest.TestCase):
    def test_fixed_layout_comparisons_in_both_languages(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for report component checks")
        root = Path(__file__).resolve().parents[1]
        script = source_path("harness/lexbench/assets/render_contract.cjs", root=root)
        subprocess.run(
            [node, str(script), "--app", str(root / "scripts/moli_report/app.js")],
            check=True,
            capture_output=True,
            text=True,
        )


def test_catalog_editions_have_matching_keys_and_parameters():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "scripts/moli_report/messages.json").read_text())
    english = catalog["en"]["messages"]
    chinese = catalog["zh"]["messages"]
    assert english.keys() == chinese.keys()
    for key in english:
        assert set(re.findall(r"\{value\d+\}", english[key])) == set(
            re.findall(r"\{value\d+\}", chinese[key]))
