"""Quote ancestry is bounded by each post card, including nested cards."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_posts import verify_article_posts
from lexbench_webfetch.evaluation import evaluate_response

from .test_review_migration_articles import POSTS, URL, public_case, scored_content


def controls():
    for wrapper in ("bare", "outer-quote", "nested-post"):
        for allow in (False, True):
            for attributed in (False, True):
                for original in (False, True):
                    quote = ('<blockquote>' + ('<span class="author">Quoted author</span>' if attributed else '')
                             + '<p>Actual quoted prose.</p></blockquote>')
                    post = ('<article class="post"><span class="author">Alice</span>'
                            '<a class="permalink" href="/post/1">Today</a><div class="body">'
                            + ('Actual original prose.' if original else '') + quote + '</div></article>')
                    contract = {**POSTS, 'allow_empty_outer_with_quotes': allow}
                    if wrapper == "outer-quote":
                        post = '<blockquote>' + post + '</blockquote>'
                    elif wrapper == "nested-post":
                        contract['minimum_posts'] = 2
                        post = ('<article class="post"><span class="author">Outer author</span>'
                                '<a class="permalink" href="/post/2">Today</a>'
                                '<div class="body">Outer original prose.</div><blockquote>'
                                + post + '</blockquote></article>')
                    yield (f"post-quote-owner-{wrapper}-{'allow' if allow else 'require-original'}-"
                           f"{'attributed' if attributed else 'unattributed'}-{'original' if original else 'quote-only'}",
                           post, contract, original or allow and attributed)
    for allow in (False, True):
        contract = {**POSTS, 'allow_empty_outer_with_quotes': allow, 'html_quote_selector': '.quoted'}
        content = ('<div class="quoted"><article class="post quoted"><span class="author">Alice</span>'
                   '<a class="permalink" href="/post/1">Today</a><div class="body"><div class="quoted">'
                   '<span class="author">Quoted author</span><p>Actual quoted prose.</p>'
                   '</div></div></article></div>')
        yield (f"post-quote-owner-custom-selector-{'allow' if allow else 'require-original'}",
               content, contract, allow)


@pytest.mark.parametrize("identifier, content, contract, expected", list(controls()))
def test_each_post_owns_its_quote_ancestors(identifier, content, contract, expected):
    result = verify_article_posts(content, contract, url=URL)
    assert result['article_posts_pass'] is expected
    public = evaluate_response(public_case('post_contract', deepcopy(contract)), scored_content(content).encode())
    assert public['measured']
    assert public['passed'] is expected


def scoring_controls():
    selected = {
        "post-quote-owner-outer-quote-require-original-unattributed-quote-only",
        "post-quote-owner-outer-quote-require-original-unattributed-original",
        "post-quote-owner-outer-quote-allow-attributed-quote-only",
        "post-quote-owner-outer-quote-require-original-attributed-quote-only",
        "post-quote-owner-nested-post-require-original-unattributed-quote-only",
        "post-quote-owner-nested-post-allow-attributed-quote-only",
    }
    for identifier, content, contract, expected in controls():
        if identifier not in selected:
            continue
        yield {
            'id': 'article-' + identifier,
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'category': 'answer-surface',
            'root_cause': 'Quote ancestry must stop at the owning post card; outer containers cannot relabel quoted prose as original.',
            'case': public_case('post_contract', contract), 'artifact': scored_content(content),
            'ok': True, 'expected_task_pass': expected,
        }
