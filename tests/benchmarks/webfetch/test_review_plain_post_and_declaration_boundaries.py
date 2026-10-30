"""Plain post headers require record boundaries; opaque declarations own no records."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_posts import _text_records
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.article_contract_fixtures import post_labels


POST = {**post_labels(), 'version': 2, 'hosts': ['example.test'], 'entry_path': r'/p/\d+',
        'html_record_selector': 'article.post', 'html_permalink_selector': 'a.time',
        'html_author_selector': '.author', 'html_body_selector': '.body',
        'minimum_posts': 2, 'allow_empty_outer_with_quotes': True}
POST_CASE = {'url': 'https://example.test/feed', 'expected_any': [],
             'source_benchmark': {'post_contract': POST}}
LIST_CASE = {'url': 'https://shop.example/catalog', 'expected_any': [],
             'acceptance_contract': {'version': 1, 'schema': 'named_link', 'hosts': ['shop.example'],
                                     'entry_path': r'/item/\d+', 'minimum_records': 1,
                                     'stable_text_anchors': []}}


def post_answer(boundary, separator='\n', destination='https://example.test/p/2'):
    return separator.join(['Alice', 'https://example.test/p/1', 'Actual first body.',
                           *([''] if boundary else []), 'See this earlier post', destination,
                           'Further discussion.'])


@pytest.mark.parametrize('separator', ['\n', '\r', '\r\n', '\u2028'])
@pytest.mark.parametrize('boundary', [False, True])
def test_valid_permalink_only_starts_a_subsequent_plain_post_at_a_paragraph(boundary, separator):
    source = post_answer(boundary, separator)
    metrics = score_attempt(POST_CASE, source.encode(), ok=True, metadata={'answer_format': 'plain_text'})
    assert task_passed(True, metrics) is boundary
    assert metrics['article_post_confirmed_count'] == (2 if boundary else 1)
    rows = _text_records(source, POST, POST_CASE['url'], representation='plain_text')
    if not boundary:
        assert rows[0]['post'] == ('Actual first body.\nSee this earlier post\n'
                                   'https://example.test/p/2\nFurther discussion.')


@pytest.mark.parametrize('destination', ['https://wrong.test/p/2', 'https://example.test/docs/2'])
@pytest.mark.parametrize('boundary', [False, True])
def test_invalid_and_non_post_references_cannot_supply_a_second_post(destination, boundary):
    source = post_answer(boundary, destination=destination)
    metrics = score_attempt(POST_CASE, source.encode(), ok=True, metadata={'answer_format': 'plain_text'})
    assert not task_passed(True, metrics)
    assert metrics['article_post_confirmed_count'] == 1


@pytest.mark.parametrize('representation', ['markdown', 'html'])
def test_explicit_markdown_and_native_html_record_boundaries_still_count(representation):
    if representation == 'markdown':
        source = '## [Alice](https://example.test/p/1)\nFirst body.\n## [Bob](https://example.test/p/2)\nSecond body.'
    else:
        source = ''.join('<article class="post"><b class="author">' + name + '</b><a class="time" href="/p/' + str(i) + '">Today</a><p class="body">Actual body.</p></article>'
                         for i, name in [(1, 'Alice'), (2, 'Bob')])
    metrics = score_attempt(POST_CASE, source.encode(), ok=True, metadata={'answer_format': representation})
    assert task_passed(True, metrics)


@pytest.mark.parametrize('declaration', ['<![bogus]>', '<![UNRECOGNIZED[opaque]]>', '<![%draft]>'])
@pytest.mark.parametrize('representation', [None, 'markdown'])
@pytest.mark.parametrize('sibling', [False, True])
def test_declarations_without_html_tags_do_not_hide_valid_markdown_siblings(declaration, representation, sibling):
    source = declaration + ('\n[Valid](/item/2)' if sibling else '')
    metadata = {} if representation is None else {'answer_format': representation}
    metrics = score_attempt(LIST_CASE, source.encode(), ok=True, metadata=metadata)
    assert task_passed(True, metrics) is sibling
    assert metrics['stable_complete_record_count'] == int(sibling)


@pytest.mark.parametrize('wrapper', ['`{}`', '```\n{}\n```', '<span title="{}">Note</span>'])
def test_declaration_literals_and_attributes_do_not_erase_a_visible_sibling(wrapper):
    source = wrapper.format('<![bogus]>') + '\n[Valid](/item/2)'
    metrics = score_attempt(LIST_CASE, source.encode(), ok=True, metadata={'answer_format': 'markdown'})
    assert task_passed(True, metrics)
    assert metrics['stable_complete_record_count'] == 1


def controls():
    rows = []
    for boundary in (False, True):
        rows.append({'id': 'plain-post-valid-reference-' + ('paragraph' if boundary else 'body-continuation'),
                     'category': 'structured-extraction',
                     'root_cause': 'Subsequent plain post permalinks were promoted from body reference lines without a record boundary.',
                     'failure_mode': 'false-negative' if boundary else 'false-positive',
                     'case': deepcopy(POST_CASE), 'artifact': post_answer(boundary), 'ok': True,
                     'metadata': {'answer_format': 'plain_text'}, 'expected_task_pass': boundary})
    for sibling in (False, True):
        rows.append({'id': 'unsupported-declaration-' + ('markdown-sibling' if sibling else 'only'),
                     'category': 'answer-surface',
                     'root_cause': 'The no-tag visibility fast path returned unsupported marked declarations before the existing declaration lexer ran.',
                     'failure_mode': 'false-negative' if sibling else 'false-positive',
                     'case': deepcopy(LIST_CASE), 'artifact': '<![bogus]>' + ('\n[Valid](/item/2)' if sibling else ''),
                     'ok': True, 'metadata': {'answer_format': 'markdown'}, 'expected_task_pass': sibling})
    return rows


CONTROLS = controls()
