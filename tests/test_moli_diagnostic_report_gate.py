import hashlib
import json

import pytest

from scripts.check_moli_diagnostic_report import check_report


def _site(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    rows = []
    for case_id, passed in (("pass-case", True), ("failed-case", False)):
        payload = f"Saved complete answer for {case_id}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        for suffix in ("first", "diagnostic"):
            (evidence / f"{case_id}-{suffix}.txt").write_bytes(payload)
        rows.append({
            "id": case_id, "family": "dynamic", "initial_pass": passed,
            "diagnostic_pass": passed, "stage": "首轮通过" if passed else "未通过",
            "source_status": None if passed else "source_met",
            "source_evidence_sha256": None if passed else "a" * 64,
            "source_checked_at": None if passed else "2026-09-29T12:00:00+00:00",
            "failed_checks": [] if passed else ["too_few_complete_records"],
            "answer_file": f"evidence/{case_id}-diagnostic.txt",
            "first_answer_file": f"evidence/{case_id}-first.txt",
            "answer_sha256": digest, "first_answer_sha256": digest,
        })
    document = {
        "schema": "moli_webfetch_111_diagnostic/2", "rows": rows,
        "counts": {"dynamic:首轮通过": 1, "dynamic:未通过": 1},
        "calls": {"uniform_first_pass": 2},
        "source_review_counts": {"source_met": 1},
    }
    (tmp_path / "moli-111-audit.json").write_text(json.dumps(document))
    (tmp_path / "index.html").write_text('''<!doctype html><html><head></head><body>
      <div data-first-pass="1" data-case-count="2"><strong>1 / 2</strong></div>
      <td data-remaining-failures="1">1</td>
      <td data-source-status="source_met" data-count="1">1</td>
      <td data-source-status="source_unmet" data-count="0">0</td>
      <td data-source-status="source_unavailable" data-count="0">0</td>
      <table class="result-table"><tr><td><button data-open="pass-case"></button></td></tr>
        <tr><td><button data-open="failed-case"></button></td></tr></table>
      <dialog id="case-dialog"></dialog><dl id="dialog-facts"></dl><pre id="dialog-excerpt"></pre>
      <a id="dialog-complete-output"></a><a id="dialog-first-output"></a>
      <a href="moli-111-audit.json"></a></body></html>''')
    return document


def test_diagnostic_release_gate_binds_case_outputs_and_reader_totals(tmp_path):
    _site(tmp_path)
    assert check_report(tmp_path, expected_cases=2)["remaining_failures"] == 1


def test_diagnostic_release_gate_rejects_missing_independent_failure_evidence(tmp_path):
    document = _site(tmp_path)
    document["rows"][1]["source_evidence_sha256"] = None
    (tmp_path / "moli-111-audit.json").write_text(json.dumps(document))
    with pytest.raises(ValueError, match="independent source evidence"):
        check_report(tmp_path, expected_cases=2)


def test_diagnostic_release_gate_rejects_mismatched_saved_output(tmp_path):
    _site(tmp_path)
    (tmp_path / "evidence/failed-case-diagnostic.txt").write_text("different")
    with pytest.raises(ValueError, match="Evidence hash mismatch"):
        check_report(tmp_path, expected_cases=2)


def test_diagnostic_release_gate_rejects_wrong_reader_count_or_missing_case(tmp_path):
    _site(tmp_path)
    page = tmp_path / "index.html"
    page.write_text(page.read_text().replace('data-remaining-failures="1"', 'data-remaining-failures="2"'))
    with pytest.raises(ValueError, match="Reader-facing failure count"):
        check_report(tmp_path, expected_cases=2)
    page.write_text(page.read_text().replace('data-remaining-failures="2"', 'data-remaining-failures="1"')
                    .replace('data-open="failed-case"', 'data-open="unknown-case"'))
    with pytest.raises(ValueError, match="case inventory"):
        check_report(tmp_path, expected_cases=2)
