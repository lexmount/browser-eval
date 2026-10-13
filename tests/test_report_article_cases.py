"""Safety boundaries for article evidence excerpts."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from harness.web_fetch.report_article_cases import (
    CASE_TABS, _dynamic_example, _link, _list_example, _read_output,
    _resource_example, _text_example, render_article_cases,
)


class ArticleCaseTests(unittest.TestCase):
    def test_excerpt_rejects_changed_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'actual.txt').write_bytes(b'original')
            record = {'attempt_id': 'moli__case', 'artifact_path': 'actual.txt',
                      'metrics': {'content_sha256': hashlib.sha256(b'original').hexdigest()}}
            self.assertEqual(_read_output(out, record), 'original')
            (out / 'actual.txt').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                _read_output(out, record)

    def test_reference_urls_cannot_execute_code(self):
        with self.assertRaisesRegex(ValueError, 'HTTP'):
            _link('javascript:alert(1)', 'unsafe')
        link = _link('https://example.com/?x="', '<script>')
        self.assertIn('&lt;script&gt;', link)
        self.assertIn('&quot;', link)
        self.assertNotIn('<script>', link)

    def test_text_evidence_is_escaped_without_rewriting_its_content(self):
        text = '<script>alert("example")</script>'
        case = {'ground_truth_main_content': text, 'min_content_chars': 1,
                'body_requirements': {'minimum_coverage': .8, 'minimum_floor': .6}}
        record = {'metrics': {'fact_results': [], 'content_chars': len(text), 'length_pass': True,
                             'body_region_results': [{'coverage': 1.0}],
                             'body_required_target_regions': 1, 'body_coverage_pass': True}}
        left, right, _, _ = _text_example(case, record, text)
        self.assertIn('&lt;script&gt;', left)
        self.assertIn('&lt;script&gt;', right)
        self.assertNotIn('<script>', left + right)
        record['metrics'].update(forbidden_hits=['Access denied'],
                                 text_encoding_corruption=True,
                                 replacement_character_count=12)
        _, _, checks, _ = _text_example(case, record, text)
        self.assertNotIn('排除错误页', ''.join(checks))
        self.assertIn('12 个替换字符', checks[-1])
        self.assertIn('未通过', checks[-1])

    def test_pdf_exposes_matching_lines_outside_abstract_without_fabricating_them(self):
        text = ('Attention Is All You Need\nThe dominant sequence transduction models are complex. '
                'Some use attention. We propose the Transformer.\n'
                '3.2.1 Scaled Dot-Product Attention\n')
        terms = ['Attention Is All You Need', 'Transformer', 'Scaled Dot-Product Attention']
        case = {'ground_truth_main_content': text, 'min_content_chars': 1,
                'body_requirements': {'minimum_coverage': .8, 'minimum_floor': .6}}
        record = {'metrics': {'fact_results': [dict(alternatives=[t], matched=[t], pass_=True) for t in terms],
                             'content_chars': len(text), 'length_pass': True,
                             'body_region_results': [{'coverage': 1.0}],
                             'body_required_target_regions': 1, 'body_coverage_pass': True}}
        record.update(elapsed_ms=2442.75, provider_metadata={'raw_fetch_ms': 1853.03, 'document_extract_ms': 469.41})
        for item in record['metrics']['fact_results']:
            item['pass'] = item.pop('pass_')
        left, right, _, _ = _text_example(case, record, text, pdf=True)
        for side in (left, right):
            for term in terms:
                self.assertIn('<mark>' + term + '</mark>', side)
            self.assertIn('3.2.1 <mark>Scaled Dot-Product Attention</mark>', side)
        with self.assertRaisesRegex(ValueError, 'fact excerpt is missing'):
            _text_example(case, record, text.replace('Scaled Dot-Product Attention', 'absent'), pdf=True)

    def test_list_keeps_decode_failure_without_prompt_veto(self):
        case = {'acceptance_contract': {'minimum_records': 3, 'hosts': ['example.com'],
                                       'entry_path': '/shop/[^/]+',
                                       'stable_text_anchors': [{'id': 'title', 'alternatives': ['Catalog']}]},
                'forbidden_any': ['Access Denied']}
        output = 'Catalog\n' + '\n'.join(f'[## Item {i}](/shop/{i})' for i in range(3))
        metrics = dict(stable_anchor_hits=1, stable_anchor_total=1, stable_content_pass=False,
                       stable_complete_record_count=3, stable_unique_record_count=3,
                       stable_structure_pass=True, forbidden_hits=[], stable_blocked_page=True,
                       replacement_character_count=20, text_encoding_corruption=True)
        _, _, checks, _ = _list_example(case, {'metrics': metrics}, output)
        self.assertNotIn('排除错误页', ''.join(checks))
        self.assertIn('20 个替换字符', checks[-1])
        self.assertIn('未通过', checks[-1])

    def test_dynamic_example_shows_reference_and_returned_shell_without_inventing_content(self):
        reference = 'A fixed quote\nby Example Author\nTags: example'
        output = '# Quotes to Scrape\n[Login](/login)'
        case = {'ground_truth_main_content': reference, 'forbidden_any': ['CAPTCHA'],
                'acceptance_contract': {'minimum_records': 10, 'stable_text_anchors': [
                    {'alternatives': [reference]}]}}
        metrics = dict(content_chars=len(output), stable_complete_record_count=0,
                       stable_unique_record_count=0, stable_structure_pass=False,
                       stable_anchor_hits=0, stable_anchor_total=10, stable_content_pass=False,
                       forbidden_hits=[], stable_blocked_page=False,
                       replacement_character_count=0, text_encoding_corruption=False,
                       fact_results=[{'alternatives': [s], 'matched': [], 'pass': False}
                                     for s in ['A fixed quote', 'Example Author', 'example']])
        left, right, checks, _ = _dynamic_example(case, {'metrics': metrics, 'elapsed_ms': 5000}, output)
        self.assertIn(reference, left)
        self.assertIn(output, right)
        self.assertNotIn('A fixed quote', right)
        self.assertIn('0/10', checks[4])
        self.assertIn('未通过', checks[3])
        self.assertIn('未通过', checks[4])

    def test_video_example_preserves_relative_link_and_shows_resolved_target(self):
        target = 'https://example.com/rabbit320.webm'
        output = "Your browser doesn't support HTML5 video. [video](rabbit320.webm)"
        case = {'expected_resources': [{'urls': [target]}]}
        metrics = {'resource_candidate_urls': [target], 'resource_results': [
            {'matched_urls': [target], 'pass': True}]}
        left, right, checks, _ = _resource_example(case, {'metrics': metrics}, output)
        self.assertIn(target, left)
        self.assertIn('support HTML5 video', right)
        self.assertIn('(\u003cmark\u003erabbit320.webm\u003c/mark\u003e)', right)
        self.assertIn(target, right)
        self.assertIn('通过', checks[0])

    def test_cases_reject_duplicate_track_coverage(self):
        cases = {cid: {'track': 'web'} for cid, _ in CASE_TABS}
        with self.assertRaisesRegex(ValueError, 'each benchmark track exactly once'):
            render_article_cases('.', cases, [])


if __name__ == '__main__':
    unittest.main()
