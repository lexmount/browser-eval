import subprocess
import sys
import unittest
from lexbench_webfetch.reference_quality import audit_reference, clean_reference_text, blocked_page_hits, markdown_link_destinations, markdown_links, reference_anchor_in_text, script_payload_shell, visible_markdown_links
from lexbench_webfetch.metrics import normalize_for_match
from harness.web_fetch.models import execution_id


class ReferenceQualityTests(unittest.TestCase):
    def test_unmatched_markdown_brackets_preserve_body_scoring_within_budget(self):
        code = """
from lexbench_webfetch.scoring import score_attempt
case = {'id': 'body', 'url': 'https://example.test/', 'track': 'web',
        'acceptance_contract': {'version': 1, 'schema': 'body_text',
            'minimum_records': 0, 'stable_text_anchors': [
                {'id': 'body', 'alternatives': ['Source prose']}]}}
for brackets in ('[' * 20000, '[x](' * 5000):
    for present in (True, False):
        text = ('Source prose' if present else 'Unrelated prose') + brackets
        result = score_attempt(case, text.encode(), ok=True)
        assert result['content_pass'] is present
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nested_and_escaped_link_delimiters_retain_existing_destinations(self):
        samples = [
            (r'[Docs \[with note](/guide)', r'Docs \[with note', '/guide'),
            ('[Docs [nested]](/guide(a))', 'Docs [nested]', '/guide(a)'),
            (r'[Docs](/guide\))', 'Docs', '/guide)'),
        ]
        for text, label, destination in samples:
            with self.subTest(text=text):
                self.assertEqual([(link.label, link.destination)
                                  for link in markdown_links(text)],
                                 [(label, destination)])
        self.assertEqual(markdown_links(r'\[Docs](/guide)'), [])
        self.assertIn('/guide', visible_markdown_links(r'\[Docs](/guide)'))

    def test_script_payload_shell_rejects_state_dump_but_not_readable_js_article(self):
        state = '\n'.join(
            f'window.__STATE{i}__ = {{"article":"Required readable sentence {i}",'
            + '"payload":"' + ('x' * 900) + '"};'
            for i in range(12)
        )
        # One dominant minified line is the actual failure shape.
        state = 'Page title\n' + state.replace('\n', '') + '\n' + '\n'.join(
            f'//# sourceURL=/bundle-{i}.js' for i in range(12))
        self.assertTrue(script_payload_shell(state))

        article = ('# Reading JavaScript application state\n' +
                   ('This article explains why readable prose remains the task output.\n' * 120) +
                   '```js\nwindow.__STATE__ = {article: "example"};\n```\n')
        self.assertFalse(script_payload_shell(article))
    def test_blocked_page_heading_accepts_markdown_emphasis_without_prose_false_positive(self):
        self.assertEqual(blocked_page_hits('**Error: Forbidden / 403**'),
                         ['**Error: Forbidden / 403**'])
        self.assertEqual(blocked_page_hits(
            'This article explains an Error: Forbidden / 403 response.'), [])
        variants = [
            '# Testing to determine if you are a bot!',
            '# Checking if you are not a bot',
            '# Checking you are not a bot',
            '# Verifying visitor request...',
            '# Pardon Our Interruption',
            '[![openSUSE](favicon.svg) Request on hold](/)',
            '# This is a measure to protect this website from bots',
        ]
        for value in variants:
            with self.subTest(value=value):
                self.assertTrue(blocked_page_hits(value))
        self.assertEqual(blocked_page_hits(
            'The incident report says the request was put on hold.'), [])
    def test_inline_breadcrumb_spacing_is_formatting_not_missing_prose(self):
        variants = ['Settings > AI > Usage', 'Settings>AI>Usage', 'Settings\n>\nAI\n>\nUsage',
                    '[Settings](https://example.com/settings)>AI > Usage']
        self.assertEqual(len({normalize_for_match(value) for value in variants}), 1)
        self.assertEqual(normalize_for_match('管理后台 > AI 使用管理 > AI 使用数据'),
                         normalize_for_match('[管理后台](https://example.com)>AI 使用管理>AI 使用数据'))
    def test_visible_text_normalization_handles_emphasis(self):
        self.assertEqual(
            normalize_for_match('the official resource for the *[Team Fortress](/wiki/tf)* series.'),
            normalize_for_match('the official resource for the Team Fortress series.'),
        )
    def test_balanced_multiline_nested_markdown_separates_image_metadata(self):
        value=('Badge [\n![\ncode style: prettier\n]\n(\nhttps://img.example/badge.svg\n)\n]\n'
               '(\nhttps://example.com/project\n) Contributing')
        visible=visible_markdown_links(value)
        self.assertNotIn('code style: prettier',visible)
        self.assertIn('code style: prettier',visible_markdown_links(value, include_image_alt=True))
        self.assertIn('Contributing',visible)
        self.assertNotIn('img.example',visible)
        self.assertNotIn('example.com/project',visible)
        titled=markdown_links('[Docs](https://example.com/a?x=1 "Reference title")')
        self.assertEqual(titled[0].destination,'https://example.com/a?x=1')
        self.assertEqual(markdown_links('['+'x'*9000),[])
        self.assertEqual(markdown_link_destinations(
            '[![Moon](//cdn.example/moon(large).jpg)](/file/moon)'),
            ['/file/moon', '//cdn.example/moon(large).jpg'])

    def test_escaped_literal_bang_before_link_is_not_an_image(self):
        odd = r'OpenWrt wants you\![Join the project](#join)'
        even = r'OpenWrt wants you\\![Join the project](avatar.png)'

        odd_links = markdown_links(odd)
        self.assertEqual([(link.label, link.is_image) for link in odd_links],
                         [('Join the project', False)])
        self.assertEqual(normalize_for_match(visible_markdown_links(odd)),
                         normalize_for_match('OpenWrt wants you! Join the project'))

        even_links = markdown_links(even)
        self.assertEqual([(link.label, link.is_image) for link in even_links],
                         [('Join the project', True)])
    def test_relative_path_autolinks_are_visible_but_html_closing_tags_are_not_rewritten(self):
        value = ('Questions: </help/start>, </help/advanced>, or '
                 '[Support](https://support.example.test/). </div>')
        visible = visible_markdown_links(value)
        self.assertEqual(visible,
                         'Questions: /help/start, /help/advanced, or Support. </div>')
        self.assertEqual(normalize_for_match(value),
                         normalize_for_match(
                             'Questions: /help/start, /help/advanced, or Support. </div>'))
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
    def test_reference_anchor_preserves_punctuation_but_accepts_capture_layout(self):
        reference = 'Python\nis a\nhigh-level\n,\ngeneral-purpose programming language'
        self.assertTrue(reference_anchor_in_text(
            reference, 'Python is a high-level, general-purpose programming language'))
        self.assertFalse(reference_anchor_in_text(
            reference, 'Python is a high-level general-purpose programming language'))
        facts = [{'id':'lead','alternatives':[
            'Python is a high-level general-purpose programming language']}]
        self.assertIn('fact_absent_from_reference:lead',
                      audit_reference(reference,facts,normalize=normalize_for_match))
    def test_run_identifier_is_stable_and_distinguishes_retests(self):
        r={'attempt_id':'provider__case__r001','started_at':'2026-09-02T01:00:00Z'}
        self.assertEqual(execution_id(r),execution_id(dict(r)))
        self.assertNotEqual(execution_id(r),execution_id({**r,'started_at':'2026-09-02T02:00:00Z'}))
