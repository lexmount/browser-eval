"""Literal evidence must support an accepted alternative before publication."""
import tempfile
import unittest
from pathlib import Path

import pytest


class AnnotationEvidenceTests(unittest.TestCase):
    def test_evidence_binding_accepts_one_declared_alternative_and_rejects_none(self):
        from lexbench_webfetch.reference_quality import bind_annotation_evidence
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / 'sources/example/stable-page.txt'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('下载', encoding='utf-8')
            case = {
                'id': 'example',
                'acceptance_contract': {'stable_text_anchors': [{
                    'id': 'download', 'alternatives': ['Download', '下载'],
                }]},
                'source_benchmark': {'stable_annotation': {
                    'anchor_evidence': 'sources/example/stable-page.txt',
                }},
            }
            bind_annotation_evidence(case, root)
            self.assertRegex(
                case['source_benchmark']['stable_annotation']['anchor_evidence_sha256'],
                r'^[0-9a-f]{64}$')
            evidence.write_text('Unrelated', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'lacks independent evidence'):
                bind_annotation_evidence(case, root)


def test_review_manifest_resolves_once_and_still_checks_other_parents(tmp_path, monkeypatch):
    from lexbench_webfetch import benchmark

    calls = []
    monkeypatch.setattr(benchmark, 'definitions', lambda: calls.append(True) or tmp_path)
    files = {f'review-evidence/{i}.txt': 'a' * 64 for i in range(100)}
    definition = {'parents': {'selected': {}, 'other': {}},
                  'review_inputs': {'selected': files, 'other': {'review-evidence/other.txt': 'b' * 64}}}
    assert benchmark.review_input_files(definition, 'selected') == files
    assert len(calls) == 1
    definition['review_inputs']['other']['../outside.txt'] = 'c' * 64
    with pytest.raises(ValueError):
        benchmark.review_input_files(definition, 'selected')
