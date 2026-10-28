"""Fixed post coverage does not require historical content or a run reference."""
from .article_contract_fixtures import post_labels
from copy import deepcopy
import unittest

from lexbench_webfetch.article_posts import validate_post_contract, verify_article_posts


class FixedPostTests(unittest.TestCase):
    def setUp(self):
        self.contract = {**post_labels(),
            'version': 2, 'hosts': ['example.com'], 'entry_path': r'/post/\d+',
            'html_record_selector': 'article.post', 'html_permalink_selector': 'a.time',
            'html_author_selector': '.author', 'html_body_selector': '.body',
            'html_quote_selector': 'blockquote', 'quote_markers': ['Quote', '引用'],
            'minimum_posts': 2, 'allow_empty_outer_with_quotes': True,
        }

    def score(self, content):
        return verify_article_posts(content, self.contract, url='https://example.com/feed')

    def html(self, number=1, author='New author', text='New content', quote=''):
        return (f'<article class="post"><span class="author">{author}</span>'
                f'<a class="time" href="/post/{number}">Today</a>'
                f'<div class="body">{text}</div>{quote}</article>')

    def md(self, number=1, author='New author', text='New content'):
        return f'## {author} [Today](https://example.com/post/{number})\n\n{text}\n\n'

    def test_changed_posts_and_exact_floor(self):
        for render in (self.html, self.md):
            result = self.score(render(900, 'Different author', 'Different text') + render(901))
            self.assertTrue(result['article_posts_pass'], result)
            self.assertNotIn('article_reference_status', result)
            self.assertEqual(result['article_post_confirmed_count'], 2)
            self.assertFalse(self.score(render(900))['article_posts_pass'])

    def test_invalid_records_do_not_count(self):
        self.contract['minimum_posts'] = 1
        for bad in (self.html(author=''), self.html(text=''),
                    self.html().replace('/post/1', 'https://wrong.example/post/1'),
                    self.html().replace('/post/1', '/profile/1'),
                    self.html().replace('/post/1', 'https://user@example.com/post/1')):
            with self.subTest(bad=bad):
                self.assertFalse(self.score(bad)['article_posts_pass'])
                self.assertTrue(self.score(bad + self.html(2))['article_posts_pass'])

    def test_permalink_label_is_not_an_author(self):
        self.contract['minimum_posts'] = 1
        for label in ('Today', 'Permalink', '2026-10-20'):
            self.assertFalse(self.score(f'## [{label}](https://example.com/post/1)\n\nBody')
                             ['article_posts_pass'])
        self.assertTrue(self.score('## [Alice](https://example.com/post/1)\n\nBody')
                        ['article_posts_pass'])

    def test_duplicate_permalink(self):
        for render in (self.html, self.md):
            self.assertFalse(self.score(render() + render(text='Different words'))['article_posts_pass'])

    def test_hidden_and_closed_disclosure(self):
        self.contract['minimum_posts'] = 1
        for wrapper in ('<div hidden>{}</div>', '<div style="display:none">{}</div>',
                        '<details><summary>More</summary>{}</details>',
                        '<details><summary>More</summary><details open>{}</details></details>'):
            self.assertFalse(self.score(wrapper.format(self.html()))['article_posts_pass'])
        self.assertTrue(self.score('<details open>' + self.html() + '</details>')['article_posts_pass'])

    def test_quote_only_needs_attributed_nonempty_text(self):
        self.contract['minimum_posts'] = 1
        good = '<blockquote><span class="author">Original author</span>Original text</blockquote>'
        for body in (self.html(text='', quote=good),
                     self.html(text='', quote=good).replace('<div class="body"></div>', ''),
                     self.md(text='> Original author\n> Original text')):
            self.assertTrue(self.score(body)['article_posts_pass'], self.score(body))
        for quote in ('<blockquote>Unattributed text</blockquote>',
                      '<blockquote><span class="author">Only author</span></blockquote>'):
            self.assertFalse(self.score(self.html(text='', quote=quote))['article_posts_pass'])
        self.assertFalse(self.score(self.md(text='> Only author'))['article_posts_pass'])
        self.contract['allow_empty_outer_with_quotes'] = False
        self.assertFalse(self.score(self.html(text='', quote=good))['article_posts_pass'])

    def test_metadata_only_bodies_and_quotes_do_not_count(self):
        self.contract['minimum_posts'] = 1
        metadata = ('<span class="author">Original author</span>'
                    '<time>2026-10-20</time><a class="time" href="/post/8">Today</a>'
                    '<button>Reply</button><span role="button">Like</span>')
        self.assertFalse(self.score(self.html(text='', quote='<blockquote>' + metadata
                                              + '</blockquote>'))['article_posts_pass'])
        # A real declared body is accepted despite neighboring controls.
        self.assertTrue(self.score(self.html(text='', quote='<blockquote>' + metadata
                                             + '<div class="body">Real quote words</div>'
                                             + '</blockquote>'))['article_posts_pass'])
        for metadata_text in ('2026-10-20\nReply\nLike', '2 hours ago\n3 Likes | Reply',
                              '今天\n回复 | 点赞', '2026-10-20T12:00:00Z'):
            self.assertFalse(self.score(self.md(text=metadata_text))['article_posts_pass'])
            self.assertFalse(self.score(self.md(text='> Original author\n> '
                                                + metadata_text.replace('\n', '\n> ')))
                             ['article_posts_pass'])
        self.assertTrue(self.score(self.md(text='I like the reply about 2026-10-20.'))
                        ['article_posts_pass'])

    def test_nested_cards_cannot_supply_outer_fields(self):
        outer = '<article class="post"><span class="author">Outer</span><a class="time" href="/post/9">Date</a><div class="body">' + self.html() + '</div></article>'
        result = self.score(outer)
        self.assertFalse(result['article_posts_pass'])
        self.assertEqual(result['article_post_confirmed_count'], 1)

    def test_contract_bounds_and_version_fields(self):
        validate_post_contract(self.contract)
        for value in (0, -1, True, 1.5, None):
            contract = {**post_labels(), **self.contract, 'minimum_posts': value}
            with self.assertRaises(ValueError):
                validate_post_contract(contract)
        for key in ('required_ratio', 'max_reference_age_seconds'):
            with self.assertRaises(ValueError):
                validate_post_contract({**self.contract, key: 1})
        legacy = deepcopy(self.contract)
        legacy.update(version=1, required_ratio=1, max_reference_age_seconds=900)
        with self.assertRaises(ValueError):
            validate_post_contract(legacy)
