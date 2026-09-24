import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_report.py"


def test_local_audit_requires_explicit_evidence_and_output():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--local-audit"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "requires both --evidence and --output" in result.stderr


def test_local_audit_rejects_release_profile():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--local-audit",
            "--evidence",
            "/tmp/evidence",
            "--output",
            "/tmp/report",
            "--profile",
            "evaluation/current-report.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "cannot be combined with a release profile" in result.stderr
