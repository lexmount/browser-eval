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


def test_saved_matrix_rejects_mixed_build_modes():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--saved-matrix', '/tmp/spec.json',
         '--output', '/tmp/report', '--local-audit'],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert 'cannot be combined with other build modes' in result.stderr


def test_saved_matrix_check_cannot_generate_or_replace_report():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), '--check-saved-matrix', '/tmp/report',
         '--output', '/tmp/another'],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert 'cannot be combined with build options' in result.stderr
