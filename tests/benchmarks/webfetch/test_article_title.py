"""The title is an independent component, not a substring of article prose."""
import unittest

from lexbench_webfetch.article_title import validate_title_contract, verify_article_title


class ArticleTitleTests(unittest.TestCase):
    def setUp(self):
        self.contract = {'version': 1, 'alternatives': ['Why We Read'],
                         'html_selector': 'h1.article-title'}

    def passes(self, content):
        return verify_article_title(content, self.contract)['article_title_pass']

    def test_plain_and_markdown_titles_pass(self):
        for text in ['Why We Read', '# Why We Read', '## **Why We Read** ##',
                     '[Why We Read](https://example.test/story)',
                     '# [Why We Read](https://example.test/story)',
                     'Why We Read\n==========\n\nThe opening paragraph.',
                     '# Why <em>We</em> Read\n\nThe opening paragraph.']:
            with self.subTest(text=text):
                self.assertTrue(self.passes(text))

    def test_body_mentions_wrong_title_and_quoted_snippets_fail(self):
        for text in ['The essay Why We Read explains the value of books.',
                     '# Another Article\nWhy We Read explains the value of books.',
                     '> Why We Read', '- Why We Read', 'Prefix Why We Read',
                     'Why We Read suffix', '```text\nWhy We Read\n```',
                     '~~~\nWhy We Read\n~~~']:
            with self.subTest(text=text):
                self.assertFalse(self.passes(text))

    def test_selected_visible_html_heading_passes(self):
        self.assertTrue(self.passes('<article><h1 class="article-title">Why <em>We</em> Read</h1>'
                                    '<p>The complete prose follows.</p></article>'))
        self.assertTrue(self.passes('<h1 class="article-title" aria-hidden="true">Why We Read</h1>'))

    def test_html_prose_and_unselected_heading_cannot_supply_title(self):
        for text in ['<article><p>Why We Read</p></article>',
                     '<article><h1>Why We Read</h1></article>',
                     '<h1 class="article-title">Another Article</h1><p>Why We Read</p>',
                     '<h1 class="article-title">Why We Read extra</h1>',
                     '<html><head><title>Why We Read</title></head><body>Prose</body></html>']:
            with self.subTest(text=text):
                self.assertFalse(self.passes(text))

    def test_hidden_html_metadata_and_image_alt_cannot_supply_title(self):
        for text in ['<h1 class="article-title" hidden>Why We Read</h1>',
                     '<div style="display:none"><h1 class="article-title">Why We Read</h1></div>',
                     '<h1 class="article-title" style="visibility:hidden">Why We Read</h1>',
                     '<h1 class="article-title" style="opacity:0">Why We Read</h1>',
                     '<h1 class="article-title"><img alt="Why We Read"></h1>',
                     '<h1 class="article-title"><script>Why We Read</script></h1>',
                     '# <span hidden>Why We Read</span>',
                     '# Header\n<span hidden>\nWhy We Read\n</span>',
                     '![Why We Read](https://example.test/image.png)',
                     '[![Why We Read](https://example.test/image.png)](https://example.test/story)']:
            with self.subTest(text=text):
                self.assertFalse(self.passes(text))

    def test_closed_details_title_is_hidden_but_summary_and_open_content_are_visible(self):
        self.assertFalse(self.passes('<article><details><summary>More</summary>'
                                     '<h1 class="article-title">Why We Read</h1>'
                                     '</details></article>'))
        self.assertTrue(self.passes('<article><details open><summary>More</summary>'
                                    '<h1 class="article-title">Why We Read</h1>'
                                    '</details></article>'))
        self.assertFalse(self.passes('# Other title\n<details><summary>More</summary>\n'
                                     'Why We Read\n</details>'))
        contract = {**self.contract, 'html_selector': 'details > summary'}
        self.assertTrue(verify_article_title(
            '<details><summary>Why We Read</summary><p>Hidden</p></details>', contract
        )['article_title_pass'])

    def test_reviewed_css_hidden_duplicate_cannot_supply_title(self):
        contract = {**self.contract, 'html_selector': '.text-title h1,h1,h2,[role="heading"]',
                    'excluded_html_selectors': ['h1.screen-reader-only']}
        hidden = '<h1 class="screen-reader-only">Why We Read</h1>'
        self.assertFalse(verify_article_title(hidden, contract)['article_title_pass'])
        self.assertTrue(verify_article_title(
            hidden + '<div class="text-title"><h1>Why We Read</h1></div>', contract
        )['article_title_pass'])
        self.assertTrue(verify_article_title('<h1>Why We Read</h1>', contract)['article_title_pass'])
        self.assertFalse(verify_article_title(
            hidden + '<p>Why We Read is mentioned in the original body.</p>', contract
        )['article_title_pass'])
        for exclusions in ['h1', [None], [''], ['['], True]:
            with self.subTest(exclusions=exclusions):
                with self.assertRaises(ValueError):
                    validate_title_contract({**self.contract,
                                             'excluded_html_selectors': exclusions})

    def test_alternative_and_whitespace_normalization(self):
        contract = {**self.contract, 'alternatives': ['Why We Read', '为何阅读']}
        self.assertTrue(verify_article_title('# 为何阅读', contract)['article_title_pass'])
        self.assertTrue(self.passes('  WHY   WE READ  '))
        chinese = {**self.contract, 'alternatives': ['从产业细微处 ， 看见周期起落']}
        self.assertTrue(verify_article_title('从产业细微处，看见周期起落', chinese)['article_title_pass'])

    def test_contract_rejects_invalid_inputs(self):
        for changes in [{'version': True}, {'version': 2}, {'alternatives': []},
                        {'alternatives': ['']}, {'alternatives': ['Why\nRead']},
                        {'alternatives': [None]}, {'html_selector': ''},
                        {'html_selector': '['}, {'unknown': 1}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    validate_title_contract({**self.contract, **changes})
        with self.assertRaises(ValueError):
            validate_title_contract(None)
        with self.assertRaises(ValueError):
            verify_article_title(b'Why We Read', self.contract)
