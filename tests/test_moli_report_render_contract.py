"""Exercise the report's public fixed-layout comparison components."""

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
        script = root / "harness/lexbench/assets/render_contract.cjs"
        subprocess.run(
            [node, str(script), "--app", str(root / "scripts/moli_report/app.js")],
            check=True,
            capture_output=True,
            text=True,
        )
