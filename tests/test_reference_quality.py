import unittest
from harness.web_fetch.reference_quality import (audit_reference, clean_reference_text,
    markdown_link_destinations, markdown_links, visible_markdown_links)
from harness.web_fetch.metrics import normalize_for_match
from harness.web_fetch.models import execution_id


class ReferenceQualityTests(unittest.TestCase):
    def test_inline_breadcrumb_spacing_is_formatting_not_missing_prose(self):
        variants = ['Settings > AI > Usage', 'Settings>AI>Usage', 'Settings\n>\nAI\n>\nUsage',
                    '[Settings](https://example.com/settings)>AI > Usage']
        self.assertEqual(len({normalize_for_match(value) for value in variants}), 1)
        self.assertEqual(normalize_for_match('管理后台 > AI 使用管理 > AI 使用数据'),
                         normalize_for_match('[管理后台](https://example.com)>AI 使用管理>AI 使用数据'))
    def test_balanced_multiline_nested_markdown_keeps_labels_not_destinations(self):
        value=('Badge [\n![\ncode style: prettier\n]\n(\nhttps://img.example/badge.svg\n)\n]\n'
               '(\nhttps://example.com/project\n) Contributing')
        visible=visible_markdown_links(value)
        self.assertIn('code style: prettier',visible)
        self.assertIn('Contributing',visible)
        self.assertNotIn('img.example',visible)
        self.assertNotIn('example.com/project',visible)
        titled=markdown_links('[Docs](https://example.com/a?x=1 "Reference title")')
        self.assertEqual(titled[0].destination,'https://example.com/a?x=1')
        self.assertEqual(markdown_links('['+'x'*9000),[])
        self.assertEqual(markdown_link_destinations(
            '[![Moon](//cdn.example/moon(large).jpg)](/file/moon)'),
            ['/file/moon', '//cdn.example/moon(large).jpg'])
    def test_breadcrumb_normalization_preserves_semantic_differences(self):
        for left, right in [('Settings > AI', 'Settings AI'), ('x > 10', 'x < 10'),
                            ('x > 10', 'x >= 10'), ('x > 10', 'x > 100'),
                            ('x > -10', 'x > 10'), ('a b', 'ab')]:
            self.assertNotEqual(normalize_for_match(left), normalize_for_match(right))
        self.assertNotEqual(normalize_for_match('first\n> second'), normalize_for_match('first second'))
    def test_cleaning_is_narrow_and_idempotent(self):
        raw='不通过 -10.5% C++ a\u200db\n正文\u200b\ufeff'
        clean=clean_reference_text(raw)
        self.assertEqual(clean,'不通过 -10.5% C++ a\u200db\n正文')
        self.assertEqual(clean_reference_text(clean),clean)
    def test_quality_gates(self):
        facts=[{'id':'a','alternatives':['absent']},{'id':'b','alternatives':['absent']}]
        issues=audit_reference('bad\ufffd\x00',facts,normalize=normalize_for_match)
        for expected in ['replacement_character','unexpected_control_character','fact_absent_from_reference:a','duplicate_fact:b']:
            self.assertIn(expected,issues)
        structured=[{'id':'entry-1-method','alternatives':['GET']},
                    {'id':'entry-2-method','alternatives':['GET']}]
        self.assertNotIn('duplicate_fact:entry-2-method',
                         audit_reference('GET',structured,normalize=normalize_for_match))
    def test_short_valid_reference_is_not_rejected(self):
        self.assertEqual(audit_reference('Dummy PDF file',[{'id':'title','alternatives':['Dummy PDF file']}],normalize=normalize_for_match),[])
    def test_run_identifier_is_stable_and_distinguishes_retests(self):
        r={'attempt_id':'provider__case__r001','started_at':'2026-09-02T01:00:00Z'}
        self.assertEqual(execution_id(r),execution_id(dict(r)))
        self.assertNotEqual(execution_id(r),execution_id({**r,'started_at':'2026-09-02T02:00:00Z'}))
