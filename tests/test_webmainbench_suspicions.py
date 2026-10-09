"""Regression checks for cross-layer public-benchmark anomaly signals."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.webmainbench_diagnostics import scan_suspicions
from harness.web_fetch.report import _wmb_error_page
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE, require_valid_teds_for_release


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class WebMainBenchSuspicionTests(unittest.TestCase):
    def test_release_rejects_impossible_teds_but_allows_valid_zero(self):
        def record(distance):
            return {'metrics': {'table_TEDS': {'score': 0, 'details': {
                'predicted_nodes': 5, 'groundtruth_nodes': 570, 'edit_distance': distance}}}}
        with self.assertRaisesRegex(ValueError, '1 impossible TEDS'):
            require_valid_teds_for_release([record(.9)])
        require_valid_teds_for_release([record(570)])
        require_valid_teds_for_release([{'metrics': {}}])

    def test_report_and_audit_share_error_page_detection(self):
        for text, expected in [("This site can’t be reached\nexample.test", True),
                               ("# 无法访问此网站\nERR_ACCESS_DENIED", True),
                               ("An article explaining why This site can't be reached appears", False),
                               ("# A normal article\nUseful body", False)]:
            self.assertEqual(_wmb_error_page(text), expected)
            self.assertEqual(bool(ERROR_PAGE.search(text)), expected)

    def test_flags_impossible_tree_distance_and_error_page_without_a_provider_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            (evidence / "artifacts").mkdir(parents=True)
            source = root / "source.jsonl"
            html = '<script>window.location.search += "&nocdn=true"</script>'
            reference = "| A | B |\n|---|---|\n| 1 | 2 |"
            case = {"track_id": "case-1", "html": html, "groundtruth_content": reference}
            source.write_text(json.dumps(case) + "\n", encoding="utf-8")
            dataset = {"cases": [{"id": "case-1", "input_html_sha256": sha(html),
                                   "reference_sha256": sha(reference)}]}
            (evidence / "dataset.json").write_text(json.dumps(dataset), encoding="utf-8")
            output = "# Error response\nError code: 404"
            (evidence / "artifacts" / "out.md").write_text(output, encoding="utf-8")
            record = {"attempt_id": "method__case-1__r001", "provider": "method", "case_id": "case-1",
                      "artifact_path": "artifacts/out.md", "output_sha256": sha(output), "ok": True, "provider_metadata": {},
                      "metrics": {"table_TEDS": {"success": True, "details": {
                          "predicted_nodes": 5, "groundtruth_nodes": 570, "edit_distance": 0.9}}}}
            (evidence / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            (evidence / "physical-attempts.jsonl").write_text(
                json.dumps({"provider": "method", "track_id": "case-1"}) + "\n", encoding="utf-8")
            result = scan_suspicions(evidence, source)
            signals = set(result["results"][0]["signals"])
            self.assertEqual(result["flagged_results"], 1)
            self.assertIn("teds-distance-below-node-count-lower-bound", signals)
            self.assertIn("error-page-counted-as-output", signals)
            self.assertIn("fixture-query-navigation-candidate", signals)
            self.assertEqual(result["kind"], "investigation_signals_not_verdicts")


if __name__ == "__main__":
    unittest.main()


def suspicion_fixture(root):
    evidence = root / 'evidence'
    evidence.mkdir()
    source = root / 'source.jsonl'
    html = '<main>' + 'synthetic body ' * 80000 + '</main>'
    reference = 'synthetic body'
    source.write_text(json.dumps({'track_id': 'one', 'html': html,
                                 'groundtruth_content': reference}) + '\n')
    (evidence / 'dataset.json').write_text(json.dumps({'cases': [{
        'id': 'one', 'input_html_sha256': sha(html), 'reference_sha256': sha(reference)}]}))
    rows = []
    for provider, ok in [('custom', False), ('other', False), ('successful', True)]:
        output = 'synthetic output'
        (evidence / f'{provider}.md').write_text(output)
        rows.append({'provider': provider, 'case_id': 'one', 'attempt_id': provider + '__one',
                     'ok': ok, 'artifact_path': f'{provider}.md', 'output_sha256': sha(output),
                     'metrics': {}})
    (evidence / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (evidence / 'physical-attempts.jsonl').write_text(''.join(json.dumps({
        'provider': row['provider'], 'track_id': 'one'}) + '\n' for row in rows))
    return evidence, source


def test_large_inline_failure_signal_is_provider_independent(tmp_path):
    evidence, source = suspicion_fixture(tmp_path)
    before = (evidence / 'results.jsonl').read_bytes()
    result = scan_suspicions(evidence, source)
    flagged = {row['provider']: row['signals'] for row in result['results']}
    for provider in ('custom', 'other'):
        assert 'large-inline-html-delivery-failure' in flagged[provider]
    assert 'large-inline-html-delivery-failure' not in flagged.get('successful', [])
    assert (evidence / 'results.jsonl').read_bytes() == before


def test_scan_rejects_changed_or_unbound_output_and_duplicate_results(tmp_path):
    import pytest

    evidence, source = suspicion_fixture(tmp_path)
    output = evidence / 'custom.md'
    output.write_text('tampered output')
    with pytest.raises(ValueError, match='hash binding'):
        scan_suspicions(evidence, source)
    output.write_text('synthetic output')
    results = evidence / 'results.jsonl'
    original = results.read_text()
    rows = [json.loads(line) for line in original.splitlines()]
    del rows[0]['output_sha256']
    results.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(ValueError, match='hash binding'):
        scan_suspicions(evidence, source)
    results.write_text(original + original.splitlines()[0] + '\n')
    with pytest.raises(ValueError, match='one result per provider'):
        scan_suspicions(evidence, source)


def test_scan_command_preserves_existing_output_before_reading_evidence(tmp_path):
    import pytest
    from harness.web_fetch.webmainbench_diagnostics import suspicion_main

    output = tmp_path / 'scan.json'
    output.write_bytes(b'existing scan')
    with pytest.raises(ValueError, match='Refusing to overwrite'):
        suspicion_main(['--evidence', str(tmp_path / 'absent-evidence'),
                        '--source-dataset', str(tmp_path / 'absent-source'),
                        '--output', str(output)])
    assert output.read_bytes() == b'existing scan'


def test_scan_rejects_inputs_changed_during_analysis(tmp_path, monkeypatch):
    import pytest
    from harness.web_fetch import webmainbench_diagnostics as diagnostics

    evidence, source = suspicion_fixture(tmp_path)
    read = diagnostics.read_suspicion_jsonl

    def changing_read(path):
        rows = read(path)
        if path.name == 'results.jsonl':
            source.write_text(source.read_text() + '\n')
        return rows

    monkeypatch.setattr(diagnostics, 'read_suspicion_jsonl', changing_read)
    with pytest.raises(ValueError, match='Evidence changed'):
        scan_suspicions(evidence, source)
