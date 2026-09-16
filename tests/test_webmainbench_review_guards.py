import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_webmainbench_report import check_teds_warnings
from scripts.review_webmainbench_teds_correction import review
from harness.web_fetch.webmainbench_diagnostics import validate_teds_revision, validate_teds_revision_if_present


class WebMainBenchReviewGuardsTests(unittest.TestCase):
    def test_revision_rejects_even_in_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'original').write_text('{}')
            (root / 'linked').symlink_to(root / 'original')
            with self.assertRaisesRegex(ValueError, 'symlinked'):
                validate_teds_revision(root)

    def test_revision_marker_cannot_bypass_validation_by_removing_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(validate_teds_revision_if_present(root, [{'metrics': {}}]))
            with self.assertRaises(FileNotFoundError):
                validate_teds_revision_if_present(root, [{'metrics': {}, 'official_metrics': {}}])
            (root / 'teds-correction.json').write_text('{}')
            with patch('harness.web_fetch.webmainbench_diagnostics.validate_teds_revision') as validate:
                validate_teds_revision_if_present(root, [{'metrics': {}}])
                validate.assert_called_once_with(root)

    def test_teds_warning_is_required_in_each_language(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pages = {
                'index.html': 'overall（待校验）',
                'review/index.html': '98 条 TEDS 记录；评分有效性尚未通过',
                'en/index.html': 'overall (under review)',
                'en/review/index.html': '98 TEDS records; score validity did not',
            }
            for name, content in pages.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            check_teds_warnings(root, 98)
            for name, content in pages.items():
                with self.subTest(page=name):
                    (root / name).write_text('')
                    with self.assertRaisesRegex(ValueError, 'validity warning'):
                        check_teds_warnings(root, 98)
                    (root / name).write_text(content)

    def test_review_rejects_source_paths_outside_snapshot_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, corrected = root / 'source', root / 'corrected'
            source.mkdir()
            (corrected / 'correction-source').mkdir(parents=True)
            for path in (source / 'results.jsonl', corrected / 'results.jsonl', corrected / 'dataset.json'):
                path.write_bytes(b'{}')
            expected = hashlib.sha256(b'{}').hexdigest()
            (corrected / 'outside.py').write_bytes(b'{}')
            (corrected / 'correction-source/linked.py').symlink_to(corrected / 'outside.py')
            for relative in ('../outside.py', str(corrected / 'outside.py'), 'linked.py'):
                receipt = {'provider_calls': 0, 'input_results_sha256': expected,
                           'output_results_sha256': expected, 'dataset_sha256': expected,
                           'source_code_sha256': {relative: expected}}
                (corrected / 'teds-correction.json').write_text(json.dumps(receipt))
                with self.subTest(path=relative), patch(
                    'scripts.review_webmainbench_teds_correction.valid_correction_provenance', return_value=True
                ), self.assertRaises(ValueError):
                    review(source, corrected)

    def test_review_rejects_escaping_raw_output_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, corrected = root / 'source', root / 'corrected'
            source.mkdir()
            corrected.mkdir()
            outside = root / 'outside.md'
            outside.write_bytes(b'answer')
            (corrected / 'linked.md').symlink_to(outside)
            for name in ('dataset.json', 'sample-manifest.json', 'physical-attempts.jsonl',
                         'execution.json', 'result-review.json'):
                for base in (source, corrected):
                    (base / name).write_text('{}')
            for relative in ('../outside.md', str(outside), 'linked.md'):
                before = [{'attempt_id': str(i), 'ok': False, 'metrics': {},
                           'artifact_path': relative, 'output_sha256': hashlib.sha256(b'answer').hexdigest()}
                          for i in range(1000)]
                original = '\n'.join(json.dumps(row) for row in before).encode()
                final = '\n'.join(json.dumps({**row, 'official_metrics': {}}) for row in before).encode()
                (source / 'results.jsonl').write_bytes(original)
                (corrected / 'original-results.jsonl').write_bytes(original)
                (corrected / 'results.jsonl').write_bytes(final)
                receipt = {'provider_calls': 0, 'input_results_sha256': hashlib.sha256(original).hexdigest(),
                           'output_results_sha256': hashlib.sha256(final).hexdigest(),
                           'dataset_sha256': hashlib.sha256(b'{}').hexdigest(), 'source_code_sha256': {}}
                (corrected / 'teds-correction.json').write_text(json.dumps(receipt))
                with self.subTest(path=relative), patch(
                    'scripts.review_webmainbench_teds_correction.valid_correction_provenance', return_value=True
                ), self.assertRaises(ValueError):
                    review(source, corrected)


if __name__ == '__main__':
    unittest.main()
