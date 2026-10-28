"""Literal evidence must support an accepted alternative before publication."""
import tempfile
import unittest
from pathlib import Path


class AnnotationEvidenceTests(unittest.TestCase):
    def test_evidence_binding_accepts_one_declared_alternative_and_rejects_none(self):
        from lexbench_webfetch.reference_quality import bind_annotation_evidence
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / 'sources/example/stable-page.txt'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('下载')
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
            evidence.write_text('Unrelated')
            with self.assertRaisesRegex(ValueError, 'lacks independent evidence'):
                bind_annotation_evidence(case, root)
