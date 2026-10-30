"""Cleaned HTML retains semantic boundaries, never competes with native records."""
from .article_contract_fixtures import comment_labels, post_labels
import pytest
from lexbench_webfetch.article_comments import verify_article_comments
from lexbench_webfetch.article_posts import verify_article_posts

COMMENTS = {**comment_labels(), 'version': 2, 'minimum_comments': 2, 'record_selector': '.comment',
            'author_selector': '.author', 'body_selector': '.body', 'section_markers': ['Comments'],
            'text_scope': {'body_end': 'Article ending.', 'end_markers': ['Related articles']}}
POSTS = {**post_labels(), 'version': 2, 'minimum_posts': 2, 'hosts': ['example.test'], 'entry_path': r'/post/\d+',
         'html_record_selector': 'article.post', 'html_permalink_selector': 'a.permalink',
         'html_author_selector': '.author', 'html_body_selector': '.body'}


def comments(text):
    return verify_article_comments(text, COMMENTS)


def posts(text):
    return verify_article_posts(text, POSTS, url='https://example.test/feed')


CLEAN_COMMENTS = '<p>Article ending.</p><h2>Comments</h2><h3>Alice</h3><p>First reply.</p><h3>Bob</h3><p>Second reply.</p>'
CLEAN_POSTS = '<h2>Alice <a href="/post/1">Today</a></h2><p>First post.</p><h2>Bob <a href="/post/2">Today</a></h2><p>Second post.</p>'


def test_cleaned_heading_comment_html_matches_natural_markdown():
    assert comments(CLEAN_COMMENTS)['article_comments_pass']
    assert comments('Article ending.\n## Comments\n### Alice\nFirst reply.\n### Bob\nSecond reply.')['article_comments_pass']


def test_cleaned_linked_post_html_preserves_author_and_destination():
    assert posts(CLEAN_POSTS)['article_posts_pass']


@pytest.mark.parametrize('wrapper', ['<aside>{}</aside>', '<nav>{}</nav>', '<footer>{}</footer>',
                                     '<div hidden>{}</div>', '<details><summary>More</summary>{}</details>'])
def test_cleaned_hidden_and_unrelated_landmarks_cannot_fill_floors(wrapper):
    assert not comments(wrapper.format(CLEAN_COMMENTS))['article_comments_pass']
    assert not posts(wrapper.format(CLEAN_POSTS))['article_posts_pass']


def test_native_malformed_records_cannot_be_overruled_by_cleaned_fallback():
    bad_comment = '<div class="comment"><span class="author">A</span></div>'
    assert not comments(bad_comment + CLEAN_COMMENTS)['article_comments_pass']
    bad_post = '<article class="post"><span class="author">A</span></article>'
    assert not posts(bad_post + CLEAN_POSTS)['article_posts_pass']


def test_cleaned_recommendation_section_does_not_supply_comments_or_posts():
    assert not comments('<p>Article ending.</p><h2>Comments</h2><h3>Alice</h3><p>Real reply.</p>'
                        '<h2>Related articles</h2><h3>Bob</h3><p>Story summary.</p>')['article_comments_pass']
    assert not posts('<h2>Alice <a href="/post/1">Today</a></h2><p>Real post.</p>'
                     '<h2>Related articles</h2>' + CLEAN_POSTS)['article_posts_pass']


def test_missing_post_body_cannot_borrow_following_section_text():
    assert not posts('<h2>Alice <a href="/post/1">Today</a></h2>'
                     '<h2>Related articles</h2><p>Story summary.</p>' + CLEAN_POSTS)['article_posts_pass']


def test_repeated_body_ending_before_explicit_heading_does_not_open_article_body():
    text = ('Article ending.\n### A caption\nImage credit.\n### Another caption\nImage credit.\n'
            'Article ending.\n## Comments')
    assert comments(text)['article_comment_count'] == 0


def test_comment_quoting_body_ending_does_not_move_start_past_real_comments():
    text = ('Article ending.\n## Comments\n### Alice\nFirst reply.\n'
            '### Bob\nI quote: Article ending.')
    assert comments(text)['article_comments_pass']


def test_unlabelled_comment_subheading_collision_is_documented_not_guessed():
    # The same string can mean a second author "Explanation" or a subheading
    # inside Alice's comment. The current text contract cannot disambiguate it.
    text = 'Article ending.\n## Comments\n### Alice\nFirst reply.\n### Explanation\nMore words.'
    assert comments(text)['article_comment_count'] == 2


def test_mixed_author_like_body_link_does_not_inflate_heading_records():
    # This can also be a second linked author. Lacking ownership metadata, the
    # conservative parser keeps it with the established heading-owned comment.
    text = ('Article ending.\n## Comments\n### Alice\nFirst reply.\n\n'
            '[Documentation](https://example.test/docs)\nMore words.')
    assert comments(text)['article_comment_count'] == 1


@pytest.mark.parametrize('literal', ['### Fake author\nNot a second comment.',
                                   '```\n### Fake author\nNot a second comment.\n```'])
def test_cleaned_pre_cannot_create_comment_records(literal):
    html = '<p>Article ending.</p><h2>Comments</h2><h3>Alice</h3><p>Real reply.</p><pre>' + literal + '</pre>'
    assert comments(html)['article_comment_count'] == 1
    assert not comments(html)['article_comments_pass']


def test_cleaned_table_cell_cannot_create_comment_record():
    html = ('<p>Article ending.</p><h2>Comments</h2><h3>Alice</h3><p>Real reply.</p>'
            '<table><tr><td>### Fake author</td></tr><tr><td>Not a comment.</td></tr></table>')
    assert comments(html)['article_comment_count'] == 1


def test_partial_native_fields_cannot_borrow_unowned_cleaned_records():
    html = '<div class="comment"><h3>Alice</h3><p>Unidentified field.</p></div>' + CLEAN_COMMENTS
    assert not comments(html)['article_comments_pass']
