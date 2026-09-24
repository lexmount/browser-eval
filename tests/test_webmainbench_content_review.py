from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.review_webmainbench_content import run_packet, sha, validate_review, write


class ContentReviewTests(unittest.TestCase):
    def test_resume_reuses_review_only_while_all_evidence_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            sources = {}
            for name in ['source', 'outline', 'reference', 'target', 'chromium']:
                path = work / name
                path.write_text('literal content')
                sources[name] = {'path': str(path), 'sha256': sha(path)}
            case = {'case_id': 'one', 'target': sources['target'],
                    'comparisons': {'chromium': sources['chromium']}}
            for name, key in [('source', 'source_html'), ('outline', 'source_outline'),
                              ('reference', 'reference')]:
                case[key] = sources[name]['path']
                case[name + '_sha256'] = sources[name]['sha256']
            packet = work / 'packet.json'
            write(packet, {'cases': [case]})
            item = {'path': str(packet), 'sha256': sha(packet)}
            review = {'cases': [{'case_id': 'one', 'page_purpose': 'article',
                                 'expected_content': 'body', 'verdict': 'acceptable',
                                 'coverage_evidence': ['beginning', 'ending'], 'issues': []}]}
            write(work / 'reviews' / packet.name, review)
            with patch('scripts.review_webmainbench_content.subprocess.run') as call:
                self.assertEqual(run_packet(item, work, 'model', 'codex'), review)
                call.assert_not_called()
                for name in sources:
                    path = Path(sources[name]['path'])
                    path.write_text('changed')
                    with self.assertRaisesRegex(ValueError, 'Review input changed'):
                        run_packet(item, work, 'model', 'codex')
                    path.write_text('literal content')
                call.assert_not_called()

    def test_review_cannot_omit_or_repeat_a_page(self):
        packet = {'cases': [{'case_id': 'one'}, {'case_id': 'two'}]}
        for rows in [[], [{'case_id': 'one'}], [{'case_id': 'one'}, {'case_id': 'one'}]]:
            with self.assertRaisesRegex(ValueError, 'exact page inventory'):
                validate_review({'cases': rows}, packet)

    def test_review_cannot_invent_an_output_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'output.md'
            path.write_text('actual content')
            packet = {'cases': [{'case_id': 'one', 'target': {'path': str(path)}}]}
            review = {'cases': [{'case_id': 'one', 'page_purpose': 'article',
                                 'expected_content': 'body', 'verdict': 'defect',
                                 'coverage_evidence': ['beginning', 'ending'],
                                 'issues': [{'description': 'problem', 'source_locator': 'p',
                                             'output_quote': 'invented text'}]}]}
            with self.assertRaisesRegex(ValueError, 'not literal'):
                validate_review(review, packet)
