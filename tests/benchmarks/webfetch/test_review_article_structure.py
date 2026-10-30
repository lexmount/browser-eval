"""Comment heading hierarchy and post UI metadata preserve record ownership."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import verify_article_comments
from lexbench_webfetch.article_posts import verify_article_posts
from lexbench_webfetch.evaluation import evaluate_response

from .test_review_migration_articles import COMMENTS, POSTS, URL, public_case, scored_content


def controls():
    for representation in ('markdown', 'html'):
        for deeper in (False, True):
            for first_level in (3, 4):
                second_level = first_level + int(deeper)
                if representation == 'markdown':
                    content = ('## Comments\n' + '#' * first_level + ' Alice\nFirst reply.\n'
                               + '#' * second_level + ' Background\nMore details.')
                else:
                    content = (f'<h2>Comments</h2><h{first_level}>Alice</h{first_level}><p>First reply.</p>'
                               f'<h{second_level}>Background</h{second_level}><p>More details.</p>')
                yield (f'comment-heading-depth-{representation}-{first_level}-'
                       + ('deeper' if deeper else 'peer'), content,
                       'comment_contract', {**COMMENTS, 'minimum_comments': 2}, not deeper,
                       'Deeper headings belong to the current comment; peer author headings establish separate comments.')
        content = ('## Comments\n### Alice\n#### Comments\nActual reply.' if representation == 'markdown'
                   else '<h2>Comments</h2><h3>Alice</h3><h4>Comments</h4><p>Actual reply.</p>')
        yield (f'comment-deeper-marker-is-body-{representation}', content,
               'comment_contract', COMMENTS, True,
               'A deeper body heading must not reset the current author merely because its text matches a section marker.')
    metadata = {
        'buttons': '<time>2026-10-20</time><button>Reply</button><button>Like</button>',
        'role-buttons': '<time>2026-10-20</time><span role="button">Reply</span><span role="button">Like</span>',
    }
    for representation in ('native', 'cleaned', 'mixed'):
        for name, controls in metadata.items():
            for prose in (False, True):
                body = controls + ('<p>I like the reply about the 2026-10-20 event.</p>' if prose else '')
                if representation == 'native':
                    content = ('<article class="post"><span class="author">Alice</span>'
                               '<a class="permalink" href="/post/1">Today</a><div class="body">'
                               + body + '</div></article>')
                elif representation == 'cleaned':
                    content = '<h2>Alice <a href="/post/1">Today</a></h2><div>' + body + '</div>'
                else:
                    content = '## Alice [Today](/post/1)\n\n<div>' + body + '</div>'
                yield (f'post-metadata-{representation}-{name}-' + ('prose' if prose else 'only'),
                       content, 'post_contract', POSTS, prose,
                       'Post UI timestamps and buttons are excluded before inline text can concatenate into fake prose.')
    for prose in (False, True):
        body = metadata['buttons'] + ('<p>Actual quoted prose.</p>' if prose else '')
        content = ('<article class="post"><span class="author">Alice</span>'
                   '<a class="permalink" href="/post/1">Today</a><div class="body"></div>'
                   '<blockquote><span class="author">Bob</span><div class="body">' + body
                   + '</div></blockquote></article>')
        yield ('post-metadata-quote-' + ('prose' if prose else 'only'), content,
               'post_contract', POSTS, prose,
               'Outer and quoted post bodies use the same UI metadata exclusion before prose validation.')
    for name, literal in (
        ('button', '<button>Reply</button>'),
        ('time', '<time>2026-10-20</time>'),
        ('c-header', '#include <stdio.h>'),
    ):
        for representation in ('markdown', 'native', 'cleaned'):
            if representation == 'markdown':
                content = '## Alice [Today](/post/1)\n\n`' + literal + '`'
            else:
                from html import escape
                code = '<code>' + escape(literal) + '</code>'
                if representation == 'native':
                    content = ('<article class="post"><span class="author">Alice</span>'
                               '<a class="permalink" href="/post/1">Today</a><div class="body">'
                               + code + '</div></article>')
                else:
                    content = '<h2>Alice <a href="/post/1">Today</a></h2><p>' + code + '</p>'
            yield (f'post-literal-ui-{representation}-{name}', content,
                   'post_contract', POSTS, True,
                   'Literal HTML in inline code remains post text rather than becoming UI metadata.')


@pytest.mark.parametrize('identifier, content, component, contract, expected, cause', list(controls()))
def test_heading_hierarchy_and_metadata_are_not_extra_content(identifier, content, component, contract, expected, cause):
    if component == 'comment_contract':
        result, key = verify_article_comments(content, contract), 'article_comments_pass'
        if not expected:
            assert result['article_comment_count'] == 1
    else:
        result, key = verify_article_posts(content, contract, url=URL), 'article_posts_pass'
    assert result[key] is expected
    public = evaluate_response(public_case(component, deepcopy(contract)), scored_content(content).encode())
    assert public['measured']
    assert public['passed'] is expected


def scoring_controls():
    selected = {
        'comment-heading-depth-markdown-3-deeper', 'comment-heading-depth-markdown-3-peer',
        'comment-heading-depth-html-3-deeper', 'comment-heading-depth-html-3-peer',
        'post-metadata-native-buttons-only', 'post-metadata-native-buttons-prose',
        'post-metadata-cleaned-role-buttons-only', 'post-metadata-cleaned-role-buttons-prose',
    }
    for identifier, content, component, contract, expected, cause in controls():
        if identifier not in selected:
            continue
        row = {
            'id': 'article-' + identifier,
            'failure_mode': 'false-negative' if expected else 'false-positive',
            'category': 'answer-surface', 'root_cause': cause,
            'case': public_case(component, contract), 'artifact': scored_content(content),
            'ok': True, 'expected_task_pass': expected,
        }
        if component == 'comment_contract':
            row['expected_metrics'] = {'article_comment_count': 2 if expected else 1}
        yield row
