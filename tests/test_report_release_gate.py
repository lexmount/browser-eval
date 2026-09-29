from pathlib import Path
import subprocess

import pytest

from scripts.report_release_gate import ROOT, check_report_release


def test_release_gate_runs_data_links_and_revision_before_upload(tmp_path):
    source = tmp_path / "report"
    source.mkdir()
    profile = ROOT / "evaluation/current-report.json"
    calls = []

    def run(argv, *, check):
        assert check is True
        calls.append(argv)

    check_report_release(source, profile, run=run)
    assert [Path(argv[1]).name for argv in calls] == [
        "check_report.py", "check_report_links.py", "check_report_revision.py"
    ]
    assert calls[0][-2:] == ["--profile", str(profile.resolve())]


def test_release_gate_stops_at_failed_check(tmp_path):
    source = tmp_path / "report"
    source.mkdir()
    calls = []

    def run(argv, *, check):
        calls.append(argv)
        raise subprocess.CalledProcessError(1, argv)

    with pytest.raises(subprocess.CalledProcessError):
        check_report_release(source, ROOT / "evaluation/current-report.json", run=run)
    assert len(calls) == 1


def test_release_gate_rejects_missing_or_foreign_profile(tmp_path):
    source = tmp_path / "report"
    source.mkdir()
    with pytest.raises(ValueError, match="missing"):
        check_report_release(source, tmp_path / "absent.json")
    foreign = tmp_path / "profile.json"
    foreign.write_text("{}")
    with pytest.raises(ValueError, match="under evaluation"):
        check_report_release(source, foreign)
