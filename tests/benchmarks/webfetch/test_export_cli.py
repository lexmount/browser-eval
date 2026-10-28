"""Export summaries distinguish written datasets from verified references."""
import json
import sys

import pytest

from lexbench_webfetch import cli


@pytest.mark.parametrize("revision", [None, "review-test"])
def test_export_cli_reports_reference_verification(tmp_path, monkeypatch, capsys, revision):
    output = tmp_path / "export"
    def export(suite, source, target, **options):
        target.mkdir()
        report = {
            "review_inputs": {"external.html": "unused-test-hash"},
            "reference_controls": {
                "complete": False, "changed_rules_complete": False,
                "cases": [
                    {"status": "passed", "reference_candidates": []},
                    {"status": "unavailable", "reference_candidates": [
                        {"source": {"path": "dataset.json"}, "passed": False},
                        {"source": {"path": "frozen.html"}, "passed": False}]},
                    {"status": "unavailable", "reference_candidates": [
                        {"source": {"path": "external.html"}, "passed": False}]},
                    {"status": "unavailable", "reference_candidates": []},
                ],
            },
        }
        if revision:
            (target / "annotation-review.json").write_text(json.dumps(report))
        return target / "dataset.json"
    monkeypatch.setattr(cli, "export", export)
    arguments = ["lexbench-webfetch", "export", "--suite", "test", "--source",
                 str(tmp_path), "--output", str(output)]
    if revision:
        arguments += ["--revision", revision]
    monkeypatch.setattr(sys, "argv", arguments)
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["dataset"] == str(output / "dataset.json")
    if revision:
        assert result["verification"] == {
            "passed": 1, "unverified": 3, "unverified_with_failed_frozen_reference": 1,
            "complete": False, "changed_rules_complete": False,
        }
    else:
        assert "verification" not in result
